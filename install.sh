#!/bin/bash
# FrameVoice installer. Builds everything inside this folder (nothing system-wide, no sudo):
#   whisper.cpp + llama.cpp with the Vulkan GPU backend, the VR button helper, and the models (~2.7 GB).
# Then adds the `voice` command (~/.local/bin) and a user service so `voice` can start it.
set -e
cd "$(dirname "$0")"
ROOT=$PWD
JOBS=${JOBS:-4}

missing=""
for t in git cmake gcc g++ glslc curl python3 pw-record; do command -v $t >/dev/null || missing="$missing $t"; done
[ -f /usr/include/vulkan/vulkan.h ] || missing="$missing vulkan-headers"
if [ -n "$missing" ]; then echo "Missing tools:$missing (they come with the Steam Frame's SteamOS)"; exit 1; fi
SVR=${STEAMVR_BIN:-/opt/steamvr/bin/linuxarm64}
[ -f "$SVR/libopenvr_api.so" ] || { echo "SteamVR not found at $SVR (set STEAMVR_BIN to its bin/linuxarm64 folder)"; exit 1; }
[ -f config.json ] || cp config.example.json config.json

mkdir -p third_party models deps
clone() { [ -d "third_party/$1" ] || git clone --depth 1 "$2" "third_party/$1"; }
clone whisper.cpp https://github.com/ggml-org/whisper.cpp
clone llama.cpp https://github.com/ggml-org/llama.cpp
clone SPIRV-Headers-src https://github.com/KhronosGroup/SPIRV-Headers

# SPIRV headers (SteamOS doesn't ship them; ggml's Vulkan backend needs them)
if [ ! -d deps/spirv/include/spirv ]; then
  cmake -S third_party/SPIRV-Headers-src -B third_party/SPIRV-Headers-src/build \
        -DCMAKE_INSTALL_PREFIX="$ROOT/deps/spirv" -DSPIRV_HEADERS_ENABLE_TESTS=OFF
  cmake --install third_party/SPIRV-Headers-src/build
fi

VK=(-DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$ROOT/deps/spirv"
    -DCMAKE_CXX_FLAGS="-I$ROOT/deps/spirv/include" -DCMAKE_C_FLAGS="-I$ROOT/deps/spirv/include"
    -DBUILD_SHARED_LIBS=OFF)
if [ ! -x third_party/whisper.cpp/build/bin/whisper-server ]; then
  cmake -S third_party/whisper.cpp -B third_party/whisper.cpp/build "${VK[@]}" -DWHISPER_BUILD_TESTS=OFF
  cmake --build third_party/whisper.cpp/build -j"$JOBS" --target whisper-server
fi
if [ ! -x third_party/llama.cpp/build/bin/llama-server ]; then
  cmake -S third_party/llama.cpp -B third_party/llama.cpp/build "${VK[@]}" -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF
  cmake --build third_party/llama.cpp/build -j"$JOBS" --target llama-server
fi

# VR helper: wrist mic button + status icon (SteamVR overlays)
gcc -O2 -w -Ivr/include vr/vrmic.c -o vr/vrmic -L"$SVR" -lopenvr_api -lm -Wl,-rpath,"$SVR"

# models: whisper base.en (speech) and Phi-4-mini Q4 (commands)
dl() { [ -s "models/$1" ] || { curl -L --fail -o "models/$1.part" "$2" && mv -f "models/$1.part" "models/$1"; }; }
dl ggml-base.en.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
dl phi.gguf https://huggingface.co/bartowski/microsoft_Phi-4-mini-instruct-GGUF/resolve/main/microsoft_Phi-4-mini-instruct-Q4_K_M.gguf

# `voice` command + user service (not started automatically at login)
mkdir -p ~/.local/bin ~/.config/systemd/user
install -m 755 scripts/voice ~/.local/bin/voice
sed "s|@ROOT@|$ROOT|g" scripts/voicevr.service > ~/.config/systemd/user/voicevr.service
systemctl --user daemon-reload

echo
echo "FrameVoice is installed. Type  voice  in a terminal to start it (voice stop / voice log)."
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "(~/.local/bin isn't on your PATH: run ~/.local/bin/voice instead)";; esac
