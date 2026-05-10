import sys
import time
import threading
import subprocess
import Quartz
import CoreFoundation

# Force-resolve all framework symbols used in background threads.
# PyObjC's lazy-import uses funcmap.pop() which is NOT thread-safe —
# two threads hitting the same symbol simultaneously → KeyError.
from CoreFoundation import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopGetCurrent,
    CFRunLoopAddSource,
    CFRunLoopRun,
    CFRunLoopStop,
    kCFRunLoopCommonModes,
)
from Quartz import (
    CGEventTapEnable,
    CGEventTapCreate,
    CGEventMaskBit,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    kCGHIDEventTap,
    kCGHeadInsertEventTap,
    kCGEventTapOptionDefault,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventFlagsChanged,
    kCGKeyboardEventKeycode,
    kCGKeyboardEventAutorepeat,
)

from config import cfg

_KEY_CODES = {
    # Special keys
    "space": 49, "return": 36, "enter": 36, "tab": 48, "escape": 53, "esc": 53,
    "delete": 51, "backspace": 51, "forwarddelete": 117,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    # Function keys
    "f1": 122, "f2": 120, "f3": 99,  "f4": 118, "f5": 96,
    "f6": 97,  "f7": 98,  "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
    # Numbers
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21,
    "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
    # Letters
    "a": 0,  "b": 11, "c": 8,  "d": 2,  "e": 14, "f": 3,
    "g": 5,  "h": 4,  "i": 34, "j": 38, "k": 40, "l": 37,
    "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15,
    "s": 1,  "t": 17, "u": 32, "v": 9,  "w": 13, "x": 7,
    "y": 16, "z": 6,
    # Common symbols
    "-": 27, "=": 24, "[": 33, "]": 30, "\\": 42,
    ";": 41, "'": 39, ",": 43, ".": 47, "/": 44, "`": 50,
}

_MODIFIER_FLAGS = {
    "command": Quartz.kCGEventFlagMaskCommand,
    "shift":   Quartz.kCGEventFlagMaskShift,
    "option":  Quartz.kCGEventFlagMaskAlternate,
    "alt":     Quartz.kCGEventFlagMaskAlternate,
    "control": Quartz.kCGEventFlagMaskControl,
    "ctrl":    Quartz.kCGEventFlagMaskControl,
}

_FN_FLAG    = 0x00800000  # kCGEventFlagMaskSecondaryFn
_FN_KEYCODE = 63           # kVK_Function — some Macs send keydown for Fn


def _parse_hotkey(hotkey_cfg):
    if isinstance(hotkey_cfg, dict):
        key       = hotkey_cfg.get("key", "space").lower()
        modifiers = [m.lower() for m in hotkey_cfg.get("modifiers", ["option"])]
    else:
        key       = "space"
        modifiers = ["option"]

    use_fn        = "fn" in modifiers
    need_ctrl     = any(m in modifiers for m in ("control", "ctrl"))
    # Modifier-only: no Fn, no regular key — detected purely via FlagsChanged
    modifier_only = not use_fn and not key
    key_code      = _KEY_CODES.get(key, 49)
    mod_flags     = 0
    for mod in modifiers:
        mod_flags |= _MODIFIER_FLAGS.get(mod, 0)
    return key_code, mod_flags, use_fn, need_ctrl, modifier_only


def hotkey_label(hotkey_cfg=None) -> str:
    """Human-readable hotkey string for menu bar display."""
    cfg_h = hotkey_cfg or getattr(cfg, "hotkey", {})
    mods  = cfg_h.get("modifiers", ["option"]) if isinstance(cfg_h, dict) else ["option"]
    key   = cfg_h.get("key", "space")          if isinstance(cfg_h, dict) else "space"
    _sym  = {"fn": "Fn", "control": "⌃", "ctrl": "⌃",
              "option": "⌥", "alt": "⌥", "command": "⌘", "shift": "⇧"}
    _key  = {"space": "Space", "": ""}
    mod_str = "".join(_sym.get(m.lower(), m.capitalize()) for m in mods)
    key_str = _key.get(key.lower(), key.upper())
    return f"{mod_str}+{key_str}".strip("+")


class HotkeyListener:
    def __init__(self, on_press: callable, on_release: callable,
                 mic_done=None, hotkey_cfg=None) -> None:
        self.on_press    = on_press
        self.on_release  = on_release
        self._mic_done   = mic_done
        cfg_h            = hotkey_cfg or getattr(cfg, "hotkey", {"modifiers": ["option"], "key": "space"})
        self._label      = hotkey_label(cfg_h)   # used in log messages
        (self._key_code, self._mod_flags,
         self._use_fn, self._need_ctrl,
         self._modifier_only) = _parse_hotkey(cfg_h)
        self._pressed   = False
        self._tap       = None
        self._run_loop  = None

    # ------------------------------------------------------------------ matching

    def _matches(self, event) -> bool:
        key   = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        if key != self._key_code:
            return False
        if self._mod_flags == 0:
            return True
        flags = CGEventGetFlags(event) & 0xFFFF0000
        return bool(flags & self._mod_flags)

    # ------------------------------------------------------------------ callback

    def _callback(self, proxy, event_type, event, refcon):
        if self._use_fn:
            return self._callback_fn(event_type, event)
        if self._modifier_only:
            return self._callback_modifier_only(event_type, event)
        return self._callback_key(event_type, event)

    def _callback_fn(self, event_type, event):
        """Handle Fn+<modifier(s)> via FlagsChanged AND Fn-keydown (Mac-dependent)."""
        flags = CGEventGetFlags(event)
        fn_held = bool(flags & _FN_FLAG)
        # All non-Fn modifiers configured must also be held
        extra_met = (self._mod_flags == 0) or \
                    ((flags & self._mod_flags) == self._mod_flags)

        if event_type == kCGEventFlagsChanged:
            active = fn_held and extra_met
            if active and not self._pressed:
                self._pressed = True
                try:   self.on_press()
                except Exception as e: print(f"[hotkey] on_press: {e}", file=sys.stderr)
            elif not active and self._pressed:
                self._pressed = False
                try:   self.on_release()
                except Exception as e: print(f"[hotkey] on_release: {e}", file=sys.stderr)

        # Fallback: some Macs fire keydown/up for the Fn key (keycode 63)
        elif event_type == kCGEventKeyDown:
            kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if kc == _FN_KEYCODE and extra_met and not self._pressed:
                self._pressed = True
                try:   self.on_press()
                except Exception as e: print(f"[hotkey] on_press: {e}", file=sys.stderr)
        elif event_type == kCGEventKeyUp:
            kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if kc == _FN_KEYCODE and self._pressed:
                self._pressed = False
                try:   self.on_release()
                except Exception as e: print(f"[hotkey] on_release: {e}", file=sys.stderr)

        return event  # never swallow Fn events — system needs them

    def _callback_modifier_only(self, event_type, event):
        """Handle pure modifier combos (e.g. Ctrl+Option) via FlagsChanged."""
        if event_type != kCGEventFlagsChanged:
            return event
        flags  = CGEventGetFlags(event)
        # Active when ALL configured modifier flags are held
        active = bool(self._mod_flags) and \
                 ((flags & self._mod_flags) == self._mod_flags)
        if active and not self._pressed:
            self._pressed = True
            try:   self.on_press()
            except Exception as e: print(f"[hotkey] on_press: {e}", file=sys.stderr)
        elif not active and self._pressed:
            self._pressed = False
            try:   self.on_release()
            except Exception as e: print(f"[hotkey] on_release: {e}", file=sys.stderr)
        return event

    def _callback_key(self, event_type, event):
        if event_type == kCGEventKeyDown and self._matches(event):
            if CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat):
                return None  # suppress autorepeat for hotkey only
            if not self._pressed:
                self._pressed = True
                try:   self.on_press()
                except Exception as e: print(f"[hotkey] on_press: {e}", file=sys.stderr)
            return None
        if event_type == kCGEventKeyUp and self._matches(event):
            if self._pressed:
                self._pressed = False
                try:   self.on_release()
                except Exception as e: print(f"[hotkey] on_release: {e}", file=sys.stderr)
            return None
        return event

    # ------------------------------------------------------------------ start/stop

    def start(self) -> None:
        """Start in a background thread; retries until accessibility is granted."""
        threading.Thread(target=self._start_loop, daemon=True, name="hotkey-setup").start()

    def _start_loop(self) -> None:
        # Wait for the microphone dialog to be resolved before showing the
        # accessibility prompt — prevents both dialogs appearing at once.
        if self._mic_done is not None:
            self._mic_done.wait(timeout=60)
            time.sleep(0.4)   # small gap so dialogs don't overlap

        prompted = False
        while True:
            if _is_trusted():
                if self._create_tap():
                    print(f"[hotkey] Listening: {self._label}", file=sys.stderr)
                    return
            else:
                if not prompted:
                    _prompt_accessibility()
                    prompted = True
                print("[hotkey] Waiting for Accessibility permission…", file=sys.stderr)
            time.sleep(3)

    def _create_tap(self) -> bool:
        mask = (CGEventMaskBit(kCGEventKeyDown)      |
                CGEventMaskBit(kCGEventKeyUp)         |
                CGEventMaskBit(kCGEventFlagsChanged))

        tap = CGEventTapCreate(
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            mask,
            self._callback,
            None,
        )
        if tap is None:
            print("[hotkey] CGEventTapCreate failed — retrying.", file=sys.stderr)
            return False

        self._tap = tap

        def _run():
            src = CFMachPortCreateRunLoopSource(None, tap, 0)
            self._run_loop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self._run_loop, src, kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            CFRunLoopRun()

        threading.Thread(target=_run, daemon=True, name="hotkey-runloop").start()
        return True

    def stop(self) -> None:
        if self._tap:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._run_loop:
            CFRunLoopStop(self._run_loop)


# ------------------------------------------------------------------ accessibility

def _is_trusted() -> bool:
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: False}))


def _prompt_accessibility() -> None:
    """Show the macOS accessibility permission dialog (only shown when not yet granted)."""
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
