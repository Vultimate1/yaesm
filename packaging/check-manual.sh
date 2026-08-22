#!/bin/sh

set -eu

source_hash=$(sha256sum man/yaesm.1.md | cut -d ' ' -f 1)
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)

grep --fixed-strings --line-regexp --quiet \
    '.\" Source-SHA256: '"$source_hash" man/yaesm.1
grep --fixed-strings --quiet '"yaesm '"$version"'"' man/yaesm.1
