# Changelog

## 1.0.0

- First release, for Ableton Live 11 and newer on Windows and macOS.
- Links Live to KnobDeck's companion hub on `127.0.0.1` (no port to set, no password: the
  connection details come from KnobDeck's own discovery file) and runs the KnobDeck `ableton`
  plugin's actions with Live's Python API: transport, arrangement and session record, overdub,
  automation arm, metronome, loop, punch in / out, tempo and tap tempo, locators, undo / redo,
  Capture MIDI, master and track volume and pan, mute / solo / arm, scenes, track and scene
  selection, and the selected device's parameters and on / off.
- Sends the transport, switches, tempo, mixer, selection and device state to KnobDeck, so keys
  show live values.
