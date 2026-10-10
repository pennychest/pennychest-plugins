"""Release each plugin whose commits since its last release warrant a version.

Commitizen can't do this: it reads every commit in the repository, so a fix to
one plugin would release all of them. This reads only the commits that touched
a plugin's directory, and otherwise follows PennyChest's .cz.toml: the gitmoji
decides the bump and the changelog section, and before 1.0.0 a breaking change
bumps the minor.

A plugin with no tag yet is released at the version already in its
pyproject.toml. For each release it bumps that version, points the plugin's entry
in plugins.json at it, adds an entry to the plugin's CHANGELOG.md, and tags
<plugin>-v<version> on one commit for them all.
It writes each release's notes to <notes dir>/<tag>.md and the tags to the
`tags` step output, and leaves pushing to the workflow.

Usage: release.py <notes dir>
"""

import datetime
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# Each gitmoji's bump and changelog section; the same as PennyChest's .cz.toml.
# A bump of None lists the commit in the next release without making one.
GITMOJI = {
    "💥": ("major", "Breaking"),
    "✨": ("minor", "Added"),
    "🔌": ("minor", "Added"),
    "🔐": ("minor", "Added"),
    "🤖": ("minor", "Added"),
    "💬": ("minor", "Added"),
    "📊": ("minor", "Added"),
    "📈": ("minor", "Added"),
    "📱": ("minor", "Added"),
    "🧠": ("minor", "Added"),
    "🧭": ("minor", "Added"),
    "📦": ("minor", "Added"),
    "🐛": ("patch", "Fixed"),
    "🚑": ("patch", "Fixed"),
    "🩹": ("patch", "Fixed"),
    "🔒": ("patch", "Fixed"),
    "⚡️": ("patch", "Changed"),
    "♻️": ("patch", "Changed"),
    "🗃️": ("patch", "Changed"),
    "🐳": ("patch", "Changed"),
    "🔧": ("patch", "Changed"),
    "⬆️": ("patch", "Changed"),
    "💄": ("patch", "Changed"),
    "🍱": ("patch", "Changed"),
    "🚸": ("patch", "Changed"),
    "♿️": ("patch", "Changed"),
    "🌐": ("patch", "Changed"),
    "🔊": ("patch", "Changed"),
    "⏪": ("patch", "Changed"),
    "🗑️": ("patch", "Changed"),
    "🔥": ("patch", "Changed"),
    "📝": (None, "Documentation"),
}
BUMPS = ["patch", "minor", "major"]
SECTIONS = ["Breaking", "Added", "Fixed", "Changed", "Documentation"]

CHANGELOG_HEADER = """# Changelog

All notable changes to pennychest-{plugin} are recorded here. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).
"""


INDEX = Path("plugins.json")
# Where releases are downloaded from; a fork's own releases come from the fork.
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "pennychest/pennychest-plugins")


def download_url(plugin: str, version: str) -> str:
    return (
        f"https://github.com/{REPOSITORY}/archive/refs/tags/"
        f"{plugin}-v{version}.zip#subdirectory={plugin}"
    )


def update_index(package: str, plugin: str, version: str) -> None:
    """Offer this version in PennyChest's Settings → Plugins."""
    index = json.loads(INDEX.read_text())
    entry = next((p for p in index["plugins"] if p["package"] == package), None)
    if entry is None:
        raise SystemExit(f"{package} isn't in plugins.json; add it before releasing")
    entry["version"] = version
    entry["url"] = download_url(plugin, version)
    INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def gitmoji(subject: str) -> str | None:
    # Some gitmoji end in a variation selector that isn't always typed, so
    # compare without it.
    bare = subject.replace("️", "")
    for emoji in GITMOJI:
        if bare.startswith(emoji.replace("️", "")):
            return emoji
    return None


def bump(version: str, level: str) -> str:
    major, minor, patch = map(int, version.split("."))
    if level == "major" and major == 0:
        level = "minor"
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def release_notes(subjects: list[str]) -> str:
    sections: dict[str, list[str]] = {}
    for subject in subjects:
        if emoji := gitmoji(subject):
            sections.setdefault(GITMOJI[emoji][1], []).append(subject)
    return "\n\n".join(
        f"### {section}\n\n" + "\n".join(f"- {subject}" for subject in sections[section])
        for section in SECTIONS
        if section in sections
    )


def add_to_changelog(path: Path, plugin: str, heading: str, notes: str) -> None:
    text = path.read_text() if path.exists() else CHANGELOG_HEADER.format(plugin=plugin)
    entry = f"## {heading}\n\n{notes}\n"
    # Newest first: before the first release already listed, if there is one.
    first = re.search(r"^## ", text, re.MULTILINE)
    if first:
        text = text[: first.start()] + entry + "\n" + text[first.start() :]
    else:
        text = text.rstrip("\n") + "\n\n" + entry
    path.write_text(text)


def main() -> None:
    notes_dir = Path(sys.argv[1])
    notes_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    released = []

    for project in sorted(Path().glob("*/pyproject.toml")):
        plugin = project.parent.name
        metadata = tomllib.loads(project.read_text())["project"]
        version = metadata["version"]
        tags = git("tag", "--list", f"{plugin}-v*", "--sort=-v:refname").splitlines()

        if not tags:
            notes = "The first release."
        else:
            subjects = git("log", "--format=%s", f"{tags[0]}..HEAD", "--", plugin).splitlines()
            levels = [
                level
                for subject in subjects
                if (emoji := gitmoji(subject)) and (level := GITMOJI[emoji][0])
            ]
            if not levels:
                print(f"{plugin}: nothing to release since {tags[0]}")
                continue
            version = bump(version, max(levels, key=BUMPS.index))
            notes = release_notes(subjects)
            project.write_text(
                re.sub(
                    r'^version = "[^"]*"',
                    f'version = "{version}"',
                    project.read_text(),
                    count=1,
                    flags=re.MULTILINE,
                )
            )

        tag = f"{plugin}-v{version}"
        update_index(metadata["name"], plugin, version)
        add_to_changelog(project.parent / "CHANGELOG.md", plugin, f"v{version} ({today})", notes)
        (notes_dir / f"{tag}.md").write_text(notes + "\n")
        git("add", str(project), str(project.parent / "CHANGELOG.md"), str(INDEX))
        released.append((plugin, version, tag))
        print(f"{plugin}: releasing v{version}")

    if released:
        names = [f"{plugin} v{version}" for plugin, version, _ in released]
        summary = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        git("commit", "-m", f"🔖 Release {summary}")
        for _, _, tag in released:
            git("tag", "--annotate", tag, "--message", tag)

    tags = " ".join(tag for _, _, tag in released)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as f:
            f.write(f"tags={tags}\n")


if __name__ == "__main__":
    main()
