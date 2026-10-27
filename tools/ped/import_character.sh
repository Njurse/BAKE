#!/bin/sh
# Import a character folder (character.json, model.obj and textures/).
set -eu
[ $# -ge 1 ] || { echo "usage: $0 <character-folder>" >&2; exit 1; }

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "JMPPED: Python 3 was not found - install it and put it on PATH." >&2
    exit 3
fi

exec "$PY" "$DIR/jmpped.py" import --folder "$1"
