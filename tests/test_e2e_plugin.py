# End to end: the real KnobDeck `ableton` plugin (built from Knobdeck/knobdeck-plugins) over its
# stdio protocol, the fake hub, and this Remote Script ticking against the fake Live, as Live
# would run it. Skipped unless KNOBDECK_ABLETON_PLUGIN names the built plugin:
#
#   (cd ../knobdeck-plugins && cargo build -p ableton)
#   KNOBDECK_ABLETON_PLUGIN=../knobdeck-plugins/target/debug/ableton pytest tests/test_e2e_plugin.py

import os
import threading
import time

import pytest

import KnobDeck
from fake_hub import FakeHub
from fake_live import CInstance, Song, synth_track
from plugin_host import Plugin, connect

EXE = os.environ.get("KNOBDECK_ABLETON_PLUGIN")
pytestmark = pytest.mark.skipif(not EXE, reason="set KNOBDECK_ABLETON_PLUGIN to the built ableton plugin")


def state_of(plugin, instance, start=0, timeout=5.0, **want):
    """Wait for a `state` frame for `instance` with the wanted fields."""
    return plugin.wait_frame(
        lambda f: f.get("type") == "state" and f.get("instance_id") == instance and all(f.get(k) == v for k, v in want.items()),
        timeout,
        start,
    )


@pytest.fixture
def setup(monkeypatch):
    plugin = Plugin(os.path.abspath(EXE))
    hub = FakeHub()
    connect(plugin, hub)
    monkeypatch.setenv("KNOBDECK_COMPANION_DISCOVERY", hub.discovery)
    song = Song()
    synth_track(song)
    song.view.selected_track = song.tracks[0]
    yield plugin, hub, song
    plugin.stop()
    hub.close()


def start_live(song):
    script = KnobDeck.create_instance(CInstance(song))
    stop = []

    def main_thread():  # Live's main thread: update_display every 100 ms (faster here)
        while not stop:
            script.update_display()
            time.sleep(0.02)

    t = threading.Thread(target=main_thread, daemon=True)
    t.start()

    def quit_live():
        stop.append(1)
        t.join(2)
        script.disconnect()

    return quit_live


def test_plugin_and_remote_script(setup):
    plugin, hub, song = setup
    assert plugin.id.startswith("ableton-")
    plugin.hello(
        {
            "st": ("selected-track", {}),
            "m1": ("metronome", {}),
            "p1": ("play-stop", {}),
            "t1": ("tempo", {"step": "1"}),
            "v1": ("track-volume", {"track": "Drums", "step": "1"}),
            "mu": ("mute", {"track": "Drums"}),
            "d3": ("device-parameter", {"slot": "1", "bank": "follow"}),
            "nb": ("next-bank", {}),
            "ls": ("launch-scene", {"scene": "Drop"}),
        }
    )
    # Not linked yet: clear errors, and the status key says so.
    errors = plugin.invoke("u0", "undo")
    assert errors and "isn't linked" in errors[0]["message"], errors
    state_of(plugin, "st", label="Live offline")

    quit_live = start_live(song)
    try:
        hub.wait_link(10)
        # The script's first state reaches the keys.
        state_of(plugin, "st", label="1-Audio\n0.0 dB")
        state_of(plugin, "t1", state="120")
        state_of(plugin, "ls", label="Launch\nDrop")
        state_of(plugin, "d3", label="Cutoff\n10010 Hz", state="50")

        start = len(plugin.frames)
        assert not plugin.invoke("m1", "metronome")
        assert song.metronome is True
        state_of(plugin, "m1", start, state="on")

        start = len(plugin.frames)
        assert not plugin.invoke("p1", "play-stop")
        assert song.is_playing
        state_of(plugin, "p1", start, state="playing")

        start = len(plugin.frames)
        assert not plugin.invoke("t1", "tempo", {"step": "1", "delta": 3}, trigger="adjust")
        assert song.tempo == 123.0
        state_of(plugin, "t1", start, state="123")

        start = len(plugin.frames)
        assert not plugin.invoke("v1", "track-volume", {"track": "Drums", "step": "1", "delta": -6}, trigger="adjust")
        state_of(plugin, "v1", start, label="Drums\n-6.0 dB", state="-6")

        start = len(plugin.frames)
        assert not plugin.invoke("mu", "mute", {"track": "Drums"})
        assert song.tracks[2].mute
        state_of(plugin, "mu", start, state="on")

        # A change made in Live itself shows up on the keys.
        start = len(plugin.frames)
        song.tempo = 98.5
        state_of(plugin, "t1", start, state="98.5")

        # Device parameters: the dial turns Cutoff, the bank key moves the dials to bank 2.
        start = len(plugin.frames)
        assert not plugin.invoke("d3", "device-parameter", {"slot": "1", "bank": "follow", "step": "2", "delta": 5}, trigger="adjust")
        state_of(plugin, "d3", start, state="60")
        start = len(plugin.frames)
        assert not plugin.invoke("nb", "next-bank")
        state_of(plugin, "nb", start, label="Bank 2/2")
        state_of(plugin, "d3", start, label="Macro 9\n0.00", state="0")

        # Pickers and wheels come from the set.
        tracks = plugin.request("query", {"query_id": "tracks", "inputs": {}})
        assert tracks[0] == {"value": "selected", "label": "Selected track"}
        assert {"value": "A-Reverb", "label": "A-Reverb (return)"} in tracks
        scenes = plugin.request("query", {"query_id": "scenes", "inputs": {}})
        assert {"value": "#2", "label": "Scene 2"} in scenes
        slices = plugin.request("wheel", {"action_id": plugin.ids["scenes-wheel"], "instance_id": "w1", "inputs": {}})
        slices = slices if isinstance(slices, list) else slices.get("slices", [])
        assert [s["label"] for s in slices] == ["Intro", "Scene 2", "Drop"], slices
        assert not plugin.invoke("w1", "scenes-wheel", {"then": "launch"}, child="2")
        assert ("fire", "Drop") in song.log

        # Errors from Live's side come back as sentences.
        errors = plugin.invoke("a1", "arm", {"track": "A-Reverb"})
        assert errors and "can't be armed" in errors[0]["message"], errors
        errors = plugin.invoke("c1", "capture-midi")
        assert errors and "Nothing to capture" in errors[0]["message"], errors

        # Live quits: the status key goes offline.
        start = len(plugin.frames)
        quit_live()
        state_of(plugin, "st", start, timeout=10, label="Live offline")
    finally:
        quit_live()
