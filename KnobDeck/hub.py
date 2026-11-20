# The link to KnobDeck's companion hub (companion protocol 1, `knobdeck-protocol::companion` in
# Knobdeck/knobdeck-sdk), without threads: Live calls the Remote Script's update_display about
# every 100 ms on its main thread, and that tick calls `Link.poll()`, which moves a non-blocking
# socket one step on and returns the requests that arrived. Nothing here ever waits.
#
#   discovery file (port, token) -> ws://127.0.0.1:<port>/companion -> hello -> ready
#   hub: request {id, action, params}   ->   companion: response {id, ok, result | error}
#   companion: event {event, payload}, ping every 20 s (answered pong)
#
# KnobDeck writes a new token on every start, so the discovery file is read again on every
# connection attempt. Without the file KnobDeck isn't running: no port is tried (the hub refuses
# native companions without the token anyway).
#
# Python 3.7 compatible (Live 11's Python).

import errno
import json
import os
import select
import socket
import sys
import time

from . import ws

PROTOCOL = 1
DISCOVERY_FILE = "companion-hub.json"
WS_PATH = "/companion"
HOST = "127.0.0.1"
PING_EVERY = 20.0
STEP_TIMEOUT = 5.0  # s: connecting, the upgrade and the hello answer each get this long
RETRY_MIN, RETRY_MAX = 1.0, 30.0
RECV_LIMIT = 1 << 20  # bytes read per poll at most, so a flood can't stall Live

_WOULD_BLOCK = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EINPROGRESS, getattr(errno, "WSAEWOULDBLOCK", 10035)}


def os_name(platform=None):
    """KnobDeck's name for this OS: the plugin id is `ableton-<os>`."""
    p = platform or sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def data_dir(platform=None, env=None, home=None):
    """KnobDeck's data folder, where it writes the discovery file."""
    p = platform or sys.platform
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    if p.startswith("win"):
        return os.path.join(env.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "KnobDeck")
    if p == "darwin":
        return os.path.join(home, "Library", "Application Support", "KnobDeck")
    return os.path.join(env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "KnobDeck")


def discovery_path():
    # KNOBDECK_COMPANION_DISCOVERY: another discovery file (tests' fake hub).
    return os.environ.get("KNOBDECK_COMPANION_DISCOVERY") or os.path.join(data_dir(), DISCOVERY_FILE)


def read_discovery(path=None):
    """{port, token, path} from the discovery file, or None when KnobDeck hasn't written it."""
    try:
        with open(path or discovery_path(), encoding="utf-8") as f:
            d = json.load(f)
        return {"port": int(d["port"]), "token": str(d.get("token") or ""), "path": str(d.get("path") or WS_PATH)}
    except (OSError, ValueError, KeyError, TypeError):
        return None


class Link:
    """One link to the hub, kept up by calling `poll()` regularly from a single thread."""

    def __init__(self, plugin, instance, app, app_version=None, name=None, version=None, features=(), clock=time.monotonic, log=None):
        self.hello = {"type": "hello", "protocol": PROTOCOL, "plugin": plugin, "instance": instance, "app": app, "features": list(features)}
        for k, v in (("app_version", app_version), ("name", name), ("version", version)):
            if v:
                self.hello[k] = v
        self.status = {"state": "disconnected"}
        self._clock = clock
        self._log = log
        self._phase = "idle"  # idle, connecting, upgrading, hello, linked
        self._sock = None
        self._out = b""
        self._in = b""
        self._decoder = None
        self._key = None
        self._deadline = 0.0
        self._next_try = 0.0
        self._retry = RETRY_MIN
        self._last_ping = 0.0
        self._link = None
        self._closed = False
        self._discovery = None

    # ── public ──────────────────────────────────────────────────────────

    @property
    def connected(self):
        return self._phase == "linked"

    @property
    def link_id(self):
        return self._link

    def poll(self):
        """Move the connection on; returns the requests that arrived as (id, action, params)."""
        if self._closed:
            return []
        now = self._clock()
        try:
            if self._phase == "idle":
                if now >= self._next_try:
                    self._start(now)
            elif self._phase == "connecting":
                self._poll_connect(now)
            elif self._phase in ("upgrading", "hello"):
                eof = self._read()
                self._poll_handshake(now)
                if eof and self._phase in ("upgrading", "hello"):
                    raise ws.Closed("connection closed")
            elif self._phase == "linked":
                requests = self._poll_linked(now)
                self._flush()
                return requests
            self._flush()
        except (OSError, ws.Closed, ws.Refused, ValueError) as e:
            self._drop(now, str(e))
        return []

    def emit(self, event, payload=None):
        """Live state for the plugin; dropped while not linked."""
        if not self.connected:
            return False
        self._send({"type": "event", "event": event, "payload": payload})
        return True

    def respond(self, req_id, result=None, error=None, unsupported=False):
        if not self.connected:
            return
        if unsupported:
            msg = {"type": "response", "id": req_id, "ok": False, "unsupported": True}
        elif error is not None:
            msg = {"type": "response", "id": req_id, "ok": False, "error": str(error)}
        else:
            msg = {"type": "response", "id": req_id, "ok": True, "result": result}
        self._send(msg)

    def flush(self):
        try:
            self._flush()
        except OSError as e:
            self._drop(self._clock(), str(e))

    def close(self):
        """Stop for good (the script is unloaded)."""
        if self._phase == "linked":
            try:
                self._out += ws.frame(ws.OP_CLOSE, (1000).to_bytes(2, "big"))
                self._flush()
            except OSError:
                pass
        self._closed = True
        self._reset()
        self._set_status(state="off")

    # ── the state machine ──────────────────────────────────────────────

    def _set_status(self, **status):
        if status != self.status:
            self.status = status
            if self._log:
                self._log("KnobDeck link: " + ", ".join("{}={}".format(k, v) for k, v in status.items()))

    def _start(self, now):
        d = read_discovery()
        if d is None:
            self._set_status(state="waiting", reason="KnobDeck is not running")
            self._schedule_retry(now)
            return
        self._discovery = d
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        code = sock.connect_ex((HOST, d["port"]))
        self._sock = sock
        self._deadline = now + STEP_TIMEOUT
        self._set_status(state="connecting", port=d["port"])
        if code == 0:
            self._connected(now)
        elif code in _WOULD_BLOCK:
            self._phase = "connecting"
        else:
            raise OSError(code, os.strerror(code))

    def _poll_connect(self, now):
        _, writable, failed = select.select([], [self._sock], [self._sock], 0)
        if writable or failed:
            err = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if err:
                raise OSError(err, os.strerror(err))
            if writable:
                self._connected(now)
                return
        if now >= self._deadline:
            raise OSError(errno.ETIMEDOUT, "connecting to KnobDeck timed out")

    def _connected(self, now):
        self._key = ws.new_key()
        self._out = ws.upgrade_request(HOST, self._discovery["port"], self._discovery["path"], self._key)
        self._in = b""
        self._phase = "upgrading"
        self._deadline = now + STEP_TIMEOUT

    def _poll_handshake(self, now):
        if self._phase == "upgrading":
            rest = ws.upgrade_answer(self._in, self._key)
            if rest is not None:
                self._decoder = ws.Decoder()
                self._in = b""
                hello = dict(self.hello)
                if self._discovery["token"]:
                    hello["token"] = self._discovery["token"]
                self._out += ws.text_frame(json.dumps(hello))
                self._phase = "hello"
                self._deadline = now + STEP_TIMEOUT
                self._handle_hello_answer(now, self._decoder.feed(rest))
        elif self._phase == "hello":
            data, self._in = self._in, b""
            self._handle_hello_answer(now, self._decoder.feed(data))
        if self._phase in ("upgrading", "hello") and now >= self._deadline:
            raise OSError(errno.ETIMEDOUT, "KnobDeck didn't answer the link")

    def _handle_hello_answer(self, now, messages):
        for kind, data in messages:
            if kind == "ping":
                self._out += ws.frame(ws.OP_PONG, data)
            elif kind == "close":
                raise ws.Closed("closed by KnobDeck")
            elif kind == "text":
                msg = json.loads(data)
                if msg.get("type") == "refused":
                    reason = str(msg.get("reason", ""))
                    self._reset()
                    self._set_status(state="refused", reason=reason)
                    self._schedule_retry(now)
                    return
                if msg.get("type") != "ready":
                    raise ws.Closed("unexpected answer to hello")
                self._link = str(msg.get("link", ""))
                self._phase = "linked"
                self._retry = RETRY_MIN
                self._last_ping = now
                self._set_status(state="connected", link=self._link, port=self._discovery["port"])
                return

    def _poll_linked(self, now):
        eof = self._read()
        data, self._in = self._in, b""
        requests = []
        for kind, payload in self._decoder.feed(data) if data else []:
            if kind == "ping":
                self._out += ws.frame(ws.OP_PONG, payload)
            elif kind == "close":
                try:
                    self._out += ws.frame(ws.OP_CLOSE, payload[:2])
                    self._flush()
                except OSError:
                    pass
                raise ws.Closed("closed by KnobDeck")
            elif kind == "text":
                try:
                    msg = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "request":
                    params = msg.get("params")
                    requests.append((str(msg.get("id", "")), str(msg.get("action", "")), params if isinstance(params, dict) else {}))
                # pong, and frame types from a newer hub: ignored
        if eof:
            raise ws.Closed("connection closed")  # requests that came with the close can't be answered
        if now - self._last_ping >= PING_EVERY:
            self._out += ws.text_frame('{"type":"ping"}')
            self._last_ping = now
        return requests

    # ── socket helpers ─────────────────────────────────────────────────

    def _send(self, msg):
        self._out += ws.text_frame(json.dumps(msg))

    def _flush(self):
        while self._out and self._sock is not None:
            try:
                n = self._sock.send(self._out)
            except (BlockingIOError, InterruptedError):
                return
            except OSError as e:
                if e.errno in _WOULD_BLOCK:
                    return
                raise
            self._out = self._out[n:]

    def _read(self):
        """Read what is there into self._in; True when the peer closed (after its last bytes)."""
        got = 0
        while got < RECV_LIMIT:
            try:
                chunk = self._sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                return False
            except OSError as e:
                if e.errno in _WOULD_BLOCK:
                    return False
                raise
            if not chunk:
                return True
            self._in += chunk
            got += len(chunk)
        return False

    def _schedule_retry(self, now):
        self._next_try = now + self._retry
        self._retry = min(self._retry * 2, RETRY_MAX)

    def _reset(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._out = b""
        self._in = b""
        self._decoder = None
        self._link = None
        self._phase = "idle"

    def _drop(self, now, reason):
        was_linked = self._phase == "linked"
        self._reset()
        self._set_status(state="disconnected", reason=reason)
        if was_linked:
            # KnobDeck restarting or the hub dropped us: try again soon.
            self._retry = RETRY_MIN
            self._next_try = now + 0.5
        else:
            self._schedule_retry(now)
