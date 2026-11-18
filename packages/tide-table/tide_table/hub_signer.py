"""Sign calendar commits and judge fetched history through the teammate's Hub.

Git signs each commit by running `small-sea-git-sign`, which asks the Hub to
sign with this berth's key. The commit message carries a work context naming
the team, berth, purpose, and the authority view this device holds when the
commit is made. A fetched history is accepted only if the Hub judges every
commit in it authorized.
"""

import os
import shutil
from urllib.parse import urlparse

from cod_sync.git import signing_git_env
from cod_sync.work_context import WorkContext, commit_message_with_context
from small_sea_client.client import SmallSeaClient, SmallSeaSession

PURPOSE = "TideTable/calendar"


class UnauthorizedHistory(Exception):
    def __init__(self, head, refusals):
        super().__init__(f"history at {head} has {len(refusals)} commit(s) the Hub did not authorize: "
                         f"{refusals[0]['result']} ({refusals[0]['reason']})")
        self.head = head
        self.refusals = refusals


class HubSigner:
    def __init__(self, hub_url, token):
        program = shutil.which("small-sea-git-sign")
        if program is None:
            raise FileNotFoundError("small-sea-git-sign is not on PATH")
        self.session = SmallSeaSession(SmallSeaClient(port=urlparse(hub_url).port), token)
        info = self.session.session_info()
        self.team_id, self.berth_id = info["team_id"], info["berth_id"]
        self.env = signing_git_env(
            os.environ, program=program, public_key=self.session.signing_public_key(),
            extra_env={"SMALL_SEA_HUB_URL": hub_url, "SMALL_SEA_SESSION_TOKEN": token})

    def message(self, message):
        context = WorkContext("commit", self.team_id, self.berth_id, PURPOSE, self.session.authority_view())
        return commit_message_with_context(message, context)

    def verify_history(self, repo, head):
        result = self.session.verify_history(repo.git_dir, head)
        refusals = [c for c in result["commits"] if c["result"] != "authorized"]
        if refusals:
            raise UnauthorizedHistory(head, refusals)
