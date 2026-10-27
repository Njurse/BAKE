#!/bin/sh
set -e
exec python "$(dirname "$0")/jmpped_wizard.py" "$@"
