#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/scripts/export_room_sequence.cpp"
BIN="$ROOT/bin/export_room_sequence"
mkdir -p "$ROOT/bin"
if command -v clang++ >/dev/null 2>&1; then
  CXX=clang++
elif command -v g++ >/dev/null 2>&1; then
  CXX=g++
else
  CXX=c++
fi
"$CXX" -std=c++20 -O3 "$SRC" -o "$BIN"
exec "$BIN" "$@"
