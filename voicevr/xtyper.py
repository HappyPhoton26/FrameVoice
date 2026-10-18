"""Types text into the focused window of an X display with the XTEST extension (libX11 + libXtst, via ctypes).

Gamescope's Xwayland (:0) hosts Steam and the apps voicevr launches, and it accepts XTEST input,
while a /dev/uinput keyboard may never be opened by the VR compositor.
"""
import ctypes
import os
import subprocess
import sys
import time

XK = {" ": "space", "\n": "Return", "\t": "Tab", "!": "exclam", '"': "quotedbl", "#": "numbersign", "$": "dollar",
      "%": "percent", "&": "ampersand", "'": "apostrophe", "(": "parenleft", ")": "parenright", "*": "asterisk",
      "+": "plus", ",": "comma", "-": "minus", ".": "period", "/": "slash", ":": "colon", ";": "semicolon",
      "<": "less", "=": "equal", ">": "greater", "?": "question", "@": "at", "[": "bracketleft", "\\": "backslash",
      "]": "bracketright", "^": "asciicircum", "_": "underscore", "`": "grave", "{": "braceleft", "|": "bar",
      "}": "braceright", "~": "asciitilde"}
PLAIN = {"’": "'", "‘": "'", "“": '"', "”": '"', "—": "-", "–": "-", "…": "..."}


class XTyper:
    def __init__(self, display=b":0", delay=0.004):
        self.x = ctypes.CDLL("libX11.so.6")
        self.t = ctypes.CDLL("libXtst.so.6")
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x.XStringToKeysym.restype = ctypes.c_ulong
        self.x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.x.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x.XKeycodeToKeysym.restype = ctypes.c_ulong
        self.x.XKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int]
        self.x.XFlush.argtypes = [ctypes.c_void_p]
        self.x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.t.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.d = self.x.XOpenDisplay(display)
        if not self.d:
            raise RuntimeError(f"cannot open X display {display.decode()}")
        self.delay = delay
        self.shift = self.x.XKeysymToKeycode(self.d, self.x.XStringToKeysym(b"Shift_L"))

    def _press(self, code, shift):
        if shift:
            self.t.XTestFakeKeyEvent(self.d, self.shift, 1, 0)
        self.t.XTestFakeKeyEvent(self.d, code, 1, 0)
        self.t.XTestFakeKeyEvent(self.d, code, 0, 0)
        if shift:
            self.t.XTestFakeKeyEvent(self.d, self.shift, 0, 0)
        self.x.XFlush(self.d)
        time.sleep(self.delay)

    def type_text(self, text):
        for ch in text:
            for c in PLAIN.get(ch, ch):
                ks = self.x.XStringToKeysym(XK.get(c, c).encode())
                code = self.x.XKeysymToKeycode(self.d, ks) if ks else 0
                if not code:
                    continue   # not on the keymap (accents, emoji): skipped
                # shifted if the keysym only sits on the keycode's second level
                self._press(code, self.x.XKeycodeToKeysym(self.d, code, 0) != ks)
        self.x.XSync(self.d, 0)

    def close(self):
        pass


class SafeXTyper:
    """Types through a short-lived `python3 xtyper.py` child with its own X connection.

    libX11 calls exit() on a fatal IO error (e.g. the display resets when an app launches), which
    would kill voicevr itself; in a child only that one typing run is lost.
    """
    def __init__(self, display=b":0"):
        self.display = display.decode() if isinstance(display, bytes) else display
        XTyper(display)   # fail early if the display can't be opened

    def type_text(self, text):
        r = subprocess.run([sys.executable, os.path.abspath(__file__), self.display], input=text.encode(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
        if r.returncode:
            raise RuntimeError(f"typing failed: {r.stderr.decode(errors='replace').strip()[-200:]}")

    def close(self):
        pass


if __name__ == "__main__":
    XTyper(sys.argv[1].encode() if len(sys.argv) > 1 else b":0").type_text(sys.stdin.buffer.read().decode())
