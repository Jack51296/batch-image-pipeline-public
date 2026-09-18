# -*- coding: utf-8 -*-
"""Like kml_run.py but: Ctrl+C to clear pending input, paste (fast) instead of typing, re-focus before Enter."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kml_term import Kml

SHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_kml_screen.png")
cmd = sys.argv[1]
if cmd.startswith("@"):
    cmd = open(cmd[1:], encoding="utf-8").read().strip().replace("\r\n", "\n")
    assert "\n" not in cmd
wait = float(sys.argv[2]) if len(sys.argv) > 2 else 4

k = Kml()
assert k.focus_term(), "terminal not focused"
k.key("c", "KeyC", 67, modifiers=2)   # Ctrl+C clears any pending input line
time.sleep(0.6)
assert k.focus_term()
k.paste_text(cmd)
time.sleep(0.6)
assert k.focus_term()
k.enter()
time.sleep(wait)
k.screenshot(SHOT)
print("done")
