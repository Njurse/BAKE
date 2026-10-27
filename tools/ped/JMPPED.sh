#!/bin/sh
# JMPPED - pedestrian authoring launcher.
#
# Verbs (see tools/NAMING.md):
#   extract | import | bake | preview | add-character |
#   sync-source | migrate | status | rebuild-all
#
# Examples:
#   ./JMPPED.sh status
#   ./JMPPED.sh extract --source DRIVER2/LEVELS/HAVANA.LEV
#   ./JMPPED.sh bake --copy
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "JMPPED: Python 3 was not found - install it and put it on PATH." >&2
    exit 3
fi

exec "$PY" "$DIR/jmpped.py" "$@"
