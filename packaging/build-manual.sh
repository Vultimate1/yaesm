#!/bin/sh

# Generate the portable roff manual with Pandoc for CI and package builds.

set -eu

version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)
if [ -z "$version" ]; then
    printf '%s\n' 'error: could not read the project version from pyproject.toml' >&2
    exit 1
fi

generated=$(mktemp)
trap 'rm -f "$generated"' EXIT

pandoc man/yaesm.1.md -f markdown-smart -s -t man -o "$generated" \
    --metadata title=YAESM \
    --metadata section=1 \
    --metadata "footer=yaesm $version"

{
    printf '%s\n' '.\" Auto-generated from man/yaesm.1.md by pandoc. Do not edit.'
    # Replace Pandoc's named fonts with portable roff font escapes.
    sed -E \
        -e 's/\\f\[(C|R)\]/\\fR/g' \
        -e 's/\\f\[(CB|VB|V|B)\]/\\fB/g' \
        -e 's/\\f\[VI\]/\\fI/g' \
        -e 's/\\f\[VBI\]/\\f(BI/g' \
        "$generated"
} > man/yaesm.1
