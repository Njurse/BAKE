#!/usr/bin/env python3
"""d2-export - Driver 2 car export and rebuild.

Public name for that pipeline. The scripts underneath are unchanged:

    exportd2.py       export a level, or rebuild with --rebuild-bank
    make_new_bank.py  one-car bank (--model, --slot, --lev, --city-index)

``--model``, ``--slot``, ``--lev``, ``--city-index``, ``--mod-id``,
``--game-data`` or ``--force`` selects make_new_bank.py. Every other
invocation, including ``--rebuild-bank`` and ``--selftest``, is exportd2.py.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_NEW_BANK_FLAGS = ("--model", "--slot", "--lev", "--city-index", "--mod-id",
                   "--game-data", "--force")


def which(argv):
    """'make_new_bank' or 'exportd2', from flags those scripts already have."""
    for arg in argv:
        name = arg.split("=", 1)[0]
        if name in _NEW_BANK_FLAGS:
            return "make_new_bank"
    return "exportd2"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if which(argv) == "make_new_bank":
        import make_new_bank
        return make_new_bank.main(argv) or 0
    import exportd2
    return exportd2.main(argv) or 0


if __name__ == "__main__":
    sys.exit(main())
