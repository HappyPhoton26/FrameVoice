#!/bin/bash
# Turns the "computer" wake word on or off (off closes the microphone). Usage: ./wake.sh on|off
case "$1" in on) v=true;; off) v=false;; *) echo "usage: $0 on|off"; exit 1;; esac
mkdir -p ~/.cache/voicevr && echo "{\"wake_word\": $v}" > ~/.cache/voicevr/state.json && echo "wake word $1"
