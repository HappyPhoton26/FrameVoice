"""voicevr - laser-click the mic button on your left wrist in VR, then speak.

Speech is transcribed by whisper.cpp (GPU, Vulkan). Then:
  "type ..." / "write ..." / "dictate ..."   -> the rest is typed into the focused text box
  anything else                              -> the Phi model (llama.cpp, GPU) decides: open an app,
                                                launch a Steam game, or type what you said
Apps live in config.json; Steam games are read from the installed library.
"""
import difflib
import glob
import json
import math
import queue
import threading
import os
import re
import signal
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vkeyboard import VirtualKeyboard  # noqa: E402
from xtyper import SafeXTyper  # noqa: E402
from icons import make_icons  # noqa: E402

CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "voicevr")
WHISPER_PORT, LLM_PORT = 8178, 8179


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_config():
    with open(os.path.join(ROOT, "config.json")) as f:
        return json.load(f)


# ---------- status icon ----------
# vr/vrmic shows the wrist button and a small see-through status icon in the headset: listening (it shows
# that itself the moment you click), thinking, ok, fail. No sounds.
_vr = None


def status_button(name):
    if _vr and _vr.poll() is None:
        try:
            _vr.stdin.write(f"button {name}\n")
            _vr.stdin.flush()
        except OSError:
            pass


def vr_send(line):
    if _vr and _vr.poll() is None:
        try:
            _vr.stdin.write(line + "\n")
            _vr.stdin.flush()
        except OSError:
            pass


def status(name):
    if _vr and _vr.poll() is None:
        try:
            _vr.stdin.write(f"icon {name}\n" if name else "hide\n")
            _vr.stdin.flush()
        except OSError:
            pass


# ---------- servers ----------
def wait_http(url, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception as e:
            if getattr(e, "code", None) and e.code != 503:
                return True   # server answers (404 etc.) -> it is up
            time.sleep(0.5)
    return False


def start_servers(cfg):
    procs = []
    os.makedirs(CACHE, exist_ok=True)
    env = dict(os.environ, DISABLE_GAMESCOPE_WSI="1", DISABLE_MANGOHUD="1")   # gamescope's Vulkan layer crashes headless GPU apps
    wlog = open(os.path.join(CACHE, "whisper-server.log"), "w")
    procs.append(subprocess.Popen([
        os.path.join(ROOT, "third_party/whisper.cpp/build/bin/whisper-server"),
        "-m", os.path.join(ROOT, "models", cfg["whisper_model"]),
        "--host", "127.0.0.1", "--port", str(WHISPER_PORT), "-t", "4", "-l", "en"],
        stdout=wlog, stderr=subprocess.STDOUT, env=env))
    llog = open(os.path.join(CACHE, "llama-server.log"), "w")
    procs.append(subprocess.Popen([
        os.path.join(ROOT, "third_party/llama.cpp/build/bin/llama-server"),
        "-m", os.path.join(ROOT, "models", cfg["llm_model"]),
        "--host", "127.0.0.1", "--port", str(LLM_PORT), "-ngl", "99", "-c", "2048", "-t", "4", "-np", "1"],
        stdout=llog, stderr=subprocess.STDOUT, env=env))
    ok_w = wait_http(f"http://127.0.0.1:{WHISPER_PORT}/")
    ok_l = wait_http(f"http://127.0.0.1:{LLM_PORT}/health", timeout=300)
    log(f"whisper server {'up' if ok_w else 'FAILED'}, phi server {'up' if ok_l else 'FAILED'}")
    # warm-up: the first whisper run and Phi's instructions (cached afterwards) are the slow parts
    try:
        silent = os.path.join(CACHE, "silence.wav")
        with wave.open(silent, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0" * 32000)
        transcribe(silent)
        ask_phi("hello")
    except Exception as e:
        log("warm-up failed:", e)
    return procs


def transcribe(wav_path):
    boundary = uuid.uuid4().hex
    with open(wav_path, "rb") as f:
        audio = f.read()
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
        f"Content-Type: audio/wav\r\n\r\n".encode() + audio + b"\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"temperature\"\r\n\r\n0\r\n".encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(f"http://127.0.0.1:{WHISPER_PORT}/inference", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    text = json.loads(urllib.request.urlopen(req, timeout=60).read())["text"]
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)   # [BLANK_AUDIO], (music) ...
    return " ".join(text.split())


def mic_level(path):
    """RMS of a 16-bit mono wav (0 = digital silence)."""
    try:
        with wave.open(path) as w:
            n = w.getnframes()
            d = struct.unpack(f"<{n}h", w.readframes(n))
        return math.sqrt(sum(x * x for x in d) / n) if n else 0.0
    except (OSError, wave.Error, struct.error):
        return 0.0


# ---------- targets ----------
LAUNCH_WORDS = re.compile(r"\b(open|launch|start|run|play|load|bring up|fire up|boot|switch to|go to|go back to|"
                          r"show me|i need|i want|give me|terminal|browser|desktop|firefox|konsole)\b", re.I)
SKIP_GAME = re.compile(r"proton|steam linux runtime|steamworks|steamvr|redistributable|fex-emu|^fex$|legacy steam runtime", re.I)


def steam_games(cfg):
    games = {}
    for lib in cfg.get("steam_libraries", ["~/.local/share/Steam"]):
        for acf in glob.glob(os.path.join(os.path.expanduser(lib), "steamapps", "appmanifest_*.acf")):
            try:
                s = open(acf, errors="replace").read()
                appid = re.search(r'"appid"\s+"(\d+)"', s).group(1)
                name = re.search(r'"name"\s+"([^"]+)"', s).group(1)
            except (AttributeError, OSError):
                continue
            if not SKIP_GAME.search(name):
                games[name] = appid
    return games


NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
           "eight": "8", "nine": "9", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7"}


def norm(s):
    """Lower case, punctuation -> spaces, numbers as digits ("II", "two" -> "2"), so names compare fairly."""
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and ").replace("'", ""))
    return " ".join(NUMBERS.get(w, w) for w in s.split())


def best_match(phrase, names):
    """Closest name to the spoken phrase, with a 0..1 score."""
    p = norm(phrase)
    best, score = None, 0.0
    for n in names:
        q = norm(n)
        r = difflib.SequenceMatcher(None, p, q).ratio()
        if q == p:
            r = 1.0
        elif q and len(p) > 3 and (q + " ").startswith(p + " "):
            r = max(r, 0.9 + 0.05 * len(p) / len(q))   # start of the name, whole words; shortest wins: "half life 2" -> "Half-Life 2: VR Mod"
        elif q and len(p) > 3 and p in q:
            r = max(r, 0.9)
        if r > score:
            best, score = n, r
    return best, score


# ---------- Phi ----------
# Phi only says WHETHER to launch something and what it is called; Python then fuzzy-matches the name.
# A short answer keeps it fast: this GPU generates ~10 tokens/s while SteamVR is rendering.
PHI_SYSTEM = (
    "You sort voice requests from a VR headset user. If they want to open, start or play an app or game, "
    "answer LAUNCH followed by its name. If they are dictating text to type, answer TYPE.\n"
    "Examples:\n"
    "can you bring up the web browser -> LAUNCH browser\n"
    "I need a terminal -> LAUNCH terminal\n"
    "let's play some table tennis -> LAUNCH table tennis\n"
    "fire up half life alyx -> LAUNCH half life alyx\n"
    "go back to the desktop -> LAUNCH desktop\n"
    "hello everyone, good game -> TYPE\n"
    "I'll be back in five minutes -> TYPE\n"
    "we should play again tomorrow -> TYPE"
)
PHI_GRAMMAR = 'root ::= "TYPE" | "LAUNCH " [a-zA-Z0-9 :\'-]{1,40}'


def ask_phi(text):
    """Returns the name to launch, or None to type the text."""
    body = {
        "messages": [{"role": "system", "content": PHI_SYSTEM}, {"role": "user", "content": text + " ->"}],
        "temperature": 0, "max_tokens": 12, "cache_prompt": True, "grammar": PHI_GRAMMAR,
    }
    req = urllib.request.Request(f"http://127.0.0.1:{LLM_PORT}/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=30).read())["choices"][0]["message"]["content"].strip()
    return out[7:].strip() if out.startswith("LAUNCH ") else None


# ---------- actions ----------
def launch_env():
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env["DISPLAY"] = env.get("VOICEVR_DISPLAY", ":0")   # gamescope's X server: each window becomes a VR panel
    env.pop("WAYLAND_DISPLAY", None)
    return env


def run_detached(cmd):
    subprocess.Popen(cmd, shell=True, env=launch_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_app(app):
    check = app.get("running_check")
    if check and subprocess.run(["pgrep", "-f", check], stdout=subprocess.DEVNULL).returncode == 0:
        log(f"{app['name']} is already running")
        return
    log("launching app", app["name"])
    run_detached(app["command"])


def launch_game(name, appid):
    log("launching game", name, appid)
    run_detached(f"steam steam://rungameid/{appid}")


class VoiceVR:
    def __init__(self):
        self.cfg = load_config()
        self.apps = {a["name"]: a for a in self.cfg["apps"]}
        self.alias = {}
        for a in self.cfg["apps"]:
            for n in [a["name"]] + a.get("aliases", []):
                self.alias[n] = a["name"]
        self.games = steam_games(self.cfg)
        log(f"{len(self.apps)} apps, {len(self.games)} games")
        if self.cfg.get("typing", "xtest") == "uinput":
            self.kb = VirtualKeyboard()
        else:
            self.kb = SafeXTyper(os.environ.get("VOICEVR_DISPLAY", ":0").encode())
        self.wav = os.path.join(tempfile.gettempdir(), f"voicevr-{os.getuid()}.wav")
        self.state_file = os.path.join(CACHE, "state.json")
        try:
            self.wake_on = json.load(open(self.state_file)).get("wake_word", True)
        except (OSError, ValueError):
            self.wake_on = self.cfg.get("wake_word_enabled", True)
        self.wake = self.cfg.get("wake_word", "computer")
        self.wake_re = re.compile(r"^\W*(?:(?:hey|ok|okay)\s+)?" + re.escape(self.wake) + r"\b[\s,.!?:;-]*(.*)$", re.I)
        self.lock = threading.Lock()
        self.ptt = None            # bytearray while the wrist button is held
        self.t_down = 0
        self.armed_until = 0       # after a bare "computer": the next phrase is the command
        self.jobs = queue.Queue()
        self.proc = None           # pw-record
        self.servers = []          # whisper-server, llama-server while active
        self.mode = "standby"      # active / waking / standby (servers stopped, mic closed, small button)
        self.last_use = time.time()
        self.request_file = os.path.join(CACHE, "request")   # `voice standby` / `voice wake` write here
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.watch_state, daemon=True).start()
        threading.Thread(target=self.auto_standby, daemon=True).start()
        self.update_mic()

    # ---------- standby ----------
    # Standby stops whisper and Phi completely (frees ~3 GB and the GPU) and shrinks the wrist button to a
    # small grey dot; clicking it starts them again (~20 s, amber button), then it is ready to talk.
    def wake_up(self, wait=False):
        with self.lock:
            if self.mode != "standby":
                return
            self.mode = "waking"
        vr_send("mode waking")
        log("waking up...")
        t = threading.Thread(target=self._start)
        t.start()
        if wait:
            t.join()

    def _start(self):
        self.servers = start_servers(self.cfg)
        self.last_use = time.time()
        self.mode = "active"
        vr_send("mode active")
        self.update_mic()
        log("ready: laser-click the mic button on your left wrist, then talk (or hold the trigger on it while talking)")

    def standby(self):
        with self.lock:
            if self.mode != "active":
                return
            self.mode = "standby"
            self.ptt = None
        self.armed_until = 0
        self.update_mic()
        vr_send("mode standby")
        for p in self.servers:
            p.terminate()
        for p in self.servers:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.servers = []
        log("standby: whisper and Phi stopped; click the small wrist button to wake up")

    def auto_standby(self):
        mins = self.cfg.get("standby_after_min", 10)
        while mins:
            time.sleep(30)
            busy = self.ptt is not None or time.time() < self.armed_until or not self.jobs.empty()
            if self.mode == "active" and not busy and time.time() - self.last_use > mins * 60:
                log(f"nothing said for {mins} min")
                self.standby()

    # ---------- microphone ----------
    # One pw-record stream feeds both the wrist button and the wake word. It only runs while the wake word
    # is on or the button is held, so "computer, disable" leaves the mic (and GPU) completely idle.
    CHUNK = 480 * 2   # 30 ms of 16 kHz mono s16

    def update_mic(self):
        need = self.mode == "active" and (self.wake_on or self.ptt is not None or time.time() < self.armed_until)
        if need and not self.proc:
            cmd = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "--raw"]
            if self.cfg.get("mic_target"):
                cmd += ["--target", self.cfg["mic_target"]]
            self.proc = subprocess.Popen(cmd + ["-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()
        elif not need and self.proc:
            self.proc.terminate()
            self.proc = None
        status_button("button")

    def reader(self, proc):
        pre = []                   # ~0.3 s before speech starts
        seg, loud, quiet, floor = None, 0, 0, 200.0
        while True:
            data = proc.stdout.read(self.CHUNK)
            if not data:
                return
            n = len(data) // 2
            rms = math.sqrt(sum(x * x for x in struct.unpack(f"<{n}h", data[:n * 2])) / max(1, n))
            with self.lock:
                if self.ptt is not None:
                    self.ptt += data
                    seg = None
                    continue
            if not self.wake_on and seg is None and time.time() >= self.armed_until:
                continue
            # a cheap loudness gate; whisper only runs on phrases that stand out from the noise
            if seg is None:
                floor = 0.97 * floor + 0.03 * rms if rms < floor * 3 else floor
                loud = loud + 1 if rms > max(3 * floor, 300) else 0
                pre = (pre + [data])[-10:]
                if loud >= 3:
                    seg, quiet = bytearray(b"".join(pre)), 0
            else:
                seg += data
                quiet = quiet + 1 if rms < max(2 * floor, 200) else 0
                secs = len(seg) / 32000
                if quiet >= 20 or secs > 10:      # 0.6 s of quiet ends the phrase
                    if 0.5 <= secs <= 10:
                        self.jobs.put(("wake", bytes(seg)))
                    seg, loud, pre = None, 0, []

    # quick click on the wrist button: like saying a bare "computer" (the next phrase is the command)
    def arm(self, secs=6):
        self.last_use = time.time()
        self.armed_until = time.time() + secs
        log("button: listening for a command")
        status("listening")
        self.update_mic()
        threading.Timer(secs + 0.5, self.disarm_if_idle).start()

    def disarm_if_idle(self):
        if self.armed_until and time.time() >= self.armed_until:
            self.armed_until = 0
            status("fail")   # nothing was said
        self.update_mic()

    # wrist mic button (push to talk)
    def start_listening(self):
        with self.lock:
            if self.ptt is not None:
                return
            self.ptt = bytearray()
        self.t_down = self.last_use = time.time()
        self.update_mic()
        log("listening...")

    def stop_listening(self):
        if self.ptt is None:
            return
        time.sleep(0.15)   # keep the tail of the last word
        with self.lock:
            if self.ptt is None:   # went to standby meanwhile
                return
            pcm, self.ptt = bytes(self.ptt), None
        self.update_mic()
        if time.time() - self.t_down < 0.5:
            log("too short, ignored")
            status(None)
            return
        status("thinking")
        self.jobs.put(("button", pcm))

    def worker(self):
        while True:
            kind, pcm = self.jobs.get()
            self.last_use = time.time()
            try:
                self.process(kind, pcm)
            except Exception as e:
                log("error:", e)
                status("fail")

    def process(self, kind, pcm):
        with wave.open(self.wav, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
        if kind == "button":
            level = mic_level(self.wav)
            log(f"mic level {level:.0f}")
            if level < 1:
                log("no sound from the microphone")
                status("fail")
                return
        t0 = time.time()
        text = transcribe(self.wav)
        m = self.wake_re.match(text)
        if kind == "wake":
            if m:
                text = m.group(1)
            elif time.time() < self.armed_until:
                self.armed_until = 0
            else:
                return                 # speech without the wake word: ignored, not logged
            if not text.strip(" .,!?"):
                log(f"heard {self.wake!r}: waiting for the command")
                self.armed_until = time.time() + 6
                status("listening")
                return
            status("thinking")
        elif m:
            text = m.group(1)          # "computer, ..." said on the button works too
        log(f"heard ({time.time() - t0:.1f}s): {text!r}")
        if not text.strip(" .,!?"):
            status("fail")
            return
        if self.control(text):
            return
        self.handle(text)

    def watch_state(self):
        """./wake.sh on|off edits state.json; pick that up (the mic is closed while off, so speech can't)."""
        while True:
            time.sleep(2)
            try:
                req = open(self.request_file).read().strip()
                os.remove(self.request_file)
            except OSError:
                req = ""
            if req == "standby":
                threading.Thread(target=self.standby).start()
            elif req == "wake":
                self.wake_up()
            try:
                on = json.load(open(self.state_file)).get("wake_word", True)
            except (OSError, ValueError):
                continue
            if on != self.wake_on:
                self.wake_on = on
                self.update_mic()
                log("wake word", "on" if on else "off", "(from wake.sh)")

    def control(self, text):
        """standby / go to sleep: standby.  computer, disable / enable: the always-listening wake word off / on."""
        t = norm(text)
        if re.fullmatch(r"((go to )?(standby|stand by|sleep)|goodnight|good night)", t):
            status("ok")
            threading.Timer(1.3, self.standby).start()   # let the green check show first
            return True
        if re.fullmatch(r"(disable|stop listening|mute|turn off|wake word off)", t):
            on = False
        elif re.fullmatch(r"(enable|start listening|wake up|unmute|turn on|wake word on)", t):
            on = True
        else:
            return False
        self.wake_on = on
        try:
            with open(self.state_file, "w") as f:
                json.dump({"wake_word": on}, f)
        except OSError:
            pass
        self.update_mic()
        log("wake word", "on" if on else "off (mic closed; turn it back on with ./wake.sh on)")
        status("ok")
        return True

    # what to do with the words
    def find(self, phrase, min_score):
        """App or game whose name best matches the phrase -> ("app"|"game", name) or None."""
        name, score = best_match(phrase, self.alias.keys())
        if score >= min_score:
            return "app", self.alias[name]
        name, score = best_match(phrase, self.games.keys())
        if score >= min_score:
            return "game", name
        return None

    def launch(self, hit):
        kind, name = hit
        if kind == "app":
            launch_app(self.apps[name])
        else:
            launch_game(name, self.games[name])
        status("ok")

    def handle(self, text):
        # whisper sometimes glues the verb to a name: "OpenKDE." -> "Open KDE."
        text = re.sub(r"^(\W*(?i:open|launch|start|play|run))(?=[A-Z0-9])", r"\1 ", text)
        m = re.match(r"^\s*(type|write|dictate)\b[\s,:.]*(.*)$", text, re.I)
        if m:
            self.type(m.group(2))
            return
        if not LAUNCH_WORDS.search(text):
            self.type(text)          # plain dictation: no model needed
            return
        m = re.match(r"^\s*(?:please\s+)?(?:open|launch|start|run|play|load)\b\s*(?:up\s+)?(?:the\s+|a\s+)?(.*?)[.!?]*$", text, re.I)
        if m:
            hit = self.find(m.group(1), 0.8)
            if hit:
                self.launch(hit)
                return
        t0 = time.time()
        name = ask_phi(text)
        log(f"phi ({time.time() - t0:.1f}s): {name!r}")
        hit = name and self.find(name, 0.5)
        if hit:
            self.launch(hit)
        elif name:
            log("nothing installed matches", name)
            status("fail")
        else:
            self.type(text)

    def type(self, text):
        text = text.strip()
        if not text:
            return
        log("typing:", text)
        if self.cfg.get("space_after_dictation", True):
            text += " "
        try:
            self.kb.type_text(text)
        except Exception as e:   # e.g. the X display went away mid-typing
            log("typing failed:", e)
            status("fail")
            return
        status("ok")


def main():
    global _vr
    make_icons(os.path.join(CACHE, "icons"))
    cfg = load_config()
    vr = None
    vv = VoiceVR()
    try:
        if not cfg.get("start_in_standby", False):
            vv.wake_up(wait=True)
        else:
            log("standby: click the small wrist button to wake up")
        mb = cfg.get("mic_button", {})
        wb = cfg.get("wrist_button", {})
        args = [os.path.join(ROOT, "vr/vrmic"), os.path.join(CACHE, "icons"),
                *[str(v) for v in mb.get("offset", [0, -0.17, -0.6])], str(mb.get("width_m", 0.026)),
                *[str(v) for v in wb.get("offset", [0, 0.02, 0.13])], str(wb.get("tilt_deg", -60)),
                str(wb.get("width_m", 0.04))]
        while True:   # restart the VR helper if SteamVR restarts
            vr = _vr = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            vr_send("mode " + ("waking" if vv.mode == "waking" else vv.mode))
            vv.update_mic()   # sends the button colour
            for line in vr.stdout:
                line = line.strip()
                if line == "wake":
                    vv.wake_up()
                elif vv.mode != "active":
                    continue
                elif line == "tap":
                    vv.arm()
                elif line == "down":
                    vv.start_listening()
                elif line == "up":
                    vv.stop_listening()
            vv.stop_listening()
            log("VR helper exited; restarting in 5 s")
            time.sleep(5)
    finally:
        for p in vv.servers + ([vr] if vr else []):
            p.terminate()


if __name__ == "__main__":
    main()
