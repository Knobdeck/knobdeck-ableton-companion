# KnobDeck Remote Script for Ableton Live (11 and newer): links Live to KnobDeck's companion hub
# so the KnobDeck `ableton` plugin can drive Live with Live's own Python API. Copy this folder
# into the User Library's "Remote Scripts" folder and pick "KnobDeck" as a Control Surface in
# Live ▸ Settings ▸ Link, Tempo & MIDI (Input and Output: None).


def create_instance(c_instance):
    from .surface import KnobDeck

    return KnobDeck(c_instance)
