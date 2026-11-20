# Every request (KnobDeck/actions.py) against the fake Live Object Model.
import pytest

from fake_live import Song, synth_track
from KnobDeck import actions
from KnobDeck.actions import Failed, run


@pytest.fixture
def song():
    return Song()


def test_transport(song):
    assert run(song, "transport", {"op": "toggle"}) == {"playing": True}
    assert song.is_playing and song.log == ["start_playing"]
    assert run(song, "transport", {"op": "toggle"}) == {"playing": False}
    run(song, "transport", {"op": "continue"})
    run(song, "transport", {"op": "stop"})
    assert song.log == ["start_playing", "stop_playing", "continue_playing", "stop_playing"]
    with pytest.raises(Failed):
        run(song, "transport", {"op": "rewind"})


@pytest.mark.parametrize("switch,prop", sorted(actions.SWITCHES.items()))
def test_switches(song, switch, prop):
    assert run(song, "toggle", {"switch": switch}) == {"value": True}
    assert getattr(song, prop) is True
    assert run(song, "toggle", {"switch": switch, "value": True}) == {"value": True}
    assert run(song, "toggle", {"switch": switch}) == {"value": False}
    assert run(song, "toggle", {"switch": switch, "value": "on"}) == {"value": True}


def test_unknown_switch_and_old_live(song):
    with pytest.raises(Failed):
        run(song, "toggle", {"switch": "warp"})
    del song.punch_in
    with pytest.raises(Failed, match="no punch in"):
        run(song, "toggle", {"switch": "punch_in"})


def test_tempo(song):
    assert run(song, "tempo", {"delta": 2.5}) == {"value": 122.5}
    assert run(song, "tempo", {"value": 90}) == {"value": 90.0}
    assert run(song, "tempo", {"value": 5000}) == {"value": 999.0}
    assert run(song, "tempo", {"delta": -10000}) == {"value": 20.0}
    assert run(song, "tap_tempo", {}) == {"tempo": 128.0}
    with pytest.raises(Failed):
        run(song, "tempo", {})
    with pytest.raises(Failed):
        run(song, "tempo", {"value": "fast"})


def test_volume_in_db(song):
    r = run(song, "mixer", {"track": "master", "control": "volume", "delta": -6})
    assert r["track"] == "Master" and r["display"] == "-6.0 dB" and r["db"] == -6.0
    r = run(song, "mixer", {"track": "Drums", "control": "volume", "value": 3})
    assert r["display"] == "3.0 dB" and song.tracks[2].mixer_device.volume.value == pytest.approx(0.925, abs=1e-4)
    assert run(song, "mixer", {"track": "Drums", "control": "volume", "value": 20})["display"] == "6.0 dB"
    r = run(song, "mixer", {"track": "Drums", "control": "volume", "value": -80})
    assert r["display"] == "-inf dB" and r["db"] is None
    # Turning up from -inf starts at the dial's floor.
    assert run(song, "mixer", {"track": "Drums", "control": "volume", "delta": 1})["db"] == pytest.approx(-69.0, abs=0.06)


def test_pan(song):
    r = run(song, "mixer", {"control": "pan", "delta": -10})  # the selected track
    assert (r["track"], r["value"], r["display"]) == ("1-Audio", -10.0, "10L")
    assert run(song, "mixer", {"control": "pan", "value": 80})["display"] == "50R"
    assert run(song, "mixer", {"control": "pan", "value": 0})["display"] == "C"


def test_picking_tracks(song):
    assert actions.find_track(song, "").name == "1-Audio"
    assert actions.find_track(song, "#3").name == "Drums"
    assert actions.find_track(song, "B-Delay").name == "B-Delay"
    assert actions.find_track(song, "master").name == "Master"
    with pytest.raises(Failed, match='No track is named "Nope"'):
        actions.find_track(song, "Nope")
    with pytest.raises(Failed, match="no track #9"):
        actions.find_track(song, "#9")
    song.view.selected_track = None
    with pytest.raises(Failed, match="No track is selected"):
        actions.find_track(song, "selected")


def test_track_flags(song):
    assert run(song, "track_flag", {"track": "Drums", "flag": "mute"}) == {"value": True, "track": "Drums"}
    assert song.tracks[2].mute
    assert run(song, "track_flag", {"track": "Drums", "flag": "solo", "value": True})["value"] is True
    assert run(song, "track_flag", {"flag": "arm"})["track"] == "1-Audio"
    assert song.tracks[0].arm
    assert run(song, "track_flag", {"track": "A-Reverb", "flag": "mute"})["value"] is True
    with pytest.raises(Failed, match="can't be armed"):
        run(song, "track_flag", {"track": "A-Reverb", "flag": "arm"})
    with pytest.raises(Failed, match="Master"):
        run(song, "track_flag", {"track": "master", "flag": "mute"})
    with pytest.raises(Failed):
        run(song, "track_flag", {"flag": "freeze"})


def test_scenes(song):
    run(song, "launch_scene", {})
    run(song, "launch_scene", {"scene": "Drop"})
    run(song, "launch_scene", {"scene": "#2"})
    assert song.log == [("fire", "Intro"), ("fire", "Drop"), ("fire", "")]
    with pytest.raises(Failed):
        run(song, "launch_scene", {"scene": "Outro"})
    run(song, "stop_all_clips", {})
    assert song.log[-1] == "stop_all_clips"
    assert run(song, "list", {"what": "scenes"})["items"] == [
        {"value": "Intro", "label": "1. Intro"},
        {"value": "#2", "label": "Scene 2"},
        {"value": "Drop", "label": "3. Drop"},
    ]


def test_selection(song):
    assert run(song, "select", {"what": "track", "delta": 1}) == {"index": 1, "name": "2-MIDI"}
    assert run(song, "select", {"what": "track", "delta": 5}) == {"index": 2, "name": "Drums"}
    assert run(song, "select", {"what": "track", "index": 99}) == {"index": 4, "name": "B-Delay"}
    assert run(song, "select", {"what": "track", "index": -3}) == {"index": 0, "name": "1-Audio"}
    assert run(song, "select", {"what": "track", "name": "A-Reverb"})["index"] == 3
    assert song.view.selected_track.name == "A-Reverb"
    assert run(song, "select", {"what": "scene", "delta": -1}) == {"index": 0, "name": "Intro"}
    assert run(song, "select", {"what": "scene", "index": 2})["name"] == "Drop"
    assert song.view.selected_scene.name == "Drop"
    with pytest.raises(Failed):
        run(song, "select", {"what": "scene"})


def test_lists_and_wheels(song):
    items = run(song, "list", {"what": "tracks"})["items"]
    assert items[0] == {"value": "1-Audio", "label": "1-Audio"}
    assert items[-1] == {"value": "B-Delay", "label": "B-Delay (return)"}
    song.tracks += [type(song.tracks[0])("T{}".format(i)) for i in range(30)]
    song.view.selected_track = song.tracks[20]
    w = run(song, "wheel", {"what": "tracks"})["items"]
    assert len(w) == actions.WHEEL_ITEMS
    assert [x for x in w if x["selected"]] == [{"index": 20, "name": "T17", "selected": True}]
    assert w[0]["index"] == 12


def test_cues_undo_capture(song):
    with pytest.raises(Failed, match="No locator before"):
        run(song, "cue", {"to": "previous"})
    run(song, "cue", {"to": "next"})
    run(song, "cue", {"to": "previous"})
    run(song, "cue", {"to": "toggle"})
    run(song, "undo", {})
    run(song, "redo", {})
    with pytest.raises(Failed, match="Nothing to capture"):
        run(song, "capture_midi", {})
    song.can_capture_midi = True
    run(song, "capture_midi", {})
    assert song.log == ["jump_to_next_cue", "jump_to_prev_cue", "set_or_delete_cue", "undo", "redo", "capture_midi"]
    song.can_undo = False
    with pytest.raises(Failed, match="Nothing to undo"):
        run(song, "undo", {})


def test_device_parameters(song):
    with pytest.raises(Failed, match="No device"):
        run(song, "device_param", {"index": 0, "delta": 1})
    t, d = synth_track(song)
    r = run(song, "device_param", {"index": 0, "delta": 10})
    assert (r["name"], r["value"], r["display"]) == ("Cutoff", 60.0, "12008 Hz")
    assert run(song, "device_param", {"index": 0, "value": 250})["value"] == 100.0
    # A quantized parameter moves one step per tick, and a drag picks the nearest item.
    assert run(song, "device_param", {"index": 1, "delta": 3})["display"] == "Saw"
    assert run(song, "device_param", {"index": 1, "delta": -1})["display"] == "Sine"
    assert run(song, "device_param", {"index": 1, "value": 70})["display"] == "Square"
    assert run(song, "device_param", {"index": 11, "value": 50})["display"] == "63.50"
    with pytest.raises(Failed, match="has no parameter 13"):
        run(song, "device_param", {"index": 12, "value": 1})
    d.parameters[3].is_enabled = False
    with pytest.raises(Failed, match="macro"):
        run(song, "device_param", {"index": 2, "value": 1})
    with pytest.raises(Failed, match="No parameter is selected"):
        run(song, "device_param", {"selected": True, "delta": 1})
    song.view.selected_parameter = d.parameters[1]
    assert run(song, "device_param", {"selected": True, "value": 0})["display"] == "20 Hz"
    assert run(song, "device_on", {}) == {"value": False}
    assert d.parameters[0].value == 0.0
    assert run(song, "device_on", {"value": "on"}) == {"value": True}


def test_device_falls_back_to_the_selected_device(song):
    t, d = synth_track(song)
    song.appointed_device = None
    assert run(song, "device_param", {"index": 0, "value": 0})["display"] == "20 Hz"


def test_snapshot(song):
    t, d = synth_track(song)
    song.tracks[2].mute = True
    s = actions.snapshot(song)
    assert s["playing"] is False and s["tempo"] == 120.0 and s["metronome"] is False and s["record"] is False
    assert s["master"]["volume"] == {"value": 0.85, "db": 0.0, "display": "0.0 dB"}
    assert s["master"]["pan"] == {"value": 0.0, "display": "C"}
    vol0, centre = {"value": 0.85, "db": 0.0, "display": "0.0 dB"}, {"value": 0.0, "display": "C"}
    assert s["tracks"][2] == {"name": "Drums", "mute": True, "solo": False, "arm": False, "volume": vol0, "pan": centre}
    assert s["returns"][0] == {"name": "A-Reverb", "mute": False, "solo": False, "volume": vol0, "pan": centre}
    assert s["scenes"] == ["Intro", "", "Drop"]
    sel = s["selected_track"]
    assert (sel["name"], sel["kind"], sel["index"], sel["arm"]) == ("Bass", "track", 3, False)
    assert s["selected_scene"] == {"name": "Intro", "index": 0}
    dev = s["device"]
    assert (dev["name"], dev["track"], dev["on"], len(dev["params"])) == ("Operator", "Bass", True, 12)
    assert dev["params"][1] == {"name": "Wave", "value": 0.0, "display": "Sine", "quantized": True}
    assert "selected_parameter" not in s
    song.view.selected_track = song.master_track
    s = actions.snapshot(song)
    assert s["selected_track"]["kind"] == "master" and "mute" not in s["selected_track"]
    import json

    json.dumps(s, allow_nan=False)  # always valid JSON


def test_unknown_request(song):
    with pytest.raises(actions.Unsupported):
        run(song, "freeze_track", {})
