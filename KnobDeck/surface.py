# The KnobDeck control surface: what Live loads when the user picks "KnobDeck" as a Control
# Surface (Settings ▸ Link, Tempo & MIDI). It needs no MIDI ports. Live calls update_display about
# every 100 ms on its main thread; each call moves the hub link on (hub.py, non-blocking), runs
# the requests that arrived with Live's API (actions.py) and, when what the keys show changed,
# sends a `state` event.
#
# This is the plain Remote Script interface (create_instance(c_instance) -> an object Live calls),
# not Ableton's _Framework, whose internals change between Live versions.
#
# Python 3.7 compatible (Live 11's Python).

import os
import uuid

from . import actions, hub
from .version import VERSION

STATE_EVERY = 2  # ticks (~200 ms) between state comparisons
# Stable while Live runs (the module stays loaded when Live re-creates the surface for another
# set), so a reconnect replaces the old link in KnobDeck instead of adding one.
INSTANCE = "{}-{}".format(uuid.uuid4().hex[:12], os.getpid())


def _live_version():
    try:
        import Live

        app = Live.Application.get_application()
        return "{}.{}.{}".format(app.get_major_version(), app.get_minor_version(), app.get_bugfix_version())
    except Exception:
        return None


class KnobDeck(object):
    def __init__(self, c_instance, link=None):
        self._c = c_instance
        version = _live_version()
        self._link = link or hub.Link(
            plugin="ableton-{}".format(hub.os_name()),
            instance=INSTANCE,
            app="ableton",
            app_version=version,
            name="Ableton Live {}".format(".".join(version.split(".")[:2])) if version else "Ableton Live",
            version=VERSION,
            features=["state"],
            log=self.log_message,
        )
        self._last_state = None
        self._ticks = 0
        self._state_due = True
        self._was_linked = False
        self._last_status = None

    # ── helpers ─────────────────────────────────────────────────────────

    def song(self):
        return self._c.song()

    def log_message(self, message):
        try:
            self._c.log_message("KnobDeck: " + str(message))
        except Exception:
            pass

    def show_message(self, message):
        try:
            self._c.show_message(str(message))
        except Exception:
            pass

    # ── the tick ────────────────────────────────────────────────────────

    def tick(self):
        try:
            requests = self._link.poll()
        except Exception as e:  # never let a bug stop Live's display loop
            self.log_message("link error: {!r}".format(e))
            return
        linked = self._link.connected
        if linked != self._was_linked:
            self._was_linked = linked
            self._last_state = None  # a new link gets the whole state
            self._state_due = True
            self.show_message("KnobDeck linked" if linked else "KnobDeck disconnected")
        status = self._link.status.get("state")
        if status == "refused" and self._last_status != "refused":
            self.show_message("KnobDeck: " + str(self._link.status.get("reason", "refused the link")))
        self._last_status = status
        for req_id, action, params in requests:
            self._answer(req_id, action, params)
        self._ticks += 1
        if linked and (self._state_due or self._ticks % STATE_EVERY == 0):
            self._state_due = False
            self._send_state()
        self._link.flush()

    def _answer(self, req_id, action, params):
        try:
            result = actions.run(self.song(), action, params)
        except actions.Unsupported:
            self._link.respond(req_id, unsupported=True)
            return
        except actions.Failed as e:
            self._link.respond(req_id, error=str(e))
            return
        except Exception as e:  # a bug or a Live API error: report it, keep going
            self._link.respond(req_id, error="{}: {}".format(type(e).__name__, e))
            return
        self._link.respond(req_id, result=result)
        self._state_due = True  # report the change at once

    def _send_state(self):
        try:
            s = actions.snapshot(self.song())
        except Exception as e:
            self.log_message("state error: {!r}".format(e))
            return
        if s != self._last_state and self._link.emit("state", s):
            self._last_state = s

    # ── the Remote Script interface Live calls ──────────────────────────

    def update_display(self):
        self.tick()

    def disconnect(self):
        self._link.close()

    def refresh_state(self):
        self._last_state = None
        self._state_due = True

    def connect_script_instances(self, instanciated_scripts):
        pass

    def can_lock_to_devices(self):
        return False

    def lock_to_device(self, device):
        pass

    def unlock_from_device(self, device):
        pass

    def toggle_lock(self):
        pass

    def build_midi_map(self, midi_map_handle):
        pass

    def receive_midi(self, midi_bytes):
        pass

    def suggest_input_port(self):
        return ""

    def suggest_output_port(self):
        return ""

    def suggest_map_mode(self, cc_no, channel):
        return -1

    def suggest_needs_takeover(self, cc_no, channel):
        return True

    def supports_pad_translation(self):
        return False

    def port_settings_changed(self):
        pass

    def restore_bank(self, bank):
        pass

    def instance_identifier(self):
        return INSTANCE
