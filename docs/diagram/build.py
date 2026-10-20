"""Build the README architecture diagram: inline the icons into template.html -> architecture.html.

{{si:name:#color:size}} is a simple-icons logo (CC0), {{lu:name:#color:size}} a lucide outline icon (ISC).
To render, serve this folder (python -m http.server), open architecture.html at a 3360px-wide viewport
(the page uses zoom: 2) with the browser's color scheme set to dark (the README uses the dark
version) and screenshot the .page element to docs/images/architecture-dark.png.
"""
import re
from pathlib import Path

HERE = Path(__file__).parent
ICONS = HERE / "icons"


def icon(m):
    kind, name, color, size = m.group(1), m.group(2), m.group(3), m.group(4)
    if kind == "si":
        svg = (ICONS / f"{name}.svg").read_text()
        svg = re.sub(r"<title>.*?</title>", "", svg)
        return svg.replace("<svg ", f'<svg width="{size}" height="{size}" fill="{color}" class="ic" ', 1)
    svg = (ICONS / f"l-{name}.svg").read_text()
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S).strip()
    svg = re.sub(r'width="24"\s+height="24"', f'width="{size}" height="{size}"', svg)
    return svg.replace("<svg", f'<svg style="color:{color}"', 1).replace('class="lucide', 'class="ic lucide', 1)


html = (HERE / "template.html").read_text()
html = re.sub(r"\{\{(si|lu):([\w-]+):(#[0-9A-Fa-f]{3,6}):(\d+)\}\}", icon, html)
ARROW = '<svg width="34" height="14" viewBox="0 0 34 14"><path d="M1 7h30M25 1.5 31 7l-6 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
(HERE / "architecture.html").write_text(html.replace("ARROW", ARROW))
