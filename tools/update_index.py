"""Sets a plugin's entry in plugins.json to a release: its version, download and checksum.

    python tools/update_index.py <package> <version> <url> <sha256>

The name and description come from the plugin's directory (its README's first line and its
manifest), so an entry is added the first time a plugin is released.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "plugins.json"


def manifest_value(package: str, key: str) -> str:
    text = (ROOT / package / "pennychest-plugin.toml").read_text()
    match = re.search(rf'^{key} = "(.*)"$', text, re.MULTILINE)
    return match.group(1) if match else ""


def main() -> None:
    package, version, url, sha256 = sys.argv[1:5]
    index = json.loads(INDEX.read_text())
    readme = (ROOT / package / "README.md").read_text().splitlines()[0].lstrip("# ").strip()
    entry = {
        "package": package,
        "name": readme,
        "description": manifest_value(package, "description"),
        "version": version,
        "url": url,
        "sha256": sha256,
    }
    plugins = [p for p in index["plugins"] if p["package"] != package]
    plugins.append(entry)
    index["plugins"] = sorted(plugins, key=lambda p: p["package"])
    INDEX.write_text(json.dumps(index, indent=2) + "\n")


if __name__ == "__main__":
    main()
