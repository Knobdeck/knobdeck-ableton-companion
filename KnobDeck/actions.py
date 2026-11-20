# What each KnobDeck request does, with Live's Python API only (the Live Object Model: the Song,
# its tracks, scenes, cue points and devices). Never key events. Every function here runs on
# Live's main thread, from the Remote Script's update_display tick.
#
# Request ids and their parameters are a contract with the `ableton` plugin in
# Knobdeck/knobdeck-plugins: add, never rename.
#
# Python 3.7 compatible (Live 11's Python).

import math

PARAMS_PER_BANK = 8
MAX_DEVICE_PARAMS = 128  # parameters sent in the state (16 banks)
MIN_DB = -70.0  # the fader's floor on the KnobDeck dial; below it is -inf
MAX_TEMPO, MIN_TEMPO = 999.0, 20.0
WHEEL_ITEMS = 16

# Song switches a "toggle" request may flip: request name -> Song property.
SWITCHES = {
    "record": "record_mode",
    "session_record": "session_record",
    "overdub": "arrangement_overdub",
    "metronome": "metronome",
    "loop": "loop",
    "punch_in": "punch_in",
    "punch_out": "punch_out",
    "automation_arm": "session_automation_record",
}


class Failed(Exception):
    """A message for the user (shown on the KnobDeck key)."""


class Unsupported(Exception):
    """No such request (an older Remote Script than the plugin)."""


# ── reading values ─────────────────────────────────────────────────────────


def _r(v, places=4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, places)


def display(param, value=None):
    """Live's own text for a parameter value ("-6.0 dB", "12L", "2.40 kHz")."""
    v = param.value if value is None else value
    try:
        return str(param.str_for_value(v))
    except Exception:
        try:
            return str(param)
        except Exception:
            return str(_r(v))


def parse_db(text):
    """-6.0 from "-6.0 dB"; -inf from "-inf dB"; None when it isn't a level."""
    t = (text or "").strip().lower().replace("db", "").strip()
    if "inf" in t:
        return float("-inf")
    try:
        return float(t)
    except ValueError:
        return None


def db_of(param, value=None):
    return parse_db(display(param, value))


def _first_value(param, db, above):
    """The lowest value Live shows as >= db (or > db when `above`)."""
    lo, hi = float(param.min), float(param.max)
    for _ in range(40):
        mid = (lo + hi) / 2
        d = db_of(param, mid)
        if d is None or d < db or (above and d == db):
            lo = mid
        else:
            hi = mid
    return hi


def value_for_db(param, db):
    """The parameter value Live shows as `db`, in the middle of the values that round to it
    (bisection over Live's own display: its fader law isn't published, but its text is
    monotonic)."""
    lo, hi = float(param.min), float(param.max)
    if db <= MIN_DB:
        return lo
    top = db_of(param, hi)
    if top is not None and db >= top:
        return hi
    first, past = _first_value(param, db, False), _first_value(param, db, True)
    return (first + past) / 2 if past > first and db_of(param, first) == db_of(param, (first + past) / 2) else first


def level(param):
    """A volume parameter for the state: {value, db, display}."""
    d = db_of(param)
    return {"value": _r(param.value), "db": _r(d, 2), "display": display(param)}


def pan(param):
    """A pan parameter for the state: {value (-50…50, as Live shows it), display}."""
    return {"value": _r(param.value * 50.0, 2), "display": display(param)}


def _flag(track, name):
    try:
        if name == "arm" and not track.can_be_armed:
            return None
        return bool(getattr(track, name))
    except Exception:
        return None


def _track_kind(song, track):
    if track == song.master_track:
        return "master"
    if any(track == t for t in song.return_tracks):
        return "return"
    return "track"


def _index(seq, item):
    for i, x in enumerate(seq):
        if x == item:
            return i
    return None


def _device(song):
    """The device KnobDeck controls: Live's appointed device (the blue hand), else the selected
    track's selected device."""
    d = getattr(song, "appointed_device", None)
    if d is None:
        try:
            d = song.view.selected_track.view.selected_device
        except Exception:
            d = None
    return d


def _device_track(song, device):
    t = device
    for _ in range(8):  # device -> chain -> rack device -> … -> track
        t = getattr(t, "canonical_parent", None)
        if t is None:
            return None
        if hasattr(t, "can_be_armed") and hasattr(t, "devices"):  # a Track (a Chain has no arm)
            return t
    return None


def param_state(param):
    lo, hi = float(param.min), float(param.max)
    span = hi - lo
    pct = (float(param.value) - lo) / span * 100.0 if span else 0.0
    out = {
        "name": str(param.name),
        "value": _r(pct, 2),
        "display": display(param),
        "quantized": bool(getattr(param, "is_quantized", False)),
    }
    if not getattr(param, "is_enabled", True):
        out["enabled"] = False
    return out


def track_state(track, armable):
    m = track.mixer_device
    out = {"name": str(track.name), "mute": _flag(track, "mute"), "solo": _flag(track, "solo")}
    if armable:
        out["arm"] = _flag(track, "arm")
    out["volume"] = level(m.volume)
    out["pan"] = pan(m.panning)
    return out


def snapshot(song):
    """Everything the KnobDeck keys show. Sent as a `state` event whenever it changes."""
    view = song.view
    tracks = list(song.tracks)
    returns = list(song.return_tracks)
    s = {
        "playing": bool(song.is_playing),
        "tempo": _r(song.tempo, 2),
        "can_undo": bool(getattr(song, "can_undo", True)),
        "can_redo": bool(getattr(song, "can_redo", True)),
        "can_capture_midi": bool(getattr(song, "can_capture_midi", False)),
        "master": {"volume": level(song.master_track.mixer_device.volume), "pan": pan(song.master_track.mixer_device.panning)},
        "tracks": [track_state(t, True) for t in tracks],
        "returns": [track_state(t, False) for t in returns],
        "scenes": [str(sc.name) for sc in song.scenes],
    }
    for name, prop in SWITCHES.items():
        try:
            s[name] = bool(getattr(song, prop))
        except Exception:
            s[name] = None
    t = view.selected_track
    if t is not None:
        kind = _track_kind(song, t)
        entry = {"name": str(t.name), "kind": kind, "volume": level(t.mixer_device.volume), "pan": pan(t.mixer_device.panning)}
        entry["index"] = _index(tracks if kind == "track" else returns, t) if kind != "master" else None
        if kind != "master":
            entry.update(mute=_flag(t, "mute"), solo=_flag(t, "solo"), arm=_flag(t, "arm"))
        s["selected_track"] = entry
    sc = view.selected_scene
    if sc is not None:
        s["selected_scene"] = {"name": str(sc.name), "index": _index(song.scenes, sc)}
    d = _device(song)
    if d is not None:
        params = list(d.parameters)
        dt = _device_track(song, d)
        s["device"] = {
            "name": str(d.name),
            "track": str(dt.name) if dt is not None else None,
            "on": bool(params[0].value) if params else None,
            "params": [param_state(p) for p in params[1 : 1 + MAX_DEVICE_PARAMS]],
        }
    p = getattr(view, "selected_parameter", None)
    if p is not None:
        s["selected_parameter"] = param_state(p)
    return s


# ── finding things ─────────────────────────────────────────────────────────


def find_track(song, pick, allow_master=True):
    """A track from a picker value: "selected" / "" (the selected track), "master", "#3" (the
    third track), or a track's or return track's name."""
    pick = (pick or "selected").strip()
    if pick == "selected":
        t = song.view.selected_track
        if t is None:
            raise Failed("No track is selected")
        if t == song.master_track and not allow_master:
            raise Failed("The Master track has no such control")
        return t
    if pick == "master":
        if not allow_master:
            raise Failed("The Master track has no such control")
        return song.master_track
    tracks = list(song.tracks)
    if pick.startswith("#"):
        try:
            return tracks[int(pick[1:]) - 1]
        except (ValueError, IndexError):
            raise Failed("The set has no track {}".format(pick))
    for t in tracks + list(song.return_tracks):
        if str(t.name) == pick:
            return t
    raise Failed('No track is named "{}"'.format(pick))


def find_scene(song, pick):
    pick = (pick or "selected").strip()
    scenes = list(song.scenes)
    if pick == "selected":
        sc = song.view.selected_scene
        if sc is None:
            raise Failed("No scene is selected")
        return sc
    if pick.startswith("#"):
        try:
            return scenes[int(pick[1:]) - 1]
        except (ValueError, IndexError):
            raise Failed("The set has no scene {}".format(pick))
    for sc in scenes:
        if str(sc.name) == pick:
            return sc
    raise Failed('No scene is named "{}"'.format(pick))


def _num(params, key):
    v = params.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise Failed("{} must be a number".format(key))
    if math.isnan(f) or math.isinf(f):
        raise Failed("{} must be a number".format(key))
    return f


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _set(param, value):
    if not getattr(param, "is_enabled", True):
        raise Failed("{} is controlled by a macro, modulation or automation".format(param.name))
    param.value = _clamp(value, float(param.min), float(param.max))


def _new_bool(params, current):
    v = params.get("value", "toggle")
    if isinstance(v, bool):
        return v
    if v in ("on", "true", 1):
        return True
    if v in ("off", "false", 0):
        return False
    return not current


# ── requests ───────────────────────────────────────────────────────────────


def a_state(song, params):
    return snapshot(song)


def a_transport(song, params):
    op = params.get("op", "toggle")
    if op == "toggle":
        op = "stop" if song.is_playing else "play"
    if op == "play":
        song.start_playing()
    elif op == "continue":
        song.continue_playing()
    elif op == "stop":
        song.stop_playing()
    else:
        raise Failed("Unknown transport command: {}".format(op))
    return {"playing": op != "stop"}


def a_toggle(song, params):
    name = params.get("switch")
    prop = SWITCHES.get(name)
    if prop is None:
        raise Failed("Unknown switch: {}".format(name))
    try:
        current = bool(getattr(song, prop))
    except AttributeError:
        raise Failed("This version of Live has no {}".format(name.replace("_", " ")))
    new = _new_bool(params, current)
    setattr(song, prop, new)
    return {"value": new}


def a_tap_tempo(song, params):
    song.tap_tempo()
    return {"tempo": _r(song.tempo, 2)}


def a_tempo(song, params):
    v, d = _num(params, "value"), _num(params, "delta")
    if v is None and d is None:
        raise Failed("tempo needs a value or a delta")
    new = _clamp(v if v is not None else float(song.tempo) + d, MIN_TEMPO, MAX_TEMPO)
    song.tempo = new
    return {"value": _r(song.tempo, 2)}


def a_mixer(song, params):
    """Volume (dB) or pan (-50…50) of a track: {track, control: volume|pan, value | delta}."""
    t = find_track(song, params.get("track"))
    control = params.get("control", "volume")
    v, d = _num(params, "value"), _num(params, "delta")
    if v is None and d is None:
        raise Failed("{} needs a value or a delta".format(control))
    if control == "volume":
        p = t.mixer_device.volume
        if v is None:
            now = db_of(p)
            now = MIN_DB if now is None or now < MIN_DB else now
            v = now + d
        _set(p, value_for_db(p, v))
        out = level(p)
    elif control == "pan":
        p = t.mixer_device.panning
        new = (v if v is not None else float(p.value) * 50.0 + d) / 50.0
        _set(p, round(new * 50.0) / 50.0)
        out = pan(p)
    else:
        raise Failed("Unknown mixer control: {}".format(control))
    out["track"] = str(t.name)
    return out


def a_track_flag(song, params):
    flag = params.get("flag")
    if flag not in ("mute", "solo", "arm"):
        raise Failed("Unknown track switch: {}".format(flag))
    t = find_track(song, params.get("track"), allow_master=False)
    if flag == "arm" and not t.can_be_armed:
        raise Failed("{} can't be armed".format(t.name))
    new = _new_bool(params, bool(getattr(t, flag)))
    setattr(t, flag, new)
    return {"value": new, "track": str(t.name)}


def a_launch_scene(song, params):
    sc = find_scene(song, params.get("scene"))
    if (params.get("scene") or "selected") == "selected" and hasattr(sc, "fire_as_selected"):
        sc.fire_as_selected()  # moves the selection on when Live's "Select Next Scene on Launch" is on
    else:
        sc.fire()
    return {"scene": str(sc.name)}


def a_stop_all_clips(song, params):
    song.stop_all_clips()
    return {}


def a_select(song, params):
    """Select a track or scene: {what: track|scene, index (0-based) | delta | name}."""
    what = params.get("what", "track")
    if what == "track":
        items = list(song.tracks) + list(song.return_tracks)
        current = song.view.selected_track
    elif what == "scene":
        items = list(song.scenes)
        current = song.view.selected_scene
    else:
        raise Failed("Unknown selection: {}".format(what))
    if not items:
        raise Failed("The set has no {}s".format(what))
    i, d, name = _num(params, "index"), _num(params, "delta"), params.get("name")
    if name is not None:
        target = find_track(song, name) if what == "track" else find_scene(song, name)
    else:
        if i is None:
            now = _index(items, current)
            now = 0 if now is None else now
            if d is None:
                raise Failed("select needs an index, a delta or a name")
            i = now + (1 if d > 0 else -1 if d < 0 else 0)
        target = items[int(_clamp(int(i), 0, len(items) - 1))]
    if what == "track":
        song.view.selected_track = target
    else:
        song.view.selected_scene = target
    return {"index": _index(items, target), "name": str(target.name)}


def a_list(song, params):
    """Picker options: {what: tracks|scenes|mixer_tracks}."""
    what = params.get("what", "tracks")
    if what == "scenes":
        return {"items": [{"value": str(sc.name) or "#{}".format(i + 1), "label": scene_label(i, sc)} for i, sc in enumerate(song.scenes)]}
    items = [{"value": str(t.name), "label": str(t.name)} for t in song.tracks]
    items += [{"value": str(t.name), "label": "{} (return)".format(t.name)} for t in song.return_tracks]
    return {"items": items}


def scene_label(i, scene):
    name = str(scene.name).strip()
    return "{}. {}".format(i + 1, name) if name else "Scene {}".format(i + 1)


def _around(items, current, n=WHEEL_ITEMS):
    """Up to n entries around the current one, as (index, item)."""
    i = _index(items, current) or 0
    start = max(0, min(i - n // 2, len(items) - n))
    return list(enumerate(items))[start : start + n]


def a_wheel(song, params):
    what = params.get("what", "tracks")
    if what == "scenes":
        items = list(song.scenes)
        cur = song.view.selected_scene
    else:
        items = list(song.tracks) + list(song.return_tracks)
        cur = song.view.selected_track
    return {"items": [{"index": i, "name": str(x.name), "selected": x == cur} for i, x in _around(items, cur)]}


def a_cue(song, params):
    to = params.get("to")
    if to == "previous":
        if not getattr(song, "can_jump_to_prev_cue", True):
            raise Failed("No locator before the play position")
        song.jump_to_prev_cue()
    elif to == "next":
        if not getattr(song, "can_jump_to_next_cue", True):
            raise Failed("No locator after the play position")
        song.jump_to_next_cue()
    elif to == "toggle":
        song.set_or_delete_cue()
    else:
        raise Failed("Unknown locator command: {}".format(to))
    return {}


def a_undo(song, params):
    if not getattr(song, "can_undo", True):
        raise Failed("Nothing to undo")
    song.undo()
    return {}


def a_redo(song, params):
    if not getattr(song, "can_redo", True):
        raise Failed("Nothing to redo")
    song.redo()
    return {}


def a_capture_midi(song, params):
    if not hasattr(song, "capture_midi"):
        raise Failed("Capture MIDI needs Live 10 or newer")
    if not getattr(song, "can_capture_midi", True):
        raise Failed("Nothing to capture: play some MIDI into an armed or monitored track first")
    song.capture_midi()
    return {}


def _target_param(song, params):
    if params.get("selected"):
        p = getattr(song.view, "selected_parameter", None)
        if p is None:
            raise Failed("No parameter is selected in Live: click a control first")
        return p
    d = _device(song)
    if d is None:
        raise Failed("No device is selected in Live")
    i = _num(params, "index")
    if i is None:
        raise Failed("device_param needs an index")
    ps = list(d.parameters)[1:]
    i = int(i)
    if i < 0 or i >= len(ps):
        raise Failed("{} has no parameter {}".format(d.name, i + 1))
    return ps[i]


def a_device_param(song, params):
    """A device parameter in percent of its range: {index (0-based, after Device On) | selected,
    value (0–100) | delta (percent; a quantized parameter moves one step per tick)}."""
    p = _target_param(song, params)
    v, d = _num(params, "value"), _num(params, "delta")
    if v is None and d is None:
        raise Failed("device_param needs a value or a delta")
    lo, hi = float(p.min), float(p.max)
    if getattr(p, "is_quantized", False):
        if v is not None:
            new = round(lo + (hi - lo) * _clamp(v, 0.0, 100.0) / 100.0)
        else:
            new = float(p.value) + (1 if d > 0 else -1 if d < 0 else 0)
    else:
        new = (lo + (hi - lo) * v / 100.0) if v is not None else float(p.value) + (hi - lo) * d / 100.0
    _set(p, new)
    return param_state(p)


def a_device_on(song, params):
    d = _device(song)
    if d is None:
        raise Failed("No device is selected in Live")
    ps = list(d.parameters)
    if not ps:
        raise Failed("{} can't be switched off".format(d.name))
    new = _new_bool(params, bool(ps[0].value))
    _set(ps[0], 1.0 if new else 0.0)
    return {"value": new}


ACTIONS = {
    "state": a_state,
    "transport": a_transport,
    "toggle": a_toggle,
    "tap_tempo": a_tap_tempo,
    "tempo": a_tempo,
    "mixer": a_mixer,
    "track_flag": a_track_flag,
    "launch_scene": a_launch_scene,
    "stop_all_clips": a_stop_all_clips,
    "select": a_select,
    "list": a_list,
    "wheel": a_wheel,
    "cue": a_cue,
    "undo": a_undo,
    "redo": a_redo,
    "capture_midi": a_capture_midi,
    "device_param": a_device_param,
    "device_on": a_device_on,
}


def run(song, action, params):
    fn = ACTIONS.get(action)
    if fn is None:
        raise Unsupported(action)
    return fn(song, params or {})
