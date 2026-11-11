#!/usr/bin/env bash
# Builds tools/verify_shortcut (signature check + payload extraction) on Linux
# or macOS from source: libplist + libshortcutsign (+ its lzfse and
# libNeoAppleArchive submodules). Requires: git, clang, make, autotools,
# OpenSSL 3 dev headers, zlib.
#
#   tools/build_verifier.sh [work-dir]      -> <work-dir>/verify_shortcut
set -euo pipefail

WORK="$(cd "${1:-${TMPDIR:-/tmp}/shortcut-verifier}" 2>/dev/null && pwd || (mkdir -p "${1:-${TMPDIR:-/tmp}/shortcut-verifier}" && cd "${1:-${TMPDIR:-/tmp}/shortcut-verifier}" && pwd))"
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$WORK/local"
cd "$WORK"

if [ ! -f "$PREFIX/lib/libplist-2.0.a" ]; then
  [ -d libplist ] || git clone --depth 1 https://github.com/libimobiledevice/libplist.git
  (cd libplist && ./autogen.sh --prefix="$PREFIX" --without-cython >/dev/null && make -j8 >/dev/null && make install >/dev/null)
fi

if [ ! -f libshortcutsign/build/usr/lib/libshortcutsign.a ]; then
  [ -d libshortcutsign ] || git clone --depth 1 https://github.com/0xilis/libshortcutsign.git
  (cd libshortcutsign && git submodule update --init --recursive --depth 1 \
     && CFLAGS="-I$PREFIX/include" LIBRARY_PATH="$PREFIX/lib" make >/dev/null)
fi

LSS="$WORK/libshortcutsign"
clang -O1 -o "$WORK/verify_shortcut" "$HERE/verify_shortcut.c" -I"$LSS" \
  "$LSS/build/usr/lib/libshortcutsign.a" \
  -L"$LSS/build/lzfse/lib" -L"$LSS/libs/libNeoAppleArchive/build/usr/lib" \
  -L"$LSS/libs/libNeoAppleArchive/build/libzbitmap/lib" -L"$PREFIX/lib" \
  -lNeoAppleArchive -lzbitmap -llzfse -lplist-2.0 -lssl -lcrypto -lz \
  -Wl,-rpath,"$PREFIX/lib"
echo "$WORK/verify_shortcut"
