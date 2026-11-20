# A stand-in for Live's Python API (the parts of the Live Object Model the Remote Script uses),
# so the script runs under plain Python in tests. Modelled on Ableton's LOM reference: names,
# ranges and the parameter display texts follow what Live shows; the fader law is made up
# (Live's isn't published), but like Live's it is monotonic with 0.85 = 0 dB and 1.0 = +6 dB.

import math
import sys
import types


class Param:
    def __init__(self, name, value=0.0, lo=0.0, hi=1.0, quantized=False, items=None, fmt=None, parent=None):
        self.name, self._value, self.min, self.max = name, float(value), float(lo), float(hi)
        self.is_quantized = quantized
        self.value_items = items or []
        self.is_enabled = True
        self._fmt = fmt
        self.canonical_parent = parent

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        if not self.is_enabled:
            raise RuntimeError("parameter is not enabled")
        if v < self.min or v > self.max:
            raise ValueError("Invalid value")  # Live raises on out-of-range values
        self._value = float(v)

    def str_for_value(self, v):
        if self._fmt:
            return self._fmt(v)
        if self.is_quantized and self.value_items:
            return self.value_items[int(round(v))]
        return "{:.2f}".format(v)

    def __str__(self):
        return self.str_for_value(self._value)


def volume_text(v):
    if v <= 0.0:
        return "-inf dB"
    # Monotonic, 0.85 -> 0 dB, 1.0 -> +6 dB.
    if v >= 0.85:
        db = (v - 0.85) / 0.15 * 6.0
    else:
        db = 40.0 * math.log10(v / 0.85)
    return "{:.1f} dB".format(db)


def pan_text(v):
    n = int(round(v * 50))
    return "C" if n == 0 else "{}{}".format(abs(n), "L" if n < 0 else "R")


class Mixer:
    def __init__(self):
        self.volume = Param("Track Volume", 0.85, 0.0, 1.0, fmt=volume_text)
        self.panning = Param("Track Panning", 0.0, -1.0, 1.0, fmt=pan_text)


class Device:
    def __init__(self, name, params, parent=None):
        self.name = name
        self.class_name = name
        self.canonical_parent = parent
        self.parameters = [Param("Device On", 1.0, 0.0, 1.0, quantized=True, items=["Off", "On"])] + params


class TrackView:
    def __init__(self):
        self.selected_device = None


class Track:
    def __init__(self, name, armable=True, master=False):
        self.name = name
        self._mute = self._solo = self._arm = False
        self.can_be_armed = armable
        self._master = master
        self.mixer_device = Mixer()
        self.devices = []
        self.view = TrackView()

    def _no_master(self):
        if self._master:
            raise RuntimeError("Master track has no mute/solo")

    @property
    def mute(self):
        self._no_master()
        return self._mute

    @mute.setter
    def mute(self, v):
        self._no_master()
        self._mute = bool(v)

    @property
    def solo(self):
        self._no_master()
        return self._solo

    @solo.setter
    def solo(self, v):
        self._no_master()
        self._solo = bool(v)

    @property
    def arm(self):
        if not self.can_be_armed:
            raise RuntimeError("Track can't be armed")
        return self._arm

    @arm.setter
    def arm(self, v):
        if not self.can_be_armed:
            raise RuntimeError("Track can't be armed")
        self._arm = bool(v)

    def add_device(self, device):
        device.canonical_parent = self
        self.devices.append(device)
        self.view.selected_device = device
        return device


class Scene:
    def __init__(self, name, song):
        self.name = name
        self._song = song
        self.fired = 0

    def fire(self):
        self.fired += 1
        self._song.log.append(("fire", self.name))

    def fire_as_selected(self, force_legato=False):
        self.fire()


class SongView:
    def __init__(self):
        self.selected_track = None
        self.selected_scene = None
        self.selected_parameter = None


class Song:
    def __init__(self, tracks=("1-Audio", "2-MIDI", "Drums"), returns=("A-Reverb", "B-Delay"), scenes=("Intro", "", "Drop")):
        self.log = []
        self.is_playing = False
        self.record_mode = False
        self.session_record = False
        self.arrangement_overdub = False
        self.metronome = False
        self.loop = False
        self.punch_in = False
        self.punch_out = False
        self.session_automation_record = False
        self._tempo = 120.0
        self.can_undo = True
        self.can_redo = False
        self.can_capture_midi = False
        self.can_jump_to_prev_cue = False
        self.can_jump_to_next_cue = True
        self.cues = []
        self.tracks = [Track(n) for n in tracks]
        self.return_tracks = [Track(n, armable=False) for n in returns]
        self.master_track = Track("Master", armable=False, master=True)
        self.scenes = [Scene(n, self) for n in scenes]
        self.view = SongView()
        self.view.selected_track = self.tracks[0] if self.tracks else None
        self.view.selected_scene = self.scenes[0] if self.scenes else None
        self.appointed_device = None

    @property
    def tempo(self):
        return self._tempo

    @tempo.setter
    def tempo(self, v):
        if not 20.0 <= v <= 999.0:
            raise ValueError("Invalid tempo")
        self._tempo = float(v)

    def start_playing(self):
        self.is_playing = True
        self.log.append("start_playing")

    def continue_playing(self):
        self.is_playing = True
        self.log.append("continue_playing")

    def stop_playing(self):
        self.is_playing = False
        self.log.append("stop_playing")

    def tap_tempo(self):
        self.log.append("tap_tempo")
        self._tempo = 128.0

    def undo(self):
        self.log.append("undo")
        self.can_redo = True

    def redo(self):
        self.log.append("redo")

    def jump_to_prev_cue(self):
        self.log.append("jump_to_prev_cue")

    def jump_to_next_cue(self):
        self.log.append("jump_to_next_cue")
        self.can_jump_to_prev_cue = True

    def set_or_delete_cue(self):
        self.log.append("set_or_delete_cue")

    def capture_midi(self, destination=0):
        self.log.append("capture_midi")

    def stop_all_clips(self, quantized=True):
        self.log.append("stop_all_clips")


class CInstance:
    """The object Live hands create_instance()."""

    def __init__(self, song):
        self._song = song
        self.messages = []
        self.logs = []

    def song(self):
        return self._song

    def show_message(self, m):
        self.messages.append(m)

    def log_message(self, m):
        self.logs.append(m)


class _App:
    def get_major_version(self):
        return 12

    def get_minor_version(self):
        return 1

    def get_bugfix_version(self):
        return 5


def install():
    """Put a fake `Live` module in sys.modules (Live.Application.get_application())."""
    live = types.ModuleType("Live")
    live.Application = types.SimpleNamespace(get_application=lambda: _App())
    sys.modules["Live"] = live
    return live


def synth_track(song, name="Bass"):
    """A MIDI track with a 12-parameter synth on it, selected and appointed."""
    t = Track(name)
    song.tracks.append(t)
    params = [Param("Cutoff", 0.5, 0.0, 1.0, fmt=lambda v: "{:.0f} Hz".format(20 + v * 19980))]
    params.append(Param("Wave", 0.0, 0.0, 3.0, quantized=True, items=["Sine", "Saw", "Square", "Noise"]))
    params += [Param("Macro {}".format(i), 0.0, 0.0, 127.0) for i in range(3, 13)]
    d = t.add_device(Device("Operator", params))
    song.view.selected_track = t
    song.appointed_device = d
    return t, d
