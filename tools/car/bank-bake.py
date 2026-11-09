#!/usr/bin/env python3
"""bank-bake - bake a declared bank.json into a Driver 2 LEV/LCF.

Public name for carbank_bake.py. Same arguments. The old script still runs.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import carbank_bake


def main(argv=None):
    return carbank_bake.main(argv) or 0


if __name__ == "__main__":
    sys.exit(main())
