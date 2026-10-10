"""Check plugins.json, which PennyChest's Settings → Plugins reads, against the plugins here:
every plugin is listed, at the version in its pyproject.toml, from that version's tag."""

import json
import re
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[2]
listed = {p["package"]: p for p in json.loads((root / "plugins.json").read_text())["plugins"]}
problems = []

for pyproject in sorted(root.glob("*/pyproject.toml")):
    directory = pyproject.parent.name
    project = tomllib.loads(pyproject.read_text())["project"]
    name, version = project["name"], project["version"]
    plugin = listed.pop(name, None)
    if plugin is None:
        problems.append(f"{name} isn't in plugins.json")
        continue
    if plugin["version"] != version:
        problems.append(f"{name} is {version}, but plugins.json says {plugin['version']}")
    # From this repository or a fork of it
    url = (
        r"https://github\.com/[^/]+/[^/]+/archive/refs/tags/"
        + re.escape(f"{directory}-v{version}.zip#subdirectory={directory}")
    )
    if not re.fullmatch(url, plugin["url"]):
        problems.append(
            f"{name}'s url should be its {directory}-v{version} tag's archive, "
            f"with #subdirectory={directory}"
        )

problems += [f"{name} is in plugins.json but has no directory" for name in listed]
print("\n".join(problems) or "plugins.json is up to date")
sys.exit(1 if problems else 0)
