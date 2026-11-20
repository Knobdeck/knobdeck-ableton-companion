# The whole Remote Script as Live loads it (create_instance -> update_display ticks) against the
# fake hub and the fake Live: requests run, state events follow the set, errors come back clean.
import threading
import time

import pytest

import KnobDeck
from fake_hub import FakeHub
from fake_live import CInstance, Song
from KnobDeck import hub


def wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def live(monkeypatch):
    plugin = "ableton-" + hub.os_name()
    h = FakeHub(accepts=lambda p: p == plugin)
    events = []
    h.on_event = lambda l, e, p: events.append((e, p))
    monkeypatch.setenv("KNOBDECK_COMPANION_DISCOVERY", h.discovery)
    song = Song()
    c = CInstance(song)
    script = KnobDeck.create_instance(c)
    stop = []

    def main_thread():
        while not stop:
            script.update_display()
            time.sleep(0.02)

    t = threading.Thread(target=main_thread, daemon=True)
    t.start()
    yield h, song, c, script, events
    stop.append(1)
    t.join(2)
    script.disconnect()
    h.close()


def test_links_and_reports_the_set(live):
    h, song, c, script, events = live
    h.wait_link()
    hello = h.frames[0]
    assert hello["plugin"] == "ableton-" + hub.os_name()
    assert hello["app"] == "ableton" and hello["app_version"] == "12.1.5" and hello["name"] == "Ableton Live 12.1"
    assert hello["version"] == KnobDeck.surface.VERSION
    assert wait(lambda: any(e == "state" for e, _ in events))
    first = [p for e, p in events if e == "state"][0]
    assert first["tempo"] == 120.0 and first["tracks"][0]["name"] == "1-Audio"
    assert wait(lambda: "KnobDeck linked" in c.messages)


def test_requests_change_the_set_and_the_state_follows(live):
    h, song, c, script, events = live
    h.wait_link()
    assert wait(lambda: events)
    r = h.request("toggle", {"switch": "metronome"})
    assert r["ok"] and r["result"] == {"value": True}
    assert song.metronome
    assert wait(lambda: events[-1][1]["metronome"] is True)
    n = len(events)
    # Nothing changed: no new state event.
    time.sleep(0.3)
    assert len(events) == n
    # A change made in Live itself (the mouse) is reported too.
    song.tempo = 140.0
    assert wait(lambda: events[-1][1]["tempo"] == 140.0)


def test_errors_and_unknown_requests(live):
    h, song, c, script, events = live
    h.wait_link()
    r = h.request("track_flag", {"track": "Nope", "flag": "mute"})
    assert r == {"type": "response", "id": r["id"], "ok": False, "error": 'No track is named "Nope"'}
    r = h.request("freeze_track", {})
    assert r["unsupported"] is True
    # A Live API error (a bug) is reported, the script keeps going.
    song.tracks = None
    r = h.request("list", {"what": "tracks"})
    assert r["ok"] is False and "TypeError" in r["error"]
    song.tracks = []
    assert h.request("tempo", {"delta": 1})["ok"]


def test_disconnect_closes_the_link(live):
    h, song, c, script, events = live
    h.wait_link()
    assert wait(lambda: script._link.connected)
    script.disconnect()
    assert wait(lambda: not h.links)
