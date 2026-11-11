#!/usr/bin/env bash
# Full pipeline: generate -> validate -> simulate -> sign -> verify signature
# -> compare signed payload with what was sent -> re-test the signed payload.
#
#   ./make_all.sh                 # needs network access to hubsign.routinehub.services
#   VERIFY=/path/verify_shortcut ./make_all.sh
#   SKIP_SIGN=1 ./make_all.sh     # only build + test the unsigned file
set -euo pipefail
cd "$(dirname "$0")"

step() { printf '\n=== %s\n' "$*"; }

step "1. Generate"
python3 build_shortcut.py
sha256sum build/PDF_Page_Editor.unsigned.shortcut

step "2. Validate structure (unsigned)"
python3 tests/validate_shortcut.py build/PDF_Page_Editor.unsigned.shortcut

step "3. Simulate (unsigned)"
python3 tests/simulate.py build/PDF_Page_Editor.unsigned.shortcut

if [ "${SKIP_SIGN:-0}" = 1 ]; then exit 0; fi

step "4. Sign (HubSign -> Apple 'anyone' signature)"
python3 sign_shortcut.py build/PDF_Page_Editor.unsigned.shortcut -o PDF_Page_Editor.shortcut
head -c 4 PDF_Page_Editor.shortcut; echo

VERIFY="${VERIFY:-$(command -v verify_shortcut || true)}"
if [ -z "$VERIFY" ]; then
  echo "verify_shortcut not found; build it with tools/build_verifier.sh and set VERIFY=" >&2
  exit 1
fi

step "5. Verify signature + extract payload (libshortcutsign)"
"$VERIFY" PDF_Page_Editor.shortcut build/extracted_from_signed.plist \
  | grep -E '^(file|format|Subject|Issuer|signature|extract):'

step "6. Compare signed payload with what was sent"
python3 tools/compare_payload.py build/PDF_Page_Editor.unsigned.shortcut build/extracted_from_signed.plist

step "7. Validate + simulate the SIGNED payload"
python3 tests/validate_shortcut.py build/extracted_from_signed.plist
python3 tests/simulate.py build/extracted_from_signed.plist

step "Done"
sha256sum PDF_Page_Editor.shortcut
