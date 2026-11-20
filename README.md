# KnobDeck Remote Script for Ableton Live

A Remote Script (control surface script) for Ableton Live that lets the
[KnobDeck](https://knobdeck.harshalkudale.com) Ableton Live plugin control Live through Live's
own Python API, instead of sending keyboard shortcuts. Turn a dial to set the tempo, a track's
volume or a device parameter; press a key to play, record, launch a scene or toggle the
metronome. Keys show Live's state as it changes.

Works with Live 11 and newer on Windows and macOS, and KnobDeck with the companion hub.

## Install

1. In KnobDeck, install the **Ableton Live** plugin (Get Plugins) and run its setup: it copies
   this script into Live's User Library for you.

   Or by hand: download `knobdeck-ableton-<version>.zip` from a
   [release](https://github.com/Knobdeck/knobdeck-ableton-companion/releases) and copy the
   `KnobDeck` folder in it into the `Remote Scripts` folder of your User Library (create it if it
   isn't there):
   - Windows: `%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts`
   - macOS: `~/Music/Ableton/User Library/Remote Scripts`

   (If you moved the User Library, Live shows where in Settings ▸ Library.)
2. Restart Live. In **Settings ▸ Link, Tempo & MIDI**, choose **KnobDeck** in a free Control
   Surface slot; leave its Input and Output at None.

That's all: there is no port or password to set. The script finds KnobDeck by itself and
reconnects when KnobDeck or Live restarts; Live's status bar says "KnobDeck linked".

## What it does

| KnobDeck action | In Live |
|---|---|
| Play / Stop, Stop, Continue Playing | the transport (the key shows playing) |
| Arrangement Record, Session Record, MIDI Arrangement Overdub, Automation Arm, Metronome, Loop, Punch In, Punch Out | the Song's switches, each key showing on / off |
| Tempo (dial), Tap Tempo | the tempo (20–999 BPM) |
| Previous / Next Locator, Add / Delete Locator | arrangement locators (cue points) |
| Undo, Redo, Capture MIDI | Live's undo history; Capture MIDI (Live 10+) |
| Master Volume, Track Volume (dials) | mixer volume in dB, as Live's fader shows it |
| Track Pan (dial) | mixer pan, 50L … 50R |
| Mute, Solo, Record Arm | the selected or a picked track (return tracks too, except arm) |
| Launch Scene, Stop All Clips | the selected or a picked scene |
| Select Track, Select Scene (dials), Tracks and Scenes wheels | the selection in Live |
| Device Parameter (dials), Previous / Next Parameter Bank | the selected device's parameters, 8 per bank |
| Selected Parameter (dial) | the control last clicked in Live |
| Device On / Off, Selected Device | the selected device |

## Privacy

It connects only to KnobDeck on your own computer and collects nothing: see
[PRIVACY.md](PRIVACY.md).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). This is one of the
[KnobDeck companions](https://github.com/Knobdeck/knobdeck-companion); the plugin it talks to is
[`ableton`](https://github.com/Knobdeck/knobdeck-plugins/tree/master/ableton) in knobdeck-plugins.

Not affiliated with Ableton. Ableton and Ableton Live are trademarks of Ableton AG.
