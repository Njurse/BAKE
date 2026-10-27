#!/usr/bin/env python3
"""Guard: no MP_DEBUG (or other debug) switch may change behaviour.

The mp module had a bug where the only `break` guarding recv() was nested inside
a dangling `if (getenv("MP_DEBUG") != NULL ...)`, so a run WITHOUT MP_DEBUG
dropped every connection the instant nothing was pending. Debug switches must
only add logging, never control flow or state.

This scans JERICHO/MODS/mp/*.c for every `getenv("...")` guard and fails if the
guarded statement (brace-balanced, log calls stripped) contains control flow
(break/continue/return/goto) or a state mutation.

    python check_debug_independence.py        # exit 0 = clean, 1 = a violation

It is a static check, so it belongs in CI/a pre-commit step as much as in a run.
"""
import glob
import os
import re
import sys

MP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# state mutators / control flow that a *debug* guard must never wrap
BAD = re.compile(
    r"\b(break|continue|return|goto)\b"
    r"|\bMp(Drop|Send|Add|Remove|Place|Start|Leave|Client|Host|Return|Spawn)[A-Za-z]*\s*\("
    r"|\bgMp\.[A-Za-z_]+\s*="
    r"|\bgConn\["
)

# env vars that are FEATURE levers, not debug switches: they are *meant* to
# change behaviour (autostart, the test bot, config, and the test levers). Only
# debug-style switches are checked.
FEATURE = re.compile(
    r'getenv\("(MP_AUTOSTART|MP_AUTOJOIN_START|MP_BOT|MP_TESTDRIVE|USERNAME|USER'
    r'|MP_EXTRA_SLOTS|MP_TEST_ONFOOT|MP_TEST_CARCHANGE|MP_TEST_CHATKEY|MP_MAP|MP_PAUSE)"\)')


def guarded_body(lines, i):
    """The statement(s) an `if (getenv(...))` on line i guards."""
    depth = lines[i].count("{") - lines[i].count("}")
    body = []
    j = i + 1
    if depth == 0:
        body.append(lines[j] if j < len(lines) else "")
    else:
        while j < len(lines) and depth > 0:
            body.append(lines[j])
            depth += lines[j].count("{") - lines[j].count("}")
            j += 1
    return " ".join(body)


def main():
    root = MP_DIR
    fails = []
    checked = 0

    for path in sorted(glob.glob(os.path.join(root, "*.c"))):
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")

        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith(("*", "//", "/*")):
                continue                      # a comment mentioning getenv != a guard
            if "getenv(" not in ln or FEATURE.search(ln):
                continue
            checked += 1
            body = guarded_body(lines, i)
            scan = re.sub(r"(jer_log|printf|puts|printInfo)\([^;]*\);", "", body)
            scan = re.sub(r"static [^;]*;", "", scan)
            if BAD.search(scan):
                fails.append(f"{os.path.relpath(path, root)}:{i + 1}: {body.strip()[:100]}")

    print(f"checked {checked} getenv guard(s) in {root}")
    for f in fails:
        print("  VIOLATION:", f)
    print("RESULT:", "PASS" if not fails else f"FAIL ({len(fails)})")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
