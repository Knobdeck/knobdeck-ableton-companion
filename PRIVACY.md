# Privacy

The KnobDeck Remote Script for Ableton Live:

- **collects nothing and sends nothing to the internet.** It has no analytics, no telemetry and no
  crash reporting. Its only connection is to the KnobDeck app on your own computer (`127.0.0.1`).
- **reads one file outside Live:** the connection file KnobDeck writes in its own settings folder
  (`companion-hub.json`: a port number and a token that changes every time KnobDeck starts;
  readable only by your user account).
- **answers only KnobDeck**, which asks it to run Live actions and to report what its keys show:
  the transport and its switches, the tempo, track, return track and scene names, mixer levels
  and pan, mute / solo / arm, the selected track, scene and device, and the device's parameter
  names and values. That information goes no further than KnobDeck on the same computer.
- **stores nothing**: no settings and no files of its own.

For KnobDeck itself see the [KnobDeck privacy policy](https://knobdeck.harshalkudale.com/legal/privacy).
Questions: support@harshalkudale.com.
