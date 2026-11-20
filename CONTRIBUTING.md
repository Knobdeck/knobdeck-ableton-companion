# Developing the KnobDeck Remote Script for Ableton Live

One of the [KnobDeck companions](https://github.com/Knobdeck/knobdeck-companion). It speaks the
companion protocol (layer 2 of `knobdeck-protocol` in
[knobdeck-sdk](https://github.com/Knobdeck/knobdeck-sdk/tree/master/knobdeck-protocol): fixtures in
`fixtures/companion-v1.jsonl`, reference in `REFERENCE.md`) to KnobDeck's companion hub. Request
ids and their parameters are a contract with the `ableton` plugin in
[knobdeck-plugins](https://github.com/Knobdeck/knobdeck-plugins/tree/master/ableton): add, never
rename.

## Layout

- `KnobDeck/`: the Remote Script, the folder users copy into `Remote Scripts`. Plain Python 3.7+
  (Live 11 runs 3.7, Live 12 runs 3.11), no dependencies: Live bundles no WebSocket module, so
  `ws.py` is a small RFC 6455 codec on the standard library.
  - `__init__.py`: `create_instance(c_instance)`, Live's entry point.
  - `surface.py`: the control surface (the plain Remote Script interface, not Ableton's
    `_Framework`). Live calls its `update_display` about every 100 ms on its main thread; each
    tick moves the hub link on, runs the requests that arrived and, about every 200 ms, sends a
    `state` event when what the keys show changed. No threads: Live's API may only be used from
    its main thread, and the tick must never wait.
  - `hub.py`: discovery file, non-blocking connect, the upgrade, hello, ping, reconnect with back-off.
    Without the discovery file (KnobDeck not running) it tries no port.
  - `actions.py`: what each request does, with the Live Object Model only (never key events),
    and the state snapshot.
  - `version.py`: the version; the release tag is `v<VERSION>`.
- `tests/`: `fake_hub.py` (a stand-in for KnobDeck's hub: same discovery file, Host / Origin /
  token checks, refusals), `fake_live.py` (the parts of the Live Object Model the script uses),
  `test_ws.py`, `test_link.py`, `test_actions.py`, `test_surface.py` (the whole script ticking
  against both), and `test_e2e_plugin.py` (the real `ableton` plugin over its stdio protocol,
  `plugin_host.py` playing KnobDeck).

## Build and test

```sh
ruff check . && ruff format --check .
python3 -m pytest

# with the real plugin: cargo build -p ableton in knobdeck-plugins
KNOBDECK_ABLETON_PLUGIN=../knobdeck-plugins/target/debug/ableton python3 -m pytest tests/test_e2e_plugin.py
```

The tests run without Live. To try the script in Live, copy (or symlink) `KnobDeck/` into your
User Library's `Remote Scripts`, restart Live and pick KnobDeck as a Control Surface. Live writes
the script's messages (`KnobDeck: …`) and Python errors to its `Log.txt` (Windows:
`%APPDATA%\Ableton\Live <version>\Preferences\Log.txt`; macOS:
`~/Library/Preferences/Ableton/Live <version>/Log.txt`). `KNOBDECK_COMPANION_DISCOVERY` points
the script at another discovery file.

CI (`.github/workflows/ci.yml`) lints, runs the tests on Python 3.11 on Windows, macOS and Linux and
on Python 3.7 on Linux, runs the end-to-end test with the `ableton` plugin built from knobdeck-plugins master and
zips the `KnobDeck/` folder.

## Releasing

Bump `VERSION` in `KnobDeck/version.py` and add a `CHANGELOG.md` entry, then merge to `master`.
That starts the Release workflow (`.github/workflows/release.yml`): it runs CI, creates the tag
`v<version>` and a GitHub release with `knobdeck-ableton-<version>.zip`, which the plugin's setup
downloads (the newest release). A push that doesn't bump the version only runs CI. Live has no
add-on store, so there is nothing else to upload.
