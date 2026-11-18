#!/usr/bin/env python3
"""Throwaway CalDAV server for the Tide Table Phase 0 client spike.

Its job is to find out what Apple Calendar and Thunderbird need from a
localhost CalDAV server, not to be the real Tide Table.
It serves one user with one calendar and stores each event as a file in a
git repo (tide_table.calendar_repo); two_teammates_demo.py runs two of these
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

Run:
    uv run python packages/tide-table/spikes/caldav_spike.py
then point a client at http://localhost:5232/ as user "alice", password "tide".
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from tide_table.calendar_repo import CalendarRepo
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
    """Events as files, plus a change counter that backs ctag and sync-token.

    The events directory is a CalendarRepo, so every save is a git commit.
    Deletions are remembered forever, so every token we ever issued stays valid.
    Handlers hold lock across each operation on the store.
    """

    def __init__(self, root, author="spike", signer=None):
        self.dir = os.path.join(root, "events")
        if os.path.isdir(os.path.join(self.dir, ".git")):
            self.cal = CalendarRepo(self.dir, signer)
        else:
            self.cal = CalendarRepo.create(self.dir, author, signer)
            if os.listdir(self.dir) != [".git"]:
                self.cal.repo.stage()
                self.cal.commit("Import events stored before git")
        self.versions = os.path.join(root, "versions")
        os.makedirs(self.versions, exist_ok=True)
        self.state_path = os.path.join(root, "state.json")
        self.lock = threading.Lock()
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                self.state = json.load(f)
        else:
            self.state = {"token": 0, "changed": {}, "deleted": {}}

    def _save(self):
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=1)

    @property
    def token(self):
        return self.state["token"]

    def names(self):
        return sorted(n for n in os.listdir(self.dir) if not n.startswith("."))

    def get(self, name):
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

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

    def put(self, name, data):
        self._keep_version(data)
        self.cal.save(name, data, f"Save {name}")
        self._mark_changed(name)
        self._save()

    def delete(self, name):
        self.cal.delete(name, f"Delete {name}")
        self._mark_deleted(name)
        self._save()

    def snapshot(self):
        """ETag of every event, to compare before and after a sync."""
        return {n: etag_of(self.get(n)) for n in self.names()}

    def record_changes_since(self, before):
        """Advance the sync token for events a sync changed; return their names."""
        after = self.snapshot()
        changed = sorted(n for n in after if before.get(n) != after[n])
        deleted = sorted(n for n in before if n not in after)
        for n in changed:
            self._keep_version(self.get(n))
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


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TideTableSpike/0"

    # --- plumbing -----------------------------------------------------------

    def log_message(self, fmt, *args):
        pass  # we write our own log in _finish

    @property
    def cfg(self):
        return self.server.cfg

    def _paths(self):
        u = self.cfg["user"]
        return f"/dav/principals/{u}/", f"/dav/calendars/{u}/", f"/dav/calendars/{u}/{CAL_NAME}/"

    def _read_body(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            data = b""
            while True:
                size = int(self.rfile.readline().strip().split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    return data
                data += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, status, body=b"", headers=None, content_type=None):
        self.send_response(status)
        headers = dict(headers or {})
        if content_type:
            headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
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
        self.send_response(401)
        self.send_header("WWW-Authenticate",
                         f'Digest realm="{REALM}", nonce="{self.server.nonce}", qop="auth", algorithm=MD5')
        self.send_header("WWW-Authenticate", f'Basic realm="{REALM}"')
        body = b"auth required"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
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

    def _handle(self):
        self._resp = None
        self._note = ""
        self._req_body = self._read_body()
        try:
            if self.command != "OPTIONS" and not self._authorized():
                self._challenge()
                return
            kind, name = self._classify(unquote(urlsplit(self.path).path))
            if kind == "wellknown":
                self._send(301, headers={"Location": "/dav/"})
                return
            getattr(self, "h_" + self.command.replace("-", "_"), self.h_unsupported)(kind, name)
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
        with self.server.log_lock, open(self.cfg["log"], "a") as f:
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

    def h_OPTIONS(self, kind, name):
        self._send(200, headers={
            "DAV": "1, 2, 3, calendar-access",
            "Allow": "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, REPORT",
        })

    def h_PROPFIND(self, kind, name):
        if kind == "unknown":
            self._send(404)
            return
        store = self.server.store
        props = requested_props(self._parse())
        depth = self.headers.get("Depth", "infinity")
        with store.lock:
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

    def h_PROPPATCH(self, kind, name):
        # Pretend every property was set; the log shows what the client wanted.
        root = self._parse()
        found = []
        if root is not None:
            for prop in root.iter(f"{{{DAV}}}prop"):
                for child in prop:
                    ns, local = child.tag[1:].split("}", 1)
                    found.append(empty_elem(ns, local))
        self._xml(207, multistatus([response_xml(self._href(kind, name), found, [])]))

    def h_REPORT(self, kind, name):
        if kind != "calendar":
            self._send(403)
            return
        root = self._parse()
        with self.server.store.lock:
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

    def h_GET(self, kind, name):
        if kind != "event":
            self._send(200 if kind != "unknown" else 404, b"Tide Table CalDAV spike\n", content_type="text/plain")
            return
        store = self.server.store
        with store.lock:
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

    def h_PUT(self, kind, name):
        if kind != "event":
            self._send(403)
            return
        store = self.server.store
        headers = {}
        with store.lock:
            current = store.get(name)
            if self._precondition_fails(current):
                merged = self._merge_stale_put(current)
                if merged is None:
                    status = 412
                else:
                    store.put(name, merged)
                    # No ETag: the stored body differs from what the client sent, so it must refetch.
                    status = 204
            else:
                store.put(name, self._req_body)
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

    def h_DELETE(self, kind, name):
        store = self.server.store
        if kind != "event":
            self._send(403)
            return
        with store.lock:
            current = store.get(name)
            if current is None:
                status = 404
            elif self._precondition_fails(current):
                status = 412
            else:
                store.delete(name)
                status = 204
        self._send(status)

    def h_unsupported(self, kind, name):
        self._send(405, headers={"Allow": "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, REPORT"})


for _m in ["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "GET", "HEAD", "PUT", "DELETE",
           "POST", "MKCALENDAR", "MKCOL", "MOVE", "COPY", "LOCK", "UNLOCK", "ACL"]:
    setattr(Handler, "do_" + _m, Handler._handle)


def make_server(port, user, password, data, log_bodies=False, signer=None):
    os.makedirs(data, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.cfg = {"user": user, "password": password, "log": os.path.join(data, "requests.log"),
                  "log_bodies": log_bodies}
    server.store = Store(data, author=user, signer=signer)
    server.log_lock = threading.Lock()
    server.nonce = secrets.token_hex(16)
    print(f"Tide Table CalDAV spike on http://localhost:{port}/  user={user} password={password}")
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
    make_server(args.port, args.user, args.password, data, args.log_bodies).serve_forever()


if __name__ == "__main__":
    main()
