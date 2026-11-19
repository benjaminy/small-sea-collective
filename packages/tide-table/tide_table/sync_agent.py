"""Talk to the Hub promptly; leave merging to the app.

The agent runs two asyncio tasks. One waits for the Hub to report that a
teammate published, fetches that teammate's history into their parked ref
(`refs/peers/<teammate>/main`), and reports it on the `events` queue. The
other publishes local history whenever the app asks.

The agent never merges and never touches the working tree or `main`. The app
takes events from the queue when it is ready and decides whether and when to
merge. A teammate's changes can sit parked for as long as the app likes.

`stop` cancels both tasks and waits for them, so whoever started the agent
can shut it down before the Hub it talks to goes away.
"""

import asyncio
from dataclasses import dataclass

from cod_sync.aio.store import PeerSmallSeaStore, SmallSeaStore
from cod_sync.protocol import NoPublishedHeadError
from small_sea_client.aio import SmallSeaSession

WATCH_TIMEOUT = 30  # seconds the Hub may hold each watch request
RETRY_DELAY = 5


@dataclass
class Parked:
    """A teammate's history was fetched; their parked ref now points at head."""
    teammate_id: str
    head: str


@dataclass
class SyncFailed:
    """Fetching or publishing failed; the agent retries after RETRY_DELAY."""
    what: str
    error: Exception


class SyncAgent:
    def __init__(self, cal, http, token):
        """http: an httpx.AsyncClient whose base_url is the Hub.

        Its timeout must be longer than WATCH_TIMEOUT. The caller closes it
        after stopping the agent.
        """
        self.cal = cal
        self.http = http
        self.token = token
        self.events = asyncio.Queue()
        self.session = SmallSeaSession(http, token)
        self._known = {}   # teammate ID -> publication count already fetched
        self._parked = {}  # teammate ID -> head last reported
        self._publish_wanted = asyncio.Event()
        self._tasks = []

    def start(self):
        self._tasks = [asyncio.create_task(self._watch()), asyncio.create_task(self._publish())]
        self.request_publish()  # in case commits were made while the agent was not running

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def request_publish(self):
        """Ask for a publish. Call it from the agent's event loop."""
        self._publish_wanted.set()

    async def _watch(self):
        while True:
            try:
                for peer in await self.session.session_peers():
                    self._known.setdefault(peer["teammate_id"], 0)
                result = await self.session.watch_notifications(dict(self._known), timeout=WATCH_TIMEOUT)
                for teammate_id, count in result["updated"].items():
                    await self._fetch(teammate_id)
                    self._known[teammate_id] = count
            except Exception as e:
                self.events.put_nowait(SyncFailed("fetch", e))
                await asyncio.sleep(RETRY_DELAY)

    async def _fetch(self, teammate_id):
        store = PeerSmallSeaStore(self.token, teammate_id, self.http)
        try:
            head = await self.cal.fetch(store, teammate_id)
        except NoPublishedHeadError:
            return  # the count rose for another of the teammate's berths; nothing here yet
        if head != self._parked.get(teammate_id):
            self._parked[teammate_id] = head
            self.events.put_nowait(Parked(teammate_id, head))

    async def _publish(self):
        while True:
            await self._publish_wanted.wait()
            self._publish_wanted.clear()
            try:
                if await self.cal.aio_repo.has_commits():
                    await self.cal.publish(SmallSeaStore(self.token, self.http))
            except Exception as e:
                self.events.put_nowait(SyncFailed("publish", e))
                await asyncio.sleep(RETRY_DELAY)
                self._publish_wanted.set()
