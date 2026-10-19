# PennyChest plugins

Optional plugins for [PennyChest](https://github.com/pennychest/pennychest): importers for
more banks' statements, and exporters to other formats. Each directory is a Rust crate, built as
a WebAssembly (WASI) module that PennyChest runs in a sandbox.

| Plugin | Type | What it does |
|---|---|---|
| [`hsbc`](hsbc) | Importer | HSBC CIIOM PDF statements (current accounts and credit cards) |
| [`beancount`](beancount) | Exporter | The ledger in Beancount's plain-text format, for Fava and bean-query |

## Installing a plugin

In PennyChest, go to **Settings → Plugins** and press **Install**. Plugins from this repository
are listed there already.

## Making your own

1. Fork this repository.
2. Add a directory for your plugin and list it in the workspace's `members` in `Cargo.toml`. It
   needs:
   - a `Cargo.toml` that depends on `pennychest-plugin`, the SDK;
   - a `pennychest-plugin.toml` manifest, with the same version as its `Cargo.toml`;
   - a `README.md`, whose first line is the name shown in Settings → Plugins;
   - a `CHANGELOG.md`, whose newest section becomes the release notes.

   `hsbc/` is a complete importer and `beancount/` an exporter. Everything a plugin does is
   described in [the plugin protocol](https://pennychest.github.io/plugins/protocol/).
3. Push, and tag the release: `git tag mybank-v0.1.0 && git push --tags`. The Release workflow
   builds and packages the plugin, attaches it to a GitHub release, and adds it to
   `plugins.json`, which is what PennyChest reads to list plugins.
4. In PennyChest, go to **Settings → Plugins → Add a repository**, paste your fork's link, and
   install your plugin.

A plugin only sees what PennyChest sends it: the statement it's reading, or the ledger it's
exporting. Still, only add repositories you trust. To share a plugin with everyone, open a pull
request here.

## Developing

```sh
cargo test
cargo clippy --all-targets
cargo build --release --target wasm32-wasip1
```

`tools/package.sh <plugin>` builds a plugin and packages it in `dist/` as Settings → Plugins
installs it, and prints its entry for `plugins.json`.

## Licence

MIT, the same as PennyChest. See [LICENSE](LICENSE).
