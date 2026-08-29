#!/bin/sh

set -eu

generated=$(mktemp)
trap 'rm -f "$generated"' EXIT

version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)
pandoc man/yaesm.1.md -f markdown-smart -s -t man -o "$generated" \
    --metadata title=YAESM \
    --metadata section=1 \
    --metadata "footer=yaesm $version"

source_hash=$(sha256sum man/yaesm.1.md | cut -d ' ' -f 1)
{
    printf '%s\n' '.\" Auto-generated from man/yaesm.1.md by pandoc. Do not edit.'
    printf '%s\n' ".\\\" Source-SHA256: $source_hash"
    cat "$generated"
} > man/yaesm.1

sh packaging/check-manual.sh
