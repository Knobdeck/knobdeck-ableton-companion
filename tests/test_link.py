# The hub link (KnobDeck/hub.py) against the fake hub. The test plays Live: a thread calls
# `poll()` every 10 ms (Live's tick is ~100 ms), as update_display does; nothing else touches the link.
import os
import threading
import time

import pytest

from fake_hub import FakeHub
from KnobDeck import hub


def wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class Ticker:
    """Live's main thread: polls the link and answers requests with `handler`."""

    def __init__(self, link, handler=None):
        self.link, self.handler = link, handler
        self.calls = []
        self._stop = False
        self.max_poll = 0.0
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while not self._stop:
            start = time.monotonic()
            for req in self.link.poll():
                self.calls.append(req)
                if self.handler:
                    self.handler(*req)
            self.link.flush()
            self.max_poll = max(self.max_poll, time.monotonic() - start)
            time.sleep(0.01)

    def stop(self):
        self._stop = True
        self._t.join(2)


@pytest.fixture
def fake_hub(monkeypatch):
    h = FakeHub(accepts=lambda p: p == "ableton-test")
    monkeypatch.setenv("KNOBDECK_COMPANION_DISCOVERY", h.discovery)
    monkeypatch.setattr(hub, "RETRY_MIN", 0.05)
    yield h
    h.close()


def new_link():
    return hub.Link(
        "ableton-test", "inst-1", "ableton", app_version="12.1.5", name="Ableton Live 12.1", version="1.0.0", features=["state"]
    )


def test_hello_carries_the_token_and_who_we_are(fake_hub):
    link = new_link()
    t = Ticker(link)
    try:
        fake_hub.wait_link()
        hello = fake_hub.frames[0]
        assert hello == {
            "type": "hello",
            "protocol": 1,
            "plugin": "ableton-test",
            "instance": "inst-1",
            "app": "ableton",
            "features": ["state"],
            "app_version": "12.1.5",
            "name": "Ableton Live 12.1",
            "version": "1.0.0",
            "token": fake_hub.token,
        }
        assert wait(lambda: link.connected)
        assert link.status["state"] == "connected"
        assert link.link_id == "ableton-1"
    finally:
        t.stop()


def test_requests_are_answered_and_events_reach_the_hub(fake_hub):
    events = []
    fake_hub.on_event = lambda l, e, p: events.append((e, p))
    link = new_link()

    def handler(rid, action, params):
        if action == "echo":
            link.respond(rid, result={"got": params})
        elif action == "fail":
            link.respond(rid, error="No track is selected")
        else:
            link.respond(rid, unsupported=True)

    t = Ticker(link, handler)
    try:
        fake_hub.wait_link()
        assert wait(lambda: link.connected)
        r = fake_hub.request("echo", {"a": 1})
        assert r["ok"] and r["result"] == {"got": {"a": 1}}
        r = fake_hub.request("fail")
        assert (r["ok"], r["error"]) == (False, "No track is selected")
        r = fake_hub.request("nope")
        assert (r["ok"], r["unsupported"]) == (False, True)
        big = "x" * 300000  # 64-bit frame lengths both ways, more than one socket buffer
        assert fake_hub.request("echo", {"s": big})["result"]["got"]["s"] == big
        assert link.emit("state", {"tempo": 120})
        assert wait(lambda: events == [("state", {"tempo": 120})])
        # Polling never blocks Live: even the big message took only a few milliseconds per tick.
        assert t.max_poll < 0.25, t.max_poll
    finally:
        t.stop()


def test_a_request_sent_with_ready_is_answered(fake_hub):
    fake_hub.with_ready = [{"type": "request", "id": "r0", "action": "echo", "params": {"n": 1}}]
    link = new_link()
    t = Ticker(link, lambda rid, action, params: link.respond(rid, result=params))
    try:
        fake_hub.wait_link()
        assert wait(lambda: any(f.get("type") == "response" and f.get("id") == "r0" for f in fake_hub.frames))
        r = [f for f in fake_hub.frames if f.get("id") == "r0"][0]
        assert r["ok"] and r["result"] == {"n": 1}
    finally:
        t.stop()


def test_ping(fake_hub, monkeypatch):
    monkeypatch.setattr(hub, "PING_EVERY", 0.2)
    t = Ticker(new_link())
    try:
        fake_hub.wait_link()
        assert wait(lambda: any(f.get("type") == "ping" for f in fake_hub.frames))
    finally:
        t.stop()


def test_a_refusal_is_reported_and_retried(fake_hub):
    fake_hub.accepts = lambda p: False
    link = new_link()
    t = Ticker(link)
    try:
        assert wait(lambda: link.status.get("state") == "refused")
        assert "is not running" in link.status["reason"]
        fake_hub.accepts = lambda p: True
        assert wait(lambda: link.connected, 5)
    finally:
        t.stop()


def test_reconnects_to_a_restarted_hub_with_its_new_token(fake_hub, monkeypatch):
    link = new_link()
    t = Ticker(link)
    try:
        fake_hub.wait_link()
        assert wait(lambda: link.connected)
        fake_hub.close()
        assert wait(lambda: not link.connected)
        h2 = FakeHub()  # new port, new token
        monkeypatch.setenv("KNOBDECK_COMPANION_DISCOVERY", h2.discovery)
        try:
            h2.wait_link(10)
            assert h2.frames[0]["token"] == h2.token
            assert wait(lambda: link.connected)
        finally:
            h2.close()
    finally:
        t.stop()


def test_waits_while_knobdeck_is_not_running(fake_hub, monkeypatch):
    monkeypatch.setenv("KNOBDECK_COMPANION_DISCOVERY", os.path.join(fake_hub.dir, "missing.json"))
    link = new_link()
    link.poll()
    assert link.status == {"state": "waiting", "reason": "KnobDeck is not running"}
    assert not link.connected
    # It doesn't try a port without the discovery file, and checks again after the retry delay.
    assert fake_hub.frames == []


def test_a_closed_port_fails_without_blocking(tmp_path, monkeypatch):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens there now
    d = tmp_path / "companion-hub.json"
    d.write_text('{"protocol": 1, "port": %d, "path": "/companion", "token": "t"}' % port)
    monkeypatch.setenv("KNOBDECK_COMPANION_DISCOVERY", str(d))
    link = new_link()
    # Windows reports a refused loopback connect only after ~2 s of retries: each poll must still
    # return at once while it waits.
    end, slowest = time.monotonic() + 8, 0.0
    while time.monotonic() < end and link.status.get("state") != "disconnected":
        t = time.monotonic()
        link.poll()
        slowest = max(slowest, time.monotonic() - t)
        time.sleep(0.01)
    assert link.status.get("state") == "disconnected", link.status
    assert slowest < 0.1, slowest


def test_close_stops_for_good(fake_hub):
    link = new_link()
    t = Ticker(link)
    try:
        fake_hub.wait_link()
        assert wait(lambda: link.connected)
    finally:
        t.stop()
    link.close()
    assert link.status == {"state": "off"}
    assert link.poll() == []
    assert wait(lambda: not fake_hub.links)


def test_paths_match_knobdeck():
    assert hub.data_dir("win32", {"APPDATA": r"C:\Users\a\AppData\Roaming"}) == os.path.join(r"C:\Users\a\AppData\Roaming", "KnobDeck")
    assert hub.data_dir("win32", {}, home="H") == os.path.join("H", "AppData", "Roaming", "KnobDeck")
    assert hub.data_dir("darwin", {}, home="/Users/a") == os.path.join("/Users/a", "Library", "Application Support", "KnobDeck")
    assert [hub.os_name(p) for p in ("win32", "darwin", "linux")] == ["windows", "macos", "linux"]
