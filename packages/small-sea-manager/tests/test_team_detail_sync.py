"""The admission-events watcher must never abort a user's Manager mutation (#215).

htmx applies the arriving request's hx-sync strategy. The watcher polls with
"queue last", so a poll that lands during a form POST waits for it instead of
aborting it, and still runs afterwards so polling continues. User forms keep
"replace", which only ever cancels an in-flight poll.
"""

import re

from test_known_apps import _TEAM, _setup

_WATCH = "/admission-events/watch"


def _sync_by_element(html):
    elements = re.findall(r"<[a-z]+\b[^>]*hx-sync=\"[^\"]*\"[^>]*>", html)
    return [(e, re.search(r'hx-sync="([^"]*)"', e).group(1)) for e in elements]


def test_watcher_queues_behind_mutations_on_team_page(playground_dir):
    _backend, _participant, _hub, _manager, web = _setup(playground_dir)
    html = web.get(f"/teams/{_TEAM}").text
    synced = _sync_by_element(html)
    watchers = [s for e, s in synced if _WATCH in e]
    others = [s for e, s in synced if _WATCH not in e]
    assert watchers == ["#team-detail:queue last"]
    assert others and all(s == "#team-detail:replace" for s in others)


def test_watch_response_rearms_with_queue_last(playground_dir):
    _backend, _participant, _hub, _manager, web = _setup(playground_dir)
    html = web.get(f"/teams/{_TEAM}{_WATCH}").text
    watchers = [s for e, s in _sync_by_element(html) if _WATCH in e]
    assert watchers == ["#team-detail:queue last"]
