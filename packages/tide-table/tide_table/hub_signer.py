"""Sign calendar commits and judge fetched history through the teammate's Hub.

Git signs each commit by running `small-sea-git-sign`, which asks the Hub to
sign with this berth's key. The commit message carries a work context naming
the team, berth, purpose, and the authority view this device holds when the
commit is made. A fetched history is accepted only if the Hub judges every
commit in it authorized.

The signer talks to the Hub through an async session over an
httpx.AsyncClient the caller owns.
"""

import os
import shutil

from cod_sync.git import signing_git_env
from cod_sync.work_context import WorkContext, commit_message_with_context
from small_sea_client.aio import SmallSeaSession

PURPOSE = "TideTable/calendar"


class UnauthorizedHistory(Exception):
    def __init__(self, head, refusals):
        super().__init__(f"history at {head} has {len(refusals)} commit(s) the Hub did not authorize: "
                         f"{refusals[0]['result']} ({refusals[0]['reason']})")
        self.head = head
        self.refusals = refusals


class HubSigner:
    def __init__(self, session, team_id, berth_id, env):
        """Use HubSigner.open."""
        self.session = session
        self.team_id, self.berth_id = team_id, berth_id
        self.env = env

    @classmethod
    async def open(cls, hub_url, token, http):
        """http: an httpx.AsyncClient whose base_url is hub_url."""
        program = shutil.which("small-sea-git-sign")
        if program is None:
            raise FileNotFoundError("small-sea-git-sign is not on PATH")
        session = SmallSeaSession(http, token)
        info = await session.session_info()
        env = signing_git_env(
            os.environ, program=program, public_key=await session.signing_public_key(),
            extra_env={"SMALL_SEA_HUB_URL": hub_url, "SMALL_SEA_SESSION_TOKEN": token})
        return cls(session, info["team_id"], info["berth_id"], env)

    async def message(self, message):
        authority_view = await self.session.authority_view()
        context = WorkContext("commit", self.team_id, self.berth_id, PURPOSE, authority_view)
        return commit_message_with_context(message, context)

    async def verify_history(self, repo, head):
        result = await self.session.verify_history(repo.git_dir, head)
        refusals = [c for c in result["commits"] if c["result"] != "authorized"]
        if refusals:
            raise UnauthorizedHistory(head, refusals)
