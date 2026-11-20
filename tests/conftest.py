# The Remote Script (../KnobDeck) imported as a package under plain Python, with a fake `Live`.
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_live  # noqa: E402

fake_live.install()
