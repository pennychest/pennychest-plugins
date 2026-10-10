# PennyChest plugins

Optional plugins for [PennyChest](https://github.com/pennychest/pennychest): importers for more
banks' statements, and exporters to other formats. Each directory is its own Python package,
which PennyChest finds through an entry point once it's installed.

| Plugin | Type | What it does |
|---|---|---|
| [`hsbc`](hsbc) | Importer | HSBC CIIOM PDF statements (current accounts and credit cards) |
| [`beancount`](beancount) | Exporter | The ledger in Beancount's plain-text format, for Fava and bean-query |

## Installing a plugin

In PennyChest, go to **Settings → Plugins** and press **Install**. Plugins from this repository
are listed there already.

## Making your own

1. Fork this repository.
2. Add a directory for your plugin, with a `pyproject.toml` that depends on `pennychest` and
   registers an entry point in `pennychest.importers` or `pennychest.exporters`. `hsbc/` is a
   complete importer and `beancount/` an exporter; the interfaces are in PennyChest's
   [`imports/base.py`](https://github.com/pennychest/pennychest/blob/main/backend/pennychest/imports/base.py)
   and [`export/base.py`](https://github.com/pennychest/pennychest/blob/main/backend/pennychest/export/base.py).
3. Add it to `plugins.json`, which is what PennyChest reads to list plugins:

    ```json
    {
      "package": "pennychest-mybank",
      "name": "My Bank statements",
      "description": "Import My Bank's PDF statements.",
      "version": "0.1.0",
      "url": "https://github.com/<you>/pennychest-plugins/archive/refs/tags/mybank-v0.1.0.zip#subdirectory=mybank"
    }
    ```

4. Push, and tag the release: `git tag mybank-v0.1.0 && git push --tags`.
5. In PennyChest, go to **Settings → Plugins → Add a repository**, paste your fork's link, and
   install your plugin.

A plugin can run any code on your server and read all your data, so only add repositories you
trust. To share a plugin with everyone, open a pull request here.

## Developing

Clone this repository next to PennyChest's:

```
pennychest/           # github.com/pennychest/pennychest
pennychest-plugins/   # this repository
```

then install both into one virtualenv and run a plugin's tests:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ../pennychest/backend -e "hsbc[dev]"
pytest hsbc/tests
ruff check hsbc
```

PennyChest's development Compose file mounts this repository at `/plugins`, so you can also
`pip install -e /plugins/hsbc` inside its `api` container.

## Adding a plugin here

Follow steps 2 and 3 above, and add the directory to the matrix in `.github/workflows/ci.yml`. CI
checks that `plugins.json` lists every plugin at the version in its `pyproject.toml`. It's
released at that version when it merges.

## Releases

Merging to `main` is the release, as in PennyChest, but each plugin is versioned on its own. The
Release workflow reads the gitmoji on the commits that touched a plugin's directory since its
last `<plugin>-v<version>` tag, using the same rules as PennyChest's `.cz.toml`: ✨ and the
other feature gitmoji release a minor version, 🐛 and the other fix gitmoji a patch, 💥 a minor
until 1.0.0, and 📝 lists the commit in the next release without making one. For each plugin
that needs a release it bumps the version in `pyproject.toml` and `plugins.json`, adds an entry
to the plugin's `CHANGELOG.md`, tags `<plugin>-v<version>` and creates a GitHub release.

## Licence

MIT, the same as PennyChest. See [LICENSE](LICENSE).
