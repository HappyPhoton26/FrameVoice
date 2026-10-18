# FrameVoice

Voice control for the **Steam Frame**. Click a mic button on your wrist in VR, speak, and FrameVoice
launches apps and Steam games or types what you said into the focused text box.

Everything runs on the headset itself (no cloud, no account). Speech is turned into text by
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) (`base.en`). Unclear requests go to Microsoft's
**Phi-4-mini** through [llama.cpp](https://github.com/ggml-org/llama.cpp). Both run on the headset's GPU (Vulkan).

How the code fits together, with block diagrams: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What you can say

| say | what happens |
|---|---|
| "open Firefox", "open terminal", "start the desktop" | launches an app from `config.json` |
| "play Beat Saber", "fire up Half-Life Alyx" | launches an installed Steam game |
| "can you bring up the web browser" | Phi works out what you meant |
| "type see you soon", or anything without a launch word | typed into the focused text box |

## Install

You need a Steam Frame with SteamVR running. The build tools FrameVoice needs (git, cmake, gcc,
glslc, Vulkan headers) come with the Frame's SteamOS. No `sudo` and no system changes are needed.

1. Open a terminal on the Frame (for example Konsole in Desktop mode).
2. Get the code:

   ```
   git clone git@github.com:HappyPhoton26/FrameVoice.git ~/FrameVoice
   cd ~/FrameVoice
   ```

   (The repo is private. You need to be added to it, and your SSH key must be on your GitHub account.
   You can also download it as a ZIP from GitHub and unzip it to `~/FrameVoice`.)
3. Run the installer:

   ```
   ./install.sh
   ```

   This takes roughly 15–30 minutes. It downloads and builds whisper.cpp and llama.cpp for the GPU,
   builds the VR button, downloads the two models (~2.7 GB), and adds a `voice` command.
   Everything stays inside the `FrameVoice` folder, except the `voice` command (`~/.local/bin/voice`)
   and a user service (`~/.config/systemd/user/voicevr.service`).
4. Start it:

   ```
   voice
   ```

   It prints `voicevr ready` after about 30 s, once the models are loaded. It keeps running after you close
   the terminal. `voice stop` stops it, `voice standby` / `voice wake` switch standby, and `voice log`
   shows what it hears. It does **not** start at
   login by itself. Run `systemctl --user enable voicevr` if you want it to.

## Using it

- Look at the back of your **left wrist**: there is a small red mic button.
- Point the **right-hand laser** at it and **click**, then speak. The phrase ends when you stop
  talking. If you say nothing for 6 s, it gives up.
- Or **hold the trigger** on the button while you talk and let go when you're done.
- **Click the button again** while it's listening to stop right away. If you had started talking,
  what you said is used. Otherwise it cancels.

### Standby

When you're not using it, FrameVoice can go into standby. Whisper and Phi shut down completely
(freeing about 3.5 GB of memory and the GPU), the microphone is closed, and the wrist button
shrinks to a **small grey dot**.

- **Go to standby**: click the small grey **power button** beside the mic button, say "standby" (or
  "go to sleep"), or run `voice standby`. It also goes into standby
  by itself after 10 minutes without use (`standby_after_min` in `config.json`, `0` = never).
- **Wake up**: click the small grey dot. It grows and turns amber while the models load (about 20 s),
  then turns red: ready to talk. You can also run `voice wake`.

A small icon at the bottom of your view shows what is happening:

| icon | meaning |
|---|---|
| red mic | listening (the wrist button itself is red when ready, amber while working or waking up, a small grey dot in standby) |
| amber mic | working on it |
| green check | done (launched or typed) |
| red X | didn't catch that, no microphone sound, or nothing installed matches |

There are no sounds.

## Settings (`config.json`)

The installer creates `config.json` from `config.example.json`. Your own settings live there and git
doesn't track them.

- `apps`: the apps you can open. Each has a `name`, spoken `aliases` (add words whisper often
  mishears), the shell `command`, and an optional `running_check` (a process name, so a running app isn't
  started twice). Restart with `voice stop` then `voice` after editing.
- `wrist_button`: where the button sits on the left controller (`offset` x/y/z in metres,
  `tilt_deg`, `width_m`).
- `mic_button`: where the status icon sits in your view.
- `standby_after_min`: minutes without use before standby (`0` = never). `start_in_standby`: start as the
  grey dot, so the models only load when you first click it.
- `mic_target`: a PipeWire microphone name, if the default mic is the wrong one.
- `typing`: `"xtest"` (default) or `"uinput"`.
- `wake_word_enabled`: hands-free "computer, open firefox". It's off by default because it keeps the mic and
  some CPU running. You can also switch it with `./wake.sh on|off`.

Installed Steam games are found automatically from your Steam library ("play Half-Life 2", "open Moss
Book Two": numbers and Roman numerals are treated the same). To add another app, copy an entry in `apps`.
For example, VS Code installed from Flathub:

```
{"name": "VS Code", "aliases": ["visual studio code", "visual code", "vs code"],
 "command": "flatpak run com.visualstudio.code", "running_check": "com.visualstudio.code"}
```

## Speed

- Plain dictation and clear commands ("open X", "play Y") skip Phi: about 0.5 s after you finish.
- Requests that need Phi take about 1–1.5 s.
- While idle, the microphone is closed and nothing runs on the GPU. The models stay loaded in memory
  (about 3.5 GB) so they answer right away, until standby unloads them.

## Troubleshooting

- `voice log` shows what was heard and what it did.
- **No button in VR**: SteamVR must be running before `voice`. FrameVoice reconnects by itself if
  SteamVR restarts.
- **Red X and "no sound from the microphone" in the log**: pick the right mic with `mic_target`
  (list them with `pw-cli ls Node | grep -A3 Audio/Source`).
- **Typing does nothing**: click into the text box first. Text is typed into the focused window on
  gamescope's X display `:0`.
- The GPU servers run with `DISABLE_GAMESCOPE_WSI=1` because gamescope's Vulkan layer crashes
  headless GPU programs.

## Uninstall

```
voice stop
rm -rf ~/FrameVoice ~/.local/bin/voice ~/.config/systemd/user/voicevr.service ~/.cache/voicevr
```

## Files

- `install.sh`: builds and downloads everything. `run.sh`: what the service runs.
- `voicevr/main.py`: microphone, whisper and Phi calls, and the app/game launcher.
- `voicevr/xtyper.py`: typing. `voicevr/icons.py`: draws the button and status icons.
- `vr/vrmic.c`: the SteamVR overlays (wrist button and status icon).
- `vr/include/openvr/openvr_capi.h`: Valve's OpenVR header (BSD 3-clause).
- `scripts/voice`, `scripts/voicevr.service`: the `voice` command and its user service.
