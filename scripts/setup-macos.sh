#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ $(uname -s) != Darwin || $(uname -m) != arm64 ]]; then
  echo '此脚本为 Apple Silicon macOS 准备依赖。' >&2
  exit 1
fi

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt 'pyinstaller>=6,<7'
mkdir -p .build/downloads .runtime/tools .runtime/mpv .runtime/portable

fetch() {
  local url="$1" digest="$2" file="$3"
  if ! printf '%s  %s\n' "$digest" "$file" | shasum -a 256 -c --status; then
    curl --fail --location --silent --show-error --retry 2 "$url" -o "$file"
    printf '%s  %s\n' "$digest" "$file" | shasum -a 256 -c
  fi
}

base=https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2
fetch "$base/ffmpeg.zip" c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924 .build/downloads/ffmpeg.zip
fetch "$base/ffprobe.zip" fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6 .build/downloads/ffprobe.zip
fetch https://github.com/media-kit/libmpv-darwin-build/releases/download/v0.7.2/libmpv-libs_v0.7.2_macos-arm64-video-full.tar.gz 9dfda56bd9ec860b8f66c9b73bba16a61cec9bcf3ffc5ad9630e180123bf0a4f .build/downloads/libmpv.tar.gz
unzip -jo .build/downloads/ffmpeg.zip ffmpeg -d .runtime/tools
unzip -jo .build/downloads/ffprobe.zip ffprobe -d .runtime/tools
tar -xzf .build/downloads/libmpv.tar.gz -C .runtime/mpv --strip-components 1
PLAYER_RUNTIME="$PWD/.runtime/mpv" .venv/bin/python - <<'PY'
from pathlib import Path
from scripts.package import copy_mac_runtime
copy_mac_runtime(Path('.runtime/portable'), include_tools=False)
PY
.venv/bin/python scripts/package.py
