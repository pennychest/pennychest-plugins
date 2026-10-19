#!/bin/sh
# Builds a plugin for WASI and packages it as Settings > Plugins installs it:
#   dist/<name>-<version>.tar.gz, holding <name>/pennychest-plugin.toml and <name>/<name>.wasm
# and prints its entry for plugins.json. The archive is reproducible: the same source gives the
# same bytes, so the same checksum.
#
#   tools/package.sh hsbc
set -eu
cd "$(dirname "$0")/.."
name=$1
manifest="$name/pennychest-plugin.toml"
version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$manifest" | head -1)
crate_version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$name/Cargo.toml" | head -1)
if [ "$version" != "$crate_version" ]; then
  echo "$manifest says $version but $name/Cargo.toml says $crate_version" >&2
  exit 1
fi
if [ -n "${EXPECTED_VERSION:-}" ] && [ "$version" != "$EXPECTED_VERSION" ]; then
  echo "$name is version $version, not $EXPECTED_VERSION" >&2
  exit 1
fi

cargo build --release --locked -p "$name" --target wasm32-wasip1
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
mkdir "$staging/$name"
cp "$manifest" "$staging/$name/"
cp "target/wasm32-wasip1/release/$name.wasm" "$staging/$name/"
mkdir -p dist
archive="dist/$name-$version.tar.gz"
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -C "$staging" -cf - "$name" \
  | gzip -n -9 > "$archive"
sha256=$(sha256sum "$archive" | cut -d' ' -f1)
echo "$archive" >&2
cat <<JSON
{"package": "$name", "version": "$version", "file": "$name-$version.tar.gz", "sha256": "$sha256"}
JSON
