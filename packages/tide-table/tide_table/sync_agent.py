"""Talk to the Hub promptly; leave merging to the app.

The agent runs two background threads. One waits for the Hub to report that
a teammate published, fetches that teammate's history into their parked ref
(`refs/peers/<teammate>/main`), and reports it on the `events` queue. The
other publishes local history whenever the app asks.

The agent never merges and never touches the working tree or `main`. The app
takes events from the queue when it is ready and decides whether and when to
merge. A teammate's changes can sit parked for as long as the app likes.
"""

import queue
import threading
import time
from dataclasses import dataclass

import httpx
from cod_sync.protocol import NoPublishedHeadError
from cod_sync.store import PeerSmallSeaStore, SmallSeaStore
from small_sea_client.client import SmallSeaClient, SmallSeaSession

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
    def __init__(self, cal, hub_url, token):
        self.cal = cal
        self.hub_url = hub_url
        self.token = token
        self.events = queue.Queue()
        http = httpx.Client(base_url=hub_url, timeout=WATCH_TIMEOUT + 30)
        self.session = SmallSeaSession(SmallSeaClient(_http_client=http), token)
        self._known = {}   # teammate ID -> publication count already fetched
        self._parked = {}  # teammate ID -> head last reported
        self._publish_wanted = threading.Event()

    def start(self):
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._publish, daemon=True).start()
        self.request_publish()  # in case commits were made while the agent was not running

    def request_publish(self):
        self._publish_wanted.set()

    def _watch(self):
        while True:
            try:
                for peer in self.session.session_peers():
                    self._known.setdefault(peer["teammate_id"], 0)
                updated = self.session.watch_notifications(dict(self._known), timeout=WATCH_TIMEOUT)["updated"]
                for teammate_id, count in updated.items():
                    self._fetch(teammate_id)
                    self._known[teammate_id] = count
            except Exception as e:
                self.events.put(SyncFailed("fetch", e))
                time.sleep(RETRY_DELAY)

    def _fetch(self, teammate_id):
        store = PeerSmallSeaStore(self.token, teammate_id, base_url=self.hub_url)
        try:
            head = self.cal.fetch(store, teammate_id)
        except NoPublishedHeadError:
            return  # the count rose for another of the teammate's berths; nothing here yet
        if head != self._parked.get(teammate_id):
            self._parked[teammate_id] = head
            self.events.put(Parked(teammate_id, head))

    def _publish(self):
        while True:
            self._publish_wanted.wait()
            self._publish_wanted.clear()
            try:
                if self.cal.repo.has_commits():
                    self.cal.publish(SmallSeaStore(self.token, base_url=self.hub_url))
            except Exception as e:
                self.events.put(SyncFailed("publish", e))
                time.sleep(RETRY_DELAY)
                self._publish_wanted.set()
