#!/usr/bin/env python3
"""
compare_payload.py - deep-compare the unsigned plist that was sent for
signing with the plist extracted from the signed file.

Exit 0 when every action is identical. Header fields that Apple's signer is
known to rewrite (WFWorkflowClientVersion) or drop (WFWorkflowName; the name
comes from the file on import) are reported but tolerated.

Usage: compare_payload.py <unsigned.shortcut> <extracted.plist>
"""

import plistlib
import sys

TOLERATED = {".WFWorkflowClientVersion", ".WFWorkflowName"}


def diff(x, y, path=""):
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            p = f"{path}.{k}"
            if k not in x:
                yield p, f"added {y[k]!r}"
            elif k not in y:
                yield p, f"removed {x[k]!r}"
            else:
                yield from diff(x[k], y[k], p)
    elif isinstance(x, list) and isinstance(y, list):
        if len(x) != len(y):
            yield path, f"length {len(x)} -> {len(y)}"
        for i, (a, b) in enumerate(zip(x, y)):
            yield from diff(a, b, f"{path}[{i}]")
    elif x != y or type(x) is not type(y):
        yield path, f"{x!r} -> {y!r}"


def main() -> int:
    a = plistlib.load(open(sys.argv[1], "rb"))
    b = plistlib.load(open(sys.argv[2], "rb"))
    bad = 0
    for path, what in diff(a, b):
        ok = path in TOLERATED
        bad += not ok
        print(f"  {'tolerated' if ok else 'MISMATCH '} {path}: {what}"[:240])
    n = len(b.get("WFWorkflowActions", []))
    print(f"{len(a['WFWorkflowActions'])} actions sent, {n} in signed file: "
          f"{'IDENTICAL' if not bad else f'{bad} MISMATCH(ES)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
