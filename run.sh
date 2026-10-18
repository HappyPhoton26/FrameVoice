#!/bin/bash
# Starts voicevr in the foreground (log also goes to ~/.cache/voicevr/voicevr.log).
cd "$(dirname "$0")"
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
mkdir -p ~/.cache/voicevr
exec python3 -u voicevr/main.py 2>&1 | tee ~/.cache/voicevr/voicevr.log
