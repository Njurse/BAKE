#!/usr/bin/env python3
"""require_deps.py -- make sure the Python packages the bake needs are installed.

BAKE_D1_CARS.bat / .sh call this once, after finding Python and before the ped
and car bakes. The bakes import Pillow lazily: tools/common/fourbpp.py
quantises a texture crop with Pillow's median cut as soon as it carries more
than sixteen colours. Without Pillow that path stops with ModuleNotFoundError
halfway through a bake, so the package is checked and installed up front
instead.

Only packages a bake cannot run without are listed here. The preview tools'
extras (numpy, pygame-ce, PyOpenGL) stay optional: a bake never needs them.

Usage:
    python require_deps.py            # check, install anything missing
    python require_deps.py --check    # report only; install nothing

Exit codes: 0 = ready, 1 = missing and could not be installed (or --check found
something missing).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass


PREFIX = "[deps]"


@dataclass(frozen=True)
class Dependency:
    """One package the bake needs, and why it needs it."""

    import_name: str
    package: str
    why: str


# The only packages a bake cannot run without. Keep this list small: previews
# and other authoring conveniences must keep degrading gracefully on their own.
REQUIRED = (
    Dependency("PIL", "Pillow", "texture palette quantisation"),
)

# Distro package names for the packages above, used only in the failure hint.
DISTRO_HINT = {
    "Pillow": "sudo apt install python3-pil",
}


def _run(command, **kwargs):
    """The one place a subprocess is started, so tests can replace it."""
    return subprocess.run(command, **kwargs)


def module_available(import_name):
    """(available, detail) for this exact interpreter.

    A subprocess is used so the answer is the one the bake tools will get, and
    so a package installed after the check is picked up by the re-check.
    """
    try:
        result = _run(
            [sys.executable, "-c", "import " + import_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return False, str(exc)

    if result.returncode == 0:
        return True, ""

    lines = (result.stderr or "").strip().splitlines()
    return False, lines[-1] if lines else "import failed"


def pip_available():
    result = _run(
        [sys.executable, "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def ensure_pip():
    if pip_available():
        return True

    print("%s pip is missing - bootstrapping it with ensurepip ..." % PREFIX)
    _run([sys.executable, "-m", "ensurepip", "--upgrade"])
    return pip_available()


def install_commands(package):
    """The installs to try, in order.

    --user first: it needs no administrator rights on Windows and leaves a
    system Python alone. Inside a virtual environment pip refuses --user, so
    the plain form is the fallback; it also covers a Python where the user site
    is disabled.
    """
    return [
        [sys.executable, "-m", "pip", "install", "--user",
         "--disable-pip-version-check", package],
        [sys.executable, "-m", "pip", "install",
         "--disable-pip-version-check", package],
    ]


def install_package(dep):
    print("%s %s is missing (%s) - installing it for %s ..."
          % (PREFIX, dep.package, dep.why, sys.executable))

    for command in install_commands(dep.package):
        print("%s   %s" % (PREFIX, " ".join(command)))
        if _run(command).returncode == 0:
            return True

    return False


def check_dependency(dep, install=True):
    """Make one package importable, installing it when allowed."""
    present, _detail = module_available(dep.import_name)
    if present:
        return True
    if not install:
        return False
    if not ensure_pip():
        return False
    if not install_package(dep):
        return False

    present, detail = module_available(dep.import_name)
    if not present:
        print("%s %s is still not importable after installation."
              % (PREFIX, dep.package))
        if detail:
            print("%s   %s" % (PREFIX, detail))
    return present


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check the Python packages the bake needs.")
    parser.add_argument(
        "--check", action="store_true",
        help="only report; never install anything")
    args = parser.parse_args(argv)

    missing = []
    for dep in REQUIRED:
        if check_dependency(dep, install=not args.check):
            if args.check:
                print("%s %s: ok" % (PREFIX, dep.package))
            continue

        missing.append(dep)
        if args.check:
            print("%s %s: MISSING (%s)" % (PREFIX, dep.package, dep.why))

    if not missing:
        if not args.check:
            print("%s Python packages are ready." % PREFIX)
        return 0

    if args.check:
        print("%s run the bake without --check to install it automatically."
              % PREFIX)
        return 1

    for dep in missing:
        print("%s could not install %s automatically." % (PREFIX, dep.package))
    print("%s Python: %s" % (PREFIX, sys.executable))
    print("%s install it by hand, then run the bake again:" % PREFIX)
    for dep in missing:
        print('%s   "%s" -m pip install --user %s'
              % (PREFIX, sys.executable, dep.package))
    print("%s if that fails, check your internet connection or proxy, and that"
          % PREFIX)
    print("%s this Python has pip (it should answer: python -m pip --version)."
          % PREFIX)
    for dep in missing:
        hint = DISTRO_HINT.get(dep.package)
        if hint:
            print("%s on Debian/Ubuntu, try: %s" % (PREFIX, hint))
    print("%s otherwise use a virtual environment for the bake." % PREFIX)
    return 1


if __name__ == "__main__":
    sys.exit(main())
