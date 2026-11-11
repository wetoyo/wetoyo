#!/usr/bin/env python3
"""
sign_shortcut.py - sign an unsigned .shortcut with RoutineHub's HubSign
service, which runs Apple's `shortcuts sign --mode anyone` on a Mac and
returns an Apple-signed AEA1 archive that iOS will import.

  python3 sign_shortcut.py [unsigned.shortcut] [-o PDF_Page_Editor.shortcut]

What is sent: ONLY the shortcut definition (XML plist) and its name. No
credentials, no PDFs, no personal data. The response is checked for the
AEA1 magic; verify it independently with tools/verify_shortcut (see README).

Alternative on a Mac (no third-party service):
  shortcuts sign --mode anyone --input build/PDF_Page_Editor.unsigned.shortcut \
                 --output PDF_Page_Editor.shortcut
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HUBSIGN_URL = "https://hubsign.routinehub.services/sign"
# Same request shape as the Cherri compiler's --hubsign option.
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "cherri/1.0",
    "Origin": "https://routinehub.co",
    "Referer": "https://routinehub.co/",
}
HERE = os.path.dirname(os.path.abspath(__file__))


def sign(plist: dict, name: str, retries: int = 4) -> bytes:
    xml = plistlib.dumps(plist, fmt=plistlib.FMT_XML).decode("utf-8")
    body = json.dumps({"shortcutName": name, "shortcut": xml}).encode("utf-8")
    delay = 2
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(HUBSIGN_URL, data=body, headers=HEADERS, method="POST"),
                         timeout=120) as resp:
                data = resp.read()
            if data[:4] != b"AEA1":
                raise ValueError(f"HubSign returned non-AEA data: {data[:80]!r}")
            return data
        except (URLError, HTTPError, TimeoutError) as e:
            if attempt == retries:
                raise
            print(f"HubSign request failed ({e}); retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="?",
                    default=os.path.join(HERE, "build", "PDF_Page_Editor.unsigned.shortcut"))
    ap.add_argument("-o", "--output", default=os.path.join(HERE, "PDF_Page_Editor.shortcut"))
    ap.add_argument("-n", "--name", default="PDF Page Editor")
    args = ap.parse_args()

    with open(args.input, "rb") as f:
        plist = plistlib.load(f)
    signed = sign(plist, args.name)
    with open(args.output, "wb") as f:
        f.write(signed)
    print(f"signed: {args.output} ({len(signed)} bytes, magic {signed[:4].decode()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
