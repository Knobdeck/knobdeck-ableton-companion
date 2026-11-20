# Plays KnobDeck for a built plugin: speaks its stdio protocol (hello, sync, invoke, request) and
# relays its `companion_request` / `companion_links` host calls to the fake hub and the hub's link
# events back to it, as Knobdeck/app does. Tests only.

import json
import os
import subprocess
import tempfile
import threading
import time


class Plugin:
    def __init__(self, exe):
        meta = json.loads(subprocess.run([exe], env={**os.environ, "KNOBDECK_CODEGEN": "1"}, capture_output=True, check=True).stdout)
        self.id = meta["manifest"]["id"]
        # short id -> full id: "<manifest id>-<short>-<8 hex>"
        self.ids = {a["id"][len(self.id) + 1 : -9]: a["id"] for a in meta["actions"]["actions"]}
        self.data = tempfile.mkdtemp(prefix="kd-plugin-")
        self.proc = subprocess.Popen(
            [exe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, "KNOBDECK_PLUGIN_DATA": self.data},
        )
        self.frames, self.waiters, self.lock = [], {}, threading.Lock()
        self.subscribed = set()
        self.ready = threading.Event()
        self.hub = None
        self.seq = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [None for _ in self.proc.stderr], daemon=True).start()

    def send(self, msg):
        with self.lock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def _read(self):
        for line in self.proc.stdout:
            f = json.loads(line)
            self.frames.append(f)
            t = f.get("type")
            if t == "ready":
                self.subscribed.update(f.get("subscribe", []))
                self.ready.set()
            elif t == "subscribe":
                self.subscribed = set(f.get("events", []))
            elif t == "host_call":
                threading.Thread(target=self._host_call, args=(f,), daemon=True).start()
            if t in ("done", "result") and f.get("id") in self.waiters:
                self.waiters.pop(f["id"]).append(f)

    def _host_call(self, f):
        method, params = f["method"], f.get("params") or {}
        if method == "companion_request":
            r = self.hub.request(
                params["action"], params.get("params"), link=params.get("link"), timeout=min(params.get("timeout_ms") or 5000, 30000) / 1000
            )
            if "type" not in r:  # the hub's own error
                self.send({"type": "result", "id": f["id"], "ok": False, "error": r["error"]})
                return
            r = {k: v for k, v in r.items() if k != "type"}
            self.send({"type": "result", "id": f["id"], "ok": True, "value": r})
        elif method == "companion_links":
            links = [
                {"link": l, "instance": c.hello["instance"], "app": c.hello["app"], "features": c.hello.get("features", [])}
                for l, c in self.hub.links.items()
            ]
            self.send({"type": "result", "id": f["id"], "ok": True, "value": links})
        else:
            self.send({"type": "result", "id": f["id"], "ok": False, "error": f"{method} is not available here"})

    def hello(self, instances):
        self.send(
            {
                "type": "hello",
                "protocol": 2,
                "plugin_id": self.id,
                "data_dir": self.data,
                "host_version": "e2e",
                "features": ["state_match", "events", "notify", "app_state", "companion_hub"],
                "host_pid": os.getpid(),
            }
        )
        assert self.ready.wait(10), "plugin sent no ready"
        self.send(
            {
                "type": "sync",
                "instances": [{"instance_id": i, "action_id": self.ids[a], "inputs": inputs} for i, (a, inputs) in instances.items()],
            }
        )

    def _wait(self, fid, timeout):
        box = []
        self.waiters[fid] = box
        end = time.monotonic() + timeout
        while not box and time.monotonic() < end:
            time.sleep(0.01)
        assert box, f"no reply to {fid}"
        return box[0]

    def invoke(self, instance, action, inputs=None, trigger="press", child=None):
        self.seq += 1
        fid = f"i{self.seq}"
        msg = {
            "type": "invoke",
            "id": fid,
            "instance_id": instance,
            "action_id": self.ids[action],
            "trigger": trigger,
            "inputs": inputs or {},
        }
        if child is not None:
            msg["child_id"] = child
        start = len(self.frames)
        self.waiters[fid] = box = []
        self.send(msg)
        end = time.monotonic() + 15
        while not box and time.monotonic() < end:
            time.sleep(0.01)
        assert box, f"no done for {action}"
        errors = [f for f in self.frames[start:] if f.get("type") == "error" and f.get("instance_id") == instance]
        return errors

    def request(self, method, params):
        self.seq += 1
        fid = f"r{self.seq}"
        self.waiters[fid] = box = []
        self.send({"type": "request", "id": fid, "method": method, "params": params})
        end = time.monotonic() + 15
        while not box and time.monotonic() < end:
            time.sleep(0.01)
        assert box, f"no result for {method}"
        f = box[0]
        assert f["ok"], f
        return f["value"]

    def event(self, name, payload):
        if name in self.subscribed:
            self.send({"type": "event", "event": name, "payload": payload})

    def wait_frame(self, pred, timeout=10.0, start=0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for f in self.frames[start:]:
                if pred(f):
                    return f
            time.sleep(0.02)
        raise AssertionError("expected frame never came")

    def stop(self):
        try:
            self.send({"type": "shutdown"})
            self.proc.wait(3)
        except Exception:
            self.proc.kill()


def connect(plugin, hub):
    """Wire the fake hub's link events to the plugin, as KnobDeck does."""
    hub.accepts = lambda p: p == plugin.id and "companion_connected" in plugin.subscribed
    hub.on_connect = lambda link, h: plugin.event(
        "companion_connected",
        {
            "link": link,
            "instance": h["instance"],
            "app": h["app"],
            "app_version": h.get("app_version"),
            "name": h.get("name"),
            "version": h.get("version"),
            "features": h.get("features", []),
        },
    )
    hub.on_disconnect = lambda link: plugin.event("companion_disconnected", {"link": link})
    hub.on_event = lambda link, e, p: plugin.event("companion_event", {"link": link, "event": e, "payload": p})
    plugin.hub = hub
