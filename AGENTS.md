# AGENTS.md

Notes for coding agents working in this repository. The [README](README.md) is the full guide for
people; this is the short version, plus the rules that aren't obvious from the code.

## What's here

Official plugins for [PennyChest](https://github.com/pennychest/pennychest). Each top-level
directory (`hsbc/`, `beancount/`) is its own Python package with its own `pyproject.toml`,
version, `CHANGELOG.md` and tests. PennyChest finds plugins through the `pennychest.importers`
and `pennychest.exporters` entry points.

- `plugins.json`: what PennyChest's **Settings → Plugins** reads to list and install plugins
- `.github/scripts/check_index.py`: checks `plugins.json` against each plugin's `pyproject.toml`
- `.github/scripts/release.py`: releases each plugin on its own when changes merge to `main`

The interfaces plugins implement live in PennyChest, not here:
[`imports/base.py`](https://github.com/pennychest/pennychest/blob/main/backend/pennychest/imports/base.py)
and [`export/base.py`](https://github.com/pennychest/pennychest/blob/main/backend/pennychest/export/base.py).
Read them before changing a plugin. If a change needs something new from the core, it belongs in
a pull request to pennychest first.

## Setup and checks

PennyChest is cloned next to this repository (`../pennychest`). In one virtualenv:

```sh
pip install -e ../pennychest/backend -e "hsbc[dev]" -e "beancount[dev]"
```

Then run what CI runs, for each plugin you touched:

```sh
ruff check <plugin>
python -m pytest -q <plugin>/tests
python .github/scripts/check_index.py
```

CI tests every plugin against PennyChest's `main`, so test against an up-to-date checkout of it.

## Rules

- **Never hand-edit versions or changelogs.** The Release workflow bumps `version` in a plugin's
  `pyproject.toml`, its entry in `plugins.json`, and its `CHANGELOG.md`, then tags
  `<plugin>-v<version>`. The exception is a new plugin, which starts at the version you give it.
- **Commit titles start with a gitmoji, which decides the release.** It's read from commits that
  touched a plugin's directory: ✨ releases a minor version, 🐛 a patch, 📝 lists the commit
  without releasing, 👷 / ✅ / 🎨 do neither. The full table is `GITMOJI` in
  `.github/scripts/release.py`, and matches PennyChest's `.cz.toml`. Keep each commit to one
  plugin where you can, so one plugin's fix doesn't release another.
- **No real financial data in the repository.** The HSBC tests read real statements from
  `HSBC_TEST_PDF_DIR` and are skipped without it; they assert structure, never transaction
  values. Don't commit statements, extracted text or values from them as fixtures. Use made-up
  data.
- **Style:** Python 3.11+, ruff with a line length of 100, the same settings in each
  `pyproject.toml`.
- **READMEs are published.** Changes to `README.md` or `*/README.md` on `main` rebuild
  pennychest.github.io, so write them for users.

## Adding a plugin

1. Add `<name>/` modelled on `hsbc/` (importer) or `beancount/` (exporter): a package
   `pennychest_<name>`, a `pyproject.toml` named `pennychest-<name>` that depends on `pennychest`
   and registers the entry point, a `README.md` and tests.
2. List it in `plugins.json` at the version in its `pyproject.toml`, with the `url` pointing at the
   `<name>-v<version>` tag's archive and `#subdirectory=<name>`. `check_index.py` enforces this.
3. Add it to the `plugin` matrix in `.github/workflows/ci.yml`, and to the table in `README.md`.

It's released at its starting version when it merges.
