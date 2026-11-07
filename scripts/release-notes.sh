#!/bin/sh
# Print the CHANGELOG.md section for a version (e.g. `scripts/release-notes.sh 0.1.0`).
# Exits non-zero if there is no such section or it is empty.
set -eu
version="${1#v}"
cd "$(dirname "$0")/.."
notes=$(awk -v v="$version" '
  /^## \[/ { inside = index($0, "[" v "]") > 0; next }
  inside
' CHANGELOG.md | sed -e '/./,$!d')
if [ -z "$(printf '%s' "$notes" | tr -d '[:space:]')" ]; then
  echo "No CHANGELOG.md section for $version" >&2
  exit 1
fi
printf '%s\n' "$notes"
