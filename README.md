# PennyChest plugins

Optional plugins for [PennyChest](https://github.com/pennychest/pennychest). Each directory
is its own Python package, which PennyChest finds through an entry point once it's installed in
the same environment.

| Plugin | Type | What it does |
|---|---|---|
| [`hsbc`](hsbc) | Importer | HSBC CIIOM PDF statements (current accounts and credit cards) |
| [`beancount`](beancount) | Exporter | The ledger in Beancount's plain-text format, for Fava and bean-query |

## Installing a plugin

With Docker, list plugins as pip requirements when building the PennyChest image:

```sh
docker build \
  --build-arg PENNYCHEST_PLUGINS="git+https://github.com/pennychest/pennychest-plugins@hsbc-v0.1.0#subdirectory=hsbc" \
  .
```

Pin a tag (`hsbc-v0.1.0`) rather than `main`, so a rebuild doesn't silently pick up a different
version, and so changing the version busts Docker's build cache.

Outside Docker, `pip install` the same requirement into PennyChest's environment and restart it.

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

## Adding a plugin

Create a directory with a `pyproject.toml` that depends on `pennychest` and registers an entry
point in `pennychest.importers` or `pennychest.exporters`; see `hsbc/` for a complete example and
PennyChest's `DESIGN.md` (decision 6) for the interfaces. Add the directory to the matrix in
`.github/workflows/ci.yml`. It's released at the version in its `pyproject.toml` when it
merges.

## Releases

Merging to `main` is the release, as in PennyChest, but each plugin is versioned on its own. The
Release workflow reads the gitmoji on the commits that touched a plugin's directory since its
last `<plugin>-v<version>` tag, using the same rules as PennyChest's `.cz.toml`: ✨ and the
other feature gitmoji release a minor version, 🐛 and the other fix gitmoji a patch, 💥 a minor
until 1.0.0, and 📝 lists the commit in the next release without making one. For each plugin
that needs a release it bumps the version in `pyproject.toml`, adds an entry to the plugin's
`CHANGELOG.md`, tags `<plugin>-v<version>` and creates a GitHub release.

## Licence

MIT, the same as PennyChest. See [LICENSE](LICENSE).
