"""A virtual keyboard through /dev/uinput (no extra Python packages), used to type dictated text.

The user must be able to write /dev/uinput (on SteamOS the `input` group can).
"""
import fcntl
import os
import struct
import time

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_SETUP = 0x405c5503
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
EV_SYN, EV_KEY = 0, 1

KEY = {
    "esc": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "-": 12, "=": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "[": 26, "]": 27, "enter": 28, "ctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38, ";": 39, "'": 40, "`": 41,
    "shift": 42, "\\": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50, ",": 51, ".": 52, "/": 53,
    " ": 57, "alt": 56,
}
# shifted characters on a US layout -> the unshifted key
SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";", '"': "'", "~": "`", "<": ",", ">": ".", "?": "/",
}
# typographic characters whisper likes to produce
PLAIN = {"’": "'", "‘": "'", "“": '"', "”": '"', "—": "-", "–": "-", "…": "...", "\n": " "}


class VirtualKeyboard:
    def __init__(self, delay=0.006):
        self.delay = delay
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
        for code in set(KEY.values()):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        # struct uinput_setup { input_id {bustype, vendor, product, version}; char name[80]; u32 ff_effects_max; }
        setup = struct.pack("HHHH80sI", 0x06, 0x1234, 0x5678, 1, b"voicevr virtual keyboard", 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.5)   # let the compositor pick the new device up before the first key

    def _emit(self, typ, code, value):
        t = time.time()
        os.write(self.fd, struct.pack("llHHi", int(t), int((t % 1) * 1e6), typ, code, value))

    def _key(self, code, down):
        self._emit(EV_KEY, code, 1 if down else 0)
        self._emit(EV_SYN, 0, 0)
        time.sleep(self.delay)

    def tap(self, name, shift=False):
        if shift:
            self._key(KEY["shift"], True)
        self._key(KEY[name], True)
        self._key(KEY[name], False)
        if shift:
            self._key(KEY["shift"], False)

    def type_text(self, text):
        for ch in text:
            ch = PLAIN.get(ch, ch)
            for c in ch:
                if c in KEY:
                    self.tap(c)
                elif c.lower() in KEY and c.isalpha():
                    self.tap(c.lower(), shift=True)
                elif c in SHIFTED:
                    self.tap(SHIFTED[c], shift=True)
                # anything else (accents, emoji) is skipped

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)
