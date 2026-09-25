"""Embed the simulator guides (Markdown) into the single-file simulator's Help page.

    python tools/embed_help.py

The simulator is one HTML file opened over file://, so it cannot fetch the .md files at run time.
This script copies docs/simulator/USER_GUIDE.md and AGENT_GUIDE_building_a_control_simulator.md
into the <script type="text/markdown"> blocks between the HELP markers. Edit the .md files, then
run this; never edit the embedded copies.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "simulator", "sample_gas_simulator.html")
DOCS = {"user": "USER_GUIDE.md", "agent": "AGENT_GUIDE_building_a_control_simulator.md"}

with open(HTML, encoding="utf-8") as f:
    html = f.read()
for key, name in DOCS.items():
    with open(os.path.join(ROOT, "docs", "simulator", name), encoding="utf-8") as f:
        md = f.read().strip()
    if "</script" in md.lower():
        sys.exit(f"{name} contains '</script', which would end the embedding block early")
    block = f'<!--HELP:{key}:BEGIN-->\n<script type="text/markdown" id="md-{key}">\n{md}\n</script>\n<!--HELP:{key}:END-->'
    html, n = re.subn(rf"<!--HELP:{key}:BEGIN-->.*?<!--HELP:{key}:END-->", lambda _: block, html, flags=re.S)
    if n != 1:
        sys.exit(f"marker HELP:{key} found {n} times (expected 1)")
    print(f"embedded {name}: {len(md)} characters")
with open(HTML, "w", encoding="utf-8", newline="\n") as f:
    f.write(html)
