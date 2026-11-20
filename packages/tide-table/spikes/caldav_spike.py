#!/usr/bin/env python3
"""Throwaway CalDAV server for the Tide Table Phase 0 client spike.

Its job is to find out what Apple Calendar and Thunderbird need from a
localhost CalDAV server, not to be the real Tide Table.
It serves one user with one calendar and stores the events in a SQLite file
in a git repo (tide_table.calendar_repo); two_teammates_demo.py runs two of these
and syncs them through the Hub.
Use --log-bodies with synthetic events to capture CalDAV payloads.

Deliberate shortcuts:
- calendar-query ignores its filters and returns every event.
- PROPPATCH reports success but stores nothing.
- One hard-coded user and password, accepted by Digest or Basic auth.
- No recurrence or time-zone handling; event bodies are stored verbatim.

A PUT whose If-Match names an older version is merged with the current
version (tide_table.merge) instead of refused, using the older version as the
base. Every stored body is kept under versions/ for this. A conflict, or a
PUT with no If-Match, behaves as before (412, or overwrite).

When a teammate's changes do not merge (Store.merge), the calendar shows a
placeholder event for each conflict (tide_table/conflicts.py), linking to a
page on this server, /conflicts/<secret>, where the person keeps their
version or the teammate's. Deleting a placeholder in a client hides it until
its conflict changes; editing one is ignored.

Run:
    uv run python packages/tide-table/spikes/caldav_spike.py
then point a client at http://localhost:5232/ as user "alice", password "tide".
"""

import argparse
import asyncio
import base64
import datetime
import hashlib
import json
import os
import re
import secrets
from urllib.parse import parse_qs, quote, unquote, urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import uvicorn
from starlette.requests import Request
from starlette.responses import Response

from tide_table import conflicts
from tide_table.calendar_repo import CalendarRepo, EventConflict, peer_ref
from tide_table.merge import Conflict, Unsupported, merge_event

DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"
CS = "http://calendarserver.org/ns/"
ICAL = "http://apple.com/ns/ical/"
PREFIX = {DAV: "d", CAL: "c", CS: "cs", ICAL: "ical"}
NSDECL = " ".join(f'xmlns:{p}="{ns}"' for ns, p in PREFIX.items())
SYNC_PREFIX = "http://tide-table.invalid/sync/"
CAL_NAME = "team"
REALM = "Tide Table spike"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))


class Store:
    """Events in a CalendarRepo, plus a change counter that backs ctag and sync-token.

    The calendar directory is a CalendarRepo, so every save is a git commit.
    Deletions are remembered forever, so every token we ever issued stays valid.
    Whoever holds lock owns the work tree: CalDAV requests hold it across each
    operation on the store, and the demo holds it across each merge.
    """

    def __init__(self, root, cal):
        """Use Store.open."""
        self.cal = cal
        self.versions = os.path.join(root, "versions")
        os.makedirs(self.versions, exist_ok=True)
        self.state_path = os.path.join(root, "state.json")
        self.lock = asyncio.Lock()
        self.on_commit = None  # called after each save, delete or resolution commits
        self.base_url = None  # this server's URL, for links in placeholder events
        self.teammate_names = {}  # teammate ID -> name shown in placeholders
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                self.state = json.load(f)
        else:
            self.state = {"token": 0, "changed": {}, "deleted": {}}
        # Conflicts with each teammate, kept with the state so that a
        # placeholder clients have seen is withdrawn even after a restart.
        self.state.setdefault("conflicts", {})
        self.state.setdefault("dismissed", {})  # placeholder name -> ETag of the version dismissed
        self.state.setdefault("conflict_secret", secrets.token_urlsafe(16))

    @classmethod
    async def open(cls, root, author="spike", signer=None):
        calendar = os.path.join(root, "calendar")
        if os.path.isdir(os.path.join(calendar, ".git")):
            return cls(root, CalendarRepo(calendar, signer))
        return cls(root, await CalendarRepo.create(calendar, author, signer))

    def _save(self):
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=1)

    @property
    def token(self):
        return self.state["token"]

    def names(self):
        return sorted(self.cal.names() + list(self._visible_placeholders()))

    def get(self, name):
        placeholder = self._visible_placeholders().get(name)
        return placeholder if placeholder is not None else self.cal.read(name)

    # --- conflicts ------------------------------------------------------------

    @property
    def conflict_page(self):
        return f"/conflicts/{self.state['conflict_secret']}"

    def _placeholders(self):
        """A placeholder event for each known conflict: name -> body."""
        out = {}
        for teammate_id, entry in self.state["conflicts"].items():
            since = datetime.datetime.fromisoformat(entry["since"])
            name = self.teammate_names.get(teammate_id, teammate_id[:8])
            for c in entry["details"]:
                out[conflicts.placeholder_name(teammate_id, c)] = conflicts.placeholder(
                    teammate_id, name, c, f"{self.base_url}{self.conflict_page}", since)
        return out

    def _visible_placeholders(self):
        """Placeholders except those a client deleted, unless they have changed since."""
        return {n: b for n, b in self._placeholders().items() if self.state["dismissed"].get(n) != etag_of(b)}

    def is_placeholder(self, name):
        return name in self._placeholders()

    def _set_conflicts(self, teammate_id, details=None, heads=None):
        """Record the conflicts with a teammate (none if details is None) and update the placeholders."""
        before = self._visible_placeholders()
        old = self.state["conflicts"].get(teammate_id)
        if details is None:
            self.state["conflicts"].pop(teammate_id, None)
        elif old and old["details"] == details:
            old["heads"] = heads
        else:
            since = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.state["conflicts"][teammate_id] = {"details": details, "heads": heads, "since": since}
        after = self._visible_placeholders()
        for n in after:
            if before.get(n) != after[n]:
                self._mark_changed(n)
        for n in before:
            if n not in after:
                self._mark_deleted(n)
        placeholders = self._placeholders()
        self.state["dismissed"] = {n: e for n, e in self.state["dismissed"].items() if n in placeholders}
        self._save()

    def dismiss(self, name):
        """A client deleted a placeholder: hide it until its conflict changes."""
        self.state["dismissed"][name] = etag_of(self._placeholders()[name])
        self._mark_deleted(name)
        self._save()

    def refuse_edit(self, name):
        """A client changed a placeholder: report it changed so the client fetches it again."""
        self._mark_changed(name)
        self._save()

    async def merge(self, teammate_id, resolutions=None, theirs=None):
        """Merge a teammate's parked changes; the caller holds lock.

        theirs is the commit to merge; by default, whatever the teammate's
        parked ref points at now. The sync agent can move that ref at any
        time, so the commit is fixed before merging, and conflicts are
        recorded against the commits that produced them.

        Returns (events merged field by field, names changed, names deleted,
        whether main moved). On conflict, records the conflicts as
        placeholders and raises EventConflict; a merge that succeeds
        withdraws the teammate's placeholders.
        """
        before = self.snapshot()
        head = await self.cal.repo.head() if await self.cal.repo.has_commits() else None
        theirs = theirs or await self.cal.repo.resolve_ref(peer_ref(teammate_id))
        try:
            merged = await self.cal.merge(theirs, resolutions)
        except EventConflict as e:
            # With choices, the driver leaves out the conflicts they settle;
            # the conflicts recorded without them stay, so all can be chosen again.
            if resolutions is None:
                self._set_conflicts(teammate_id, e.details, [head, theirs])
            raise
        self._set_conflicts(teammate_id)
        changed, deleted = self.record_changes_since(before)
        return merged, changed, deleted, await self.cal.repo.head() != head

    async def resolve(self, teammate_id, resolutions, heads):
        """Merge a teammate's changes with a person's choices for the conflicts.

        heads are the two commits whose conflicts the person saw. If this
        device has committed since, or the conflicts have since been
        recorded against other commits, the choices are not applied and the
        conflicts are refreshed. Otherwise exactly the teammate's commit the
        person saw is merged; anything fetched since merges afterwards as a
        new change.

        Returns "resolved", "stale", or "unresolved" if conflicts remain.
        """
        await self.lock.acquire()
        # Its own task, so an abandoned request cannot stop a merge halfway.
        return await asyncio.shield(asyncio.create_task(self._resolve_holding_lock(teammate_id, resolutions, heads)))

    async def _resolve_holding_lock(self, teammate_id, resolutions, heads):
        try:
            entry = self.state["conflicts"].get(teammate_id)
            if entry is None:
                return "stale"
            ours = await self.cal.repo.head() if await self.cal.repo.has_commits() else None
            fresh = entry["heads"] == heads and heads[0] == ours
            try:
                if fresh:
                    *_, moved = await self.merge(teammate_id, resolutions, theirs=heads[1])
                else:
                    *_, moved = await self.merge(teammate_id)
            except EventConflict:
                return "unresolved" if fresh else "stale"
            if moved and self.on_commit:
                self.on_commit()
            return "resolved" if fresh else "stale"
        finally:
            self.lock.release()

    def version(self, etag):
        """A body this store has held, looked up by its ETag, or None."""
        match = re.fullmatch(r'"([0-9a-f]{32})"', etag)
        if match is None:
            return None
        path = os.path.join(self.versions, match[1] + ".ics")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def _keep_version(self, data):
        with open(os.path.join(self.versions, etag_of(data).strip('"') + ".ics"), "wb") as f:
            f.write(data)

    def _mark_changed(self, name):
        self.state["token"] += 1
        self.state["changed"][name] = self.token
        self.state["deleted"].pop(name, None)

    def _mark_deleted(self, name):
        self.state["token"] += 1
        self.state["changed"].pop(name, None)
        self.state["deleted"][name] = self.token

    async def put(self, name, data):
        self._keep_version(data)
        await self.cal.save(name, data, f"Save {name}")
        self._mark_changed(name)
        self._save()
        if self.on_commit:
            self.on_commit()

    async def delete(self, name):
        await self.cal.delete(name, f"Delete {name}")
        self._mark_deleted(name)
        self._save()
        if self.on_commit:
            self.on_commit()

    def snapshot(self):
        """ETag of every stored event, to compare before and after a sync."""
        return {n: etag_of(self.cal.read(n)) for n in self.cal.names()}

    def record_changes_since(self, before):
        """Advance the sync token for events a sync changed; return their names."""
        after = self.snapshot()
        changed = sorted(n for n in after if before.get(n) != after[n])
        deleted = sorted(n for n in before if n not in after)
        for n in changed:
            self._keep_version(self.cal.read(n))
            self._mark_changed(n)
        for n in deleted:
            self._mark_deleted(n)
        self._save()
        return changed, deleted

    def changes_since(self, token):
        changed = [n for n, t in self.state["changed"].items() if t > token and n in self.names()]
        deleted = [n for n, t in self.state["deleted"].items() if t > token]
        return sorted(changed), sorted(deleted)


def etag_of(data):
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


def tag(ns, local):
    return f"{PREFIX[ns]}:{local}" if ns in PREFIX else None


def empty_elem(ns, local):
    t = tag(ns, local)
    return f"<{t}/>" if t else f'<x:{local} xmlns:x="{escape(ns)}"/>'


def full_elem(ns, local, inner):
    t = tag(ns, local)
    return f"<{t}>{inner}</{t}>"


def multistatus(parts):
    body = f'<?xml version="1.0" encoding="utf-8"?>\n<d:multistatus {NSDECL}>' + "".join(parts) + "</d:multistatus>"
    return body.encode()


def response_xml(href, found, missing):
    out = f"<d:response><d:href>{escape(href)}</d:href>"
    if found:
        out += "<d:propstat><d:prop>" + "".join(found) + "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
    if missing:
        out += "<d:propstat><d:prop>" + "".join(missing) + "</d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>"
    return out + "</d:response>"


def requested_props(elem):
    """Return [(ns, local)] from a <d:prop> under elem, or None for allprop/empty."""
    if elem is None:
        return None
    prop = elem.find(f"{{{DAV}}}prop")
    if prop is None:
        return None
    out = []
    for child in prop:
        ns, local = child.tag[1:].split("}", 1) if child.tag.startswith("{") else ("", child.tag)
        out.append((ns, local))
    return out


PRIVILEGES = "".join(
    f"<d:privilege>{p}</d:privilege>"
    for p in ["<d:all/>", "<d:read/>", "<d:write/>", "<d:write-content/>", "<d:write-properties/>",
              "<d:bind/>", "<d:unbind/>", "<d:read-current-user-privilege-set/>", "<c:read-free-busy/>"]
)


class Handler:
    """One CalDAV request: its method, path, headers and body, and the response built for it."""

    def __init__(self, server, request, body):
        self.server = server  # the CalDAVApp: cfg, store, nonce
        self.command = request.method
        # The ASGI server has already percent-decoded scope["path"], so a
        # decoded "?" or "%" is part of the name; decoding again would change it.
        self.decoded_path = request.scope["path"]
        query = request.scope["query_string"]
        self.path = request.scope["raw_path"].decode("latin-1") + (f"?{query.decode('latin-1')}" if query else "")
        self.headers = request.headers
        self._req_body = body

    # --- plumbing -----------------------------------------------------------

    @property
    def cfg(self):
        return self.server.cfg

    def _paths(self):
        u = self.cfg["user"]
        return f"/dav/principals/{u}/", f"/dav/calendars/{u}/", f"/dav/calendars/{u}/{CAL_NAME}/"

    def _send(self, status, body=b"", headers=None, content_type=None):
        headers = dict(headers or {})
        if content_type:
            headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
        # uvicorn leaves out the body of a response to HEAD.
        self.response = Response(body, status, headers)
        self._resp = (status, headers, body)

    def _xml(self, status, body):
        self._send(status, body, content_type='application/xml; charset="utf-8"')

    def _authorized(self):
        h = self.headers.get("Authorization", "")
        if h.startswith("Digest "):
            return self._digest_ok(h[7:])
        if not h.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return False
        return user == self.cfg["user"] and pw == self.cfg["password"]

    def _digest_ok(self, params):
        # RFC 7616 Digest with MD5 and qop=auth. Apple Calendar will not send
        # a Basic password over plain HTTP, so the spike offers this too.
        p = {m.group(1): m.group(3) if m.group(3) is not None else m.group(4)
             for m in re.finditer(r'(\w+)=("([^"]*)"|([^,\s]*))', params)}
        if p.get("username") != self.cfg["user"] or p.get("nonce") != self.server.nonce:
            return False
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
        ha1 = md5(f"{self.cfg['user']}:{REALM}:{self.cfg['password']}")
        ha2 = md5(f"{self.command}:{p.get('uri', '')}")
        if p.get("qop"):
            expected = md5(f"{ha1}:{p['nonce']}:{p.get('nc', '')}:{p.get('cnonce', '')}:{p['qop']}:{ha2}")
        else:
            expected = md5(f"{ha1}:{p['nonce']}:{ha2}")
        return p.get("response") == expected

    def _challenge(self):
        body = b"auth required"
        self.response = Response(body, 401)
        for offer in (f'Digest realm="{REALM}", nonce="{self.server.nonce}", qop="auth", algorithm=MD5',
                      f'Basic realm="{REALM}"'):
            self.response.raw_headers.append((b"www-authenticate", offer.encode()))
        self._resp = (401, {"WWW-Authenticate": "Digest + Basic"}, body)

    def _classify(self, path):
        principal, home, cal = self._paths()
        coll = path if path.endswith("/") else path + "/"
        if coll in ("/", "/dav/"):
            return "root", None
        if coll == "/.well-known/caldav/":
            return "wellknown", None
        if coll == "/dav/principals/":
            return "principals", None
        if coll == principal:
            return "principal", None
        if coll == home:
            return "home", None
        if coll == cal:
            return "calendar", None
        if path.startswith(cal):
            name = path[len(cal):]
            if name and "/" not in name and "\x00" not in name and not name.startswith("."):
                return "event", name
        return "unknown", None

    def _href(self, kind, name=None):
        principal, home, cal = self._paths()
        return {"root": "/dav/", "principals": "/dav/principals/", "principal": principal,
                "home": home, "calendar": cal}.get(kind) or cal + quote(name)

    async def handle(self):
        """Build self.response."""
        self._resp = None
        self._note = ""
        try:
            if self.decoded_path.startswith("/conflicts/"):
                await self.h_conflicts()  # its own secret instead of the CalDAV password
                return
            if self.command != "OPTIONS" and not self._authorized():
                self._challenge()
                return
            kind, name = self._classify(self.decoded_path)
            if kind == "wellknown":
                self._send(301, headers={"Location": "/dav/"})
                return
            await getattr(self, "h_" + self.command.replace("-", "_"), self.h_unsupported)(kind, name)
        except Exception as e:  # keep serving; log the failure
            import traceback
            traceback.print_exc()
            if self._resp is None:
                self._send(500, repr(e).encode())
        finally:
            self._finish()

    def _finish(self):
        status, rheaders, rbody = self._resp or (0, {}, b"")
        stamp = datetime.datetime.now().isoformat(timespec="milliseconds")
        ua = self.headers.get("User-Agent", "")
        note = f"  ({self._note})" if self._note else ""
        print(f"{stamp} {self.command} {self.path} depth={self.headers.get('Depth', '-')} -> {status}  [{ua[:40]}]{note}", flush=True)
        interesting = ["User-Agent", "Depth", "If-Match", "If-None-Match", "Content-Type", "Prefer", "Brief"]
        lines = [f"===== {stamp} {self.command} {self.path}"]
        lines += [f"> {h}: {self.headers[h]}" for h in interesting if self.headers.get(h)]
        if self.cfg["log_bodies"] and self._req_body:
            lines.append(self._req_body.decode(errors="replace"))
        lines.append(f"----- {status}{note}")
        lines += [f"< {k}: {v}" for k, v in rheaders.items() if k != "Content-Length"]
        if self.cfg["log_bodies"] and rbody:
            text = rbody.decode(errors="replace")
            lines.append(text if len(text) < 8000 else text[:8000] + "\n...[truncated]")
        with open(self.cfg["log"], "a") as f:
            f.write("\n".join(lines) + "\n\n")

    # --- properties ---------------------------------------------------------

    def _prop(self, kind, name, ns, local):
        """Inner XML for one property, or None if this resource lacks it."""
        principal, home, cal = self._paths()
        store = self.server.store
        key = (ns, local)
        common = {
            (DAV, "current-user-principal"): f"<d:href>{principal}</d:href>",
            (DAV, "principal-collection-set"): "<d:href>/dav/principals/</d:href>",
            (DAV, "current-user-privilege-set"): PRIVILEGES,
            (DAV, "owner"): f"<d:href>{principal}</d:href>",
        }
        if key in common:
            return common[key]
        if kind in ("root", "principals", "principal"):
            vals = {
                (DAV, "resourcetype"): "<d:collection/><d:principal/>" if kind == "principal" else "<d:collection/>",
                (DAV, "displayname"): self.cfg["user"],
                (DAV, "principal-URL"): f"<d:href>{principal}</d:href>",
                (CAL, "calendar-home-set"): f"<d:href>{home}</d:href>",
                (CAL, "calendar-user-address-set"):
                    f"<d:href>mailto:{self.cfg['user']}@localhost</d:href><d:href>{principal}</d:href>",
            }
            return vals.get(key)
        if kind == "home":
            return {(DAV, "resourcetype"): "<d:collection/>",
                    (DAV, "displayname"): "Calendars"}.get(key)
        if kind == "calendar":
            vals = {
                (DAV, "resourcetype"): "<d:collection/><c:calendar/>",
                (DAV, "displayname"): "Team (spike)",
                (CAL, "calendar-description"): "Tide Table CalDAV spike",
                (CAL, "supported-calendar-component-set"): '<c:comp name="VEVENT"/>',
                (CAL, "supported-calendar-data"): '<c:calendar-data content-type="text/calendar" version="2.0"/>',
                (CS, "getctag"): str(store.token),
                (DAV, "sync-token"): f"{SYNC_PREFIX}{store.token}",
                (DAV, "supported-report-set"): "".join(
                    f"<d:supported-report><d:report>{r}</d:report></d:supported-report>"
                    for r in ["<c:calendar-multiget/>", "<c:calendar-query/>", "<d:sync-collection/>"]),
                (ICAL, "calendar-color"): "#2E7D9AFF",
                (ICAL, "calendar-order"): "1",
            }
            return vals.get(key)
        if kind == "event":
            data = store.get(name)
            if data is None:
                return None
            vals = {
                (DAV, "resourcetype"): "",
                (DAV, "getetag"): escape(etag_of(data)),
                (DAV, "getcontenttype"): "text/calendar; charset=utf-8; component=vevent",
                (DAV, "getcontentlength"): str(len(data)),
                (CAL, "calendar-data"): escape(data.decode(errors="replace")),
            }
            return vals.get(key)
        return None

    def _default_props(self, kind):
        base = [(DAV, "resourcetype"), (DAV, "displayname")]
        if kind == "calendar":
            base += [(CS, "getctag"), (DAV, "sync-token")]
        if kind == "event":
            base = [(DAV, "resourcetype"), (DAV, "getetag"), (DAV, "getcontenttype")]
        return base

    def _response_for(self, kind, name, props):
        found, missing = [], []
        for ns, local in props if props is not None else self._default_props(kind):
            v = self._prop(kind, name, ns, local)
            if v is None:
                missing.append(empty_elem(ns, local))
            elif tag(ns, local):
                found.append(full_elem(ns, local, v))
            else:
                missing.append(empty_elem(ns, local))
        return response_xml(self._href(kind, name), found, missing)

    def _parse(self):
        if not self._req_body.strip():
            return None
        return ET.fromstring(self._req_body)

    # --- methods ------------------------------------------------------------

    async def h_OPTIONS(self, kind, name):
        self._send(200, headers={
            "DAV": "1, 2, 3, calendar-access",
            "Allow": "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, REPORT",
        })

    async def h_PROPFIND(self, kind, name):
        if kind == "unknown":
            self._send(404)
            return
        store = self.server.store
        props = requested_props(self._parse())
        depth = self.headers.get("Depth", "infinity")
        async with store.lock:
            parts = []
            if kind != "event" or store.get(name) is not None:
                parts.append(self._response_for(kind, name, props))
                if depth != "0" and kind == "home":
                    parts.append(self._response_for("calendar", None, props))
                elif depth != "0" and kind == "calendar":
                    parts += [self._response_for("event", n, props) for n in store.names()]
        if not parts:
            self._send(404)
            return
        self._xml(207, multistatus(parts))

    async def h_PROPPATCH(self, kind, name):
        # Pretend every property was set; the log shows what the client wanted.
        root = self._parse()
        found = []
        if root is not None:
            for prop in root.iter(f"{{{DAV}}}prop"):
                for child in prop:
                    ns, local = child.tag[1:].split("}", 1)
                    found.append(empty_elem(ns, local))
        self._xml(207, multistatus([response_xml(self._href(kind, name), found, [])]))

    async def h_REPORT(self, kind, name):
        if kind != "calendar":
            self._send(403)
            return
        root = self._parse()
        async with self.server.store.lock:
            status, body = self._report(root)
        self._xml(status, body)

    def _report(self, root):
        store = self.server.store
        props = requested_props(root)
        rtype = root.tag if root is not None else ""
        if rtype == f"{{{CAL}}}calendar-multiget":
            parts = []
            for h in root.iter(f"{{{DAV}}}href"):
                href = (h.text or "").strip()
                try:
                    kind, n = self._classify(unquote(urlsplit(href).path))
                except ValueError:
                    kind, n = "unknown", None
                if kind != "event" or store.get(n) is None:
                    parts.append(f"<d:response><d:href>{escape(href)}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>")
                else:
                    parts.append(self._response_for("event", n, props))
            return 207, multistatus(parts)
        elif rtype == f"{{{CAL}}}calendar-query":
            return 207, multistatus([self._response_for("event", n, props) for n in store.names()])
        elif rtype == f"{{{DAV}}}sync-collection":
            text = (root.findtext(f"{{{DAV}}}sync-token") or "").strip()
            if not text:
                changed, deleted = store.names(), []
            else:
                try:
                    if not text.startswith(SYNC_PREFIX):
                        raise ValueError
                    since = int(text[len(SYNC_PREFIX):])
                    if since > store.token:
                        raise ValueError
                except ValueError:
                    return 403, f'<?xml version="1.0" encoding="utf-8"?>\n<d:error {NSDECL}><d:valid-sync-token/></d:error>'.encode()
                changed, deleted = store.changes_since(since)
            parts = [self._response_for("event", n, props) for n in changed]
            parts += [f"<d:response><d:href>{escape(self._href('event', n))}</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>"
                      for n in deleted]
            parts.append(f"<d:sync-token>{SYNC_PREFIX}{store.token}</d:sync-token>")
            return 207, multistatus(parts)
        else:
            return 403, f'<?xml version="1.0" encoding="utf-8"?>\n<d:error {NSDECL}><d:supported-report/></d:error>'.encode()

    async def h_GET(self, kind, name):
        if kind != "event":
            self._send(200 if kind != "unknown" else 404, b"Tide Table CalDAV spike\n", content_type="text/plain")
            return
        store = self.server.store
        async with store.lock:
            data = store.get(name)
        if data is None:
            self._send(404)
            return
        self._send(200, data, {"ETag": etag_of(data)}, content_type="text/calendar; charset=utf-8")

    h_HEAD = h_GET

    def _precondition_fails(self, current):
        im, inm = self.headers.get("If-Match"), self.headers.get("If-None-Match")
        if inm == "*" and current is not None:
            return True
        if im and (current is None or (im != "*" and etag_of(current) not in [t.strip() for t in im.split(",")])):
            return True
        return False

    async def h_PUT(self, kind, name):
        if kind != "event":
            self._send(403)
            return
        store = self.server.store
        headers = {}
        async with store.lock:
            current = store.get(name)
            if store.is_placeholder(name):
                store.refuse_edit(name)
                status = 204  # no ETag, so the client fetches the placeholder again
            elif self._precondition_fails(current):
                merged = self._merge_stale_put(current)
                if merged is None:
                    status = 412
                else:
                    await store.put(name, merged)
                    # No ETag: the stored body differs from what the client sent, so it must refetch.
                    status = 204
            else:
                await store.put(name, self._req_body)
                status = 201 if current is None else 204
                headers["ETag"] = etag_of(self._req_body)
        self._send(status, headers=headers)

    def _merge_stale_put(self, current):
        """Merge a PUT made against an older version; return the merged body or None."""
        im = self.headers.get("If-Match")
        if current is None or not im or self.headers.get("If-None-Match") == "*":
            return None
        base = self.server.store.version(im.strip())
        if base is None:
            self._note = "stale If-Match names a version we never stored"
            return None
        try:
            result = merge_event(base, current, self._req_body)
        except Unsupported as e:
            self._note = f"merge unsupported: {e}"
            return None
        if isinstance(result, Conflict):
            self._note = f"merge conflict: {', '.join(result.fields)}"
            return None
        self._note = "merged stale PUT"
        return result.text

    async def h_DELETE(self, kind, name):
        store = self.server.store
        if kind != "event":
            self._send(403)
            return
        async with store.lock:
            current = store.get(name)
            if current is None:
                status = 404
            elif self._precondition_fails(current):
                status = 412
            elif store.is_placeholder(name):
                store.dismiss(name)
                status = 204
            else:
                await store.delete(name)
                status = 204
        self._send(status)

    async def h_conflicts(self):
        """The conflict page, at /conflicts/<secret>, and its form's target, <page>/resolve.

        Any web page the person visits can send requests to localhost, so the
        secret in the path is what lets a request in.
        """
        store = self.server.store
        parts = self.decoded_path.split("/")
        if len(parts) < 3 or not secrets.compare_digest(parts[2].encode(), store.state["conflict_secret"].encode()):
            self._send(404)
            return
        if parts[3:] == [] and self.command == "GET":
            notice = next(iter(parse_qs(self.path.partition("?")[2]).get("result", [])), None)
            async with store.lock:
                body = conflicts.page(store.state["conflicts"], store.teammate_names,
                                      store.conflict_page + "/resolve", notice)
            self._send(200, body, content_type="text/html; charset=utf-8")
        elif parts[3:] == ["resolve"] and self.command == "POST":
            form = {k: v[0] for k, v in parse_qs(self._req_body.decode(), keep_blank_values=True).items()}
            choices = {k[len("conflict:"):]: v for k, v in form.items() if k.startswith("conflict:")}
            if not form.get("teammate") or not choices or set(choices.values()) - {"ours", "theirs"}:
                self._send(400)
                return
            # An event can be in two conflicts (one over its name, one over
            # its UID); choices that disagree about it cannot both apply.
            pairs = {(i, choice) for ids, choice in choices.items() for i in ids.split(",")}
            resolutions = dict(pairs)
            heads = [form.get("ours_head") or None, form.get("theirs_head")]
            if len(resolutions) < len(pairs):
                result = "contradictory"
            else:
                result = await store.resolve(form["teammate"], resolutions, heads)
            self._note = f"choices {result}"
            self._send(303, headers={"Location": store.conflict_page + ("" if result == "resolved" else f"?result={result}")})
        elif parts[3:] == ["resolve"] and self.command == "GET":
            # A browser sometimes fetches the form's address on its own after submitting.
            self._send(303, headers={"Location": store.conflict_page})
        else:
            self._send(404)

    async def h_unsupported(self, kind, name):
        self._send(405, headers={"Allow": "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, REPORT"})


class CalDAVApp:
    """The ASGI application: one Handler per request."""

    def __init__(self, store, cfg):
        self.store = store
        self.cfg = cfg
        self.nonce = secrets.token_hex(16)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        request = Request(scope, receive)
        handler = Handler(self, request, await request.body())
        await handler.handle()
        await handler.response(scope, receive, send)


async def make_server(port, user, password, data, log_bodies=False, signer=None):
    """A uvicorn server for one user's calendar; its store is server.store.

    Run it with `await server.serve()`. Setting server.should_exit stops it
    accepting connections; serve() returns once requests in progress finish.
    """
    os.makedirs(data, exist_ok=True)
    store = await Store.open(data, author=user, signer=signer)
    cfg = {"user": user, "password": password, "log": os.path.join(data, "requests.log"),
           "log_bodies": log_bodies}
    config = uvicorn.Config(CalDAVApp(store, cfg), host="127.0.0.1", port=port, lifespan="off", ws="none",
                            log_level="warning", access_log=False, server_header=False)
    server = uvicorn.Server(config)
    server.store = store
    store.base_url = f"http://localhost:{port}"  # port 0: the caller sets it once the port is known
    print(f"Tide Table CalDAV spike on http://localhost:{port}/  user={user} password={password}")
    print(f"conflicts: http://localhost:{port}{store.conflict_page}")
    print(f"store + request log: {data}")
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=5232)
    ap.add_argument("--user", default="alice")
    ap.add_argument("--password", default="tide")
    ap.add_argument("--data", default=None, help="store directory (default: Scratch/tide-table-spike/<user>)")
    ap.add_argument("--log-bodies", action="store_true", help="log CalDAV payloads for synthetic calendars")
    args = ap.parse_args()
    data = args.data or os.path.join(REPO_ROOT, "Scratch", "tide-table-spike", args.user)

    async def serve():
        await (await make_server(args.port, args.user, args.password, data, args.log_bodies)).serve()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
