#!/usr/bin/env python3
"""pool-bake - Driver 1 pool bake.

Public name for the pool pipeline. The scripts underneath are unchanged:

    d1pool.py    extract the traffic shapes   (--levels, --render)
    bake.py      bake that pool into a LEV    (--pool, or --city)

Pass the same arguments you would pass to one of those. ``--pool``, ``--city``,
``--first`` or ``--name`` selects bake.py. Anything else, including ``--levels``,
selects d1pool.py.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_BAKE_FLAGS = ("--pool", "--city", "--first", "--name")


def which(argv):
    """'bake' or 'd1pool', from flags those scripts already have."""
    for arg in argv:
        name = arg.split("=", 1)[0]
        if name in _BAKE_FLAGS:
            return "bake"
    return "d1pool"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    if which(argv) == "bake":
        import bake
        return bake.main(argv) or 0
    import d1pool
    return d1pool.main(argv) or 0


if __name__ == "__main__":
    sys.exit(main())
