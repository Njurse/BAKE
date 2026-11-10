#!/usr/bin/env python3
"""ped-bake - JMPPED pedestrian tools.

Public name for jmpped.py. Same verbs, including ``ped-bake bake``.
JMPPED.bat and jmpped.py still run.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jmpped


def main(argv=None):
    return jmpped.main(argv) or 0


if __name__ == "__main__":
    sys.exit(main())
