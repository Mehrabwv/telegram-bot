"""Build the 3D viewer page, weather_case_viewer.html, from the case's meshes.

Run it after build_case.py, which writes ../viewer_data.json:
    py viewer/make_viewer.py
Only the standard library is needed. The page loads three.js from a CDN, so it needs
an internet connection when it is opened.
"""
import base64
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASE = HERE.parent
template = (HERE / "template.html").read_text(encoding="utf-8")
data = (CASE / "viewer_data.json").read_text(encoding="utf-8")
# The picture on the model's screen: a real screenshot from the display
screen = base64.b64encode((CASE.parent / "screenshot-example.png").read_bytes()).decode()
assert "</script" not in data
page = template.replace("__DATA__", data).replace("__SCREEN__", "data:image/png;base64," + screen)
out = HERE / "weather_case_viewer.html"
out.write_text(page, encoding="utf-8")
print(f"{out} ({out.stat().st_size / 1024:.0f} KB)")
