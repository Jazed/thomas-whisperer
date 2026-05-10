import sys
import time
import threading
from pathlib import Path
import objc
from AppKit import (
    NSObject, NSApplication,
    NSStatusBar, NSVariableStatusItemLength,
    NSMenu, NSMenuItem,
    NSImage, NSColor, NSBezierPath, NSMakeRect,
)
from Foundation import NSMakeSize

# Lightweight imports only — heavy libs (numpy, sounddevice, faster_whisper)
# are loaded in _async_init so the UI appears instantly.
from overlay import OverlayPanel
from hotkey import HotkeyListener, hotkey_label, _is_trusted
from config import cfg, watch as watch_config

_LOG_FILE = Path.home() / ".thomas-voice" / "app.log"


def _log(msg: str) -> None:
    _LOG_FILE.parent.mkdir(exist_ok=True)
    line = f"{time.strftime('%H:%M:%S')} {msg}\n"
    print(line, end="", file=sys.stderr)
    with open(_LOG_FILE, "a") as f:
        f.write(line)


class AppController(NSObject):
    def init(self):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self._state            = "idle"
        self._overlay          = None
        self._hotkey           = None
        self._translate_hotkey = None
        self._status_item      = None
        self._record_start     = 0.0
        self._overlay_shown    = True
        self._toggle_item      = None
        self._libs_ready       = False
        return self

    # ------------------------------------------------------------------ launch

    def applicationDidFinishLaunching_(self, notification):
        # 1. Show UI immediately — no heavy imports yet
        try:
            self._overlay = OverlayPanel()
            self._overlay.set_state("idle")
        except Exception as e:
            _log(f"OVERLAY INIT FAILED: {e}")

        try:
            self._setup_menu_bar()
        except Exception as e:
            _log(f"MENUBAR INIT FAILED: {e}")

        # 2. Event shared between async_init (mic) and hotkey (accessibility)
        #    so permission dialogs appear sequentially, not simultaneously.
        self._mic_done = threading.Event()

        # 3. Start hotkey listeners
        self._hotkey = HotkeyListener(
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release,
            mic_done=self._mic_done,
        )
        self._hotkey.start()

        t_cfg = getattr(cfg, "translate_hotkey", None)
        if t_cfg:
            self._translate_hotkey = HotkeyListener(
                on_press=self._on_translate_press,
                on_release=self._on_translate_release,
                mic_done=self._mic_done,
                hotkey_cfg=t_cfg,
            )
            self._translate_hotkey.start()

        # 4. Load heavy libs + warm up Whisper in background
        threading.Thread(target=self._async_init, daemon=True).start()

        # 5. Watch config.json for live edits
        watch_config(on_reload=self._on_config_reload)

        t_label = f"  •  {hotkey_label(t_cfg)} translate" if t_cfg else ""
        _log(f"UI ready. Hotkey: {hotkey_label()} transcribe{t_label}")

    def _async_init(self) -> None:
        """Import numpy/sounddevice/whisper off the main thread."""
        import audio        # noqa — loads numpy + sounddevice
        import transcribe   # noqa — loads faster_whisper lazily
        import inject       # noqa
        import dictionary   # noqa
        import history      # noqa

        # Request mic permission first; warm stream in the callback.
        # Sets _mic_done so the hotkey loop knows mic dialog is resolved.
        def _on_mic(granted):
            if granted:
                try:
                    audio.warm()
                except Exception as e:
                    _log(f"Mic warm error: {e}")
            else:
                _log("Microphone permission denied.")
            self._mic_done.set()

        audio.request_permission(_on_mic)
        # Wait for the mic dialog to be handled (up to 60 s) before marking ready
        self._mic_done.wait(timeout=60)
        self._libs_ready = True
        _log("Audio engine ready.")
        try:
            transcribe.warm_up()
        except Exception as e:
            _log(f"warm_up error: {e}")

    # ------------------------------------------------------------------ menu bar

    @staticmethod
    def _make_logo_image(size: int = 22) -> NSImage:
        img = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
        img.lockFocus()
        cx = cy = size / 2
        r = size / 2 * 0.82
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, size, size))
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.10, 0.42, 1.0).set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx-r, cy-r, r*2, r*2)).fill()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.28, 0.38, 0.82, 0.45).set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx-r*.62, cy-r*.62, r*1.24, r*1.24)).fill()
        bar_heights = [0.40, 0.68, 1.00, 0.68, 0.40]
        bw = r * 0.15; gap = r * 0.12
        total = len(bar_heights) * bw + (len(bar_heights)-1) * gap
        x0 = cx - total / 2; max_h = r * 0.80
        NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.92).set()
        for i, frac in enumerate(bar_heights):
            bh = frac * max_h; bx = x0 + i * (bw + gap); by = cy - bh / 2
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(bx, by, bw, bh), bw/2, bw/2
            ).fill()
        img.unlockFocus()
        img.setTemplate_(False)
        return img

    def _setup_menu_bar(self) -> None:
        sb = NSStatusBar.systemStatusBar()
        self._status_item = sb.statusItemWithLength_(NSVariableStatusItemLength)
        self._status_item.button().setImage_(self._make_logo_image(22))
        self._status_item.button().setTitle_("")

        menu = NSMenu.alloc().init()

        t_cfg   = getattr(cfg, "translate_hotkey", None)
        t_label = f"   {hotkey_label(t_cfg)} translate" if t_cfg else ""
        info = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"ThomasWhisperer  •  {hotkey_label()} transcribe{t_label}", None, ""
        )
        info.setEnabled_(False)
        menu.addItem_(info)
        menu.addItem_(NSMenuItem.separatorItem())

        self._toggle_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Hide ThomasWhisperer", "toggleOverlay:", ""
        )
        self._toggle_item.setTarget_(self)
        menu.addItem_(self._toggle_item)
        menu.addItem_(NSMenuItem.separatorItem())

        for title, action in [("Open config.json…",  "openConfig:"),
                               ("Reload Config",      "reloadConfig:"),
                               ("View history…",      "openHistory:")]:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(self)
            menu.addItem_(item)

        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit", "terminate:", "q")
        quit_item.setTarget_(NSApplication.sharedApplication())
        menu.addItem_(quit_item)

        self._status_item.setMenu_(menu)

    def toggleOverlay_(self, sender):
        if self._overlay_shown:
            self._overlay.hide()
            self._toggle_item.setTitle_("Show ThomasWhisperer")
        else:
            self._overlay.show()
            self._toggle_item.setTitle_("Hide ThomasWhisperer")
        self._overlay_shown = not self._overlay_shown

    @objc.python_method
    def _on_config_reload(self):
        _log("Config reloaded from disk.")

    def reloadConfig_(self, sender):
        from config import reload
        reload()
        _log("Config manually reloaded.")

    def openConfig_(self, sender):
        import subprocess
        from config import CONFIG_PATH
        subprocess.Popen(["open", str(CONFIG_PATH)])

    def openHistory_(self, sender):
        import subprocess
        from pathlib import Path
        import history
        path = Path(history.get_history_path())
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_text("")   # create so Finder can reveal it
        # Reveal in Finder — works whether the file is empty or has entries
        subprocess.Popen(["open", "-R", str(path)])

    # ------------------------------------------------------------------ hotkey

    @objc.python_method
    def _on_hotkey_press(self) -> None:
        if self._state != "idle" or not self._libs_ready:
            return
        import audio
        try:
            audio.start_recording()
        except Exception as e:
            _log(f"Microphone error: {e}")
            return  # stay idle — don't enter recording state if mic failed
        self._state = "recording"
        self._record_start = time.monotonic()
        self._overlay.set_state("recording")

    @objc.python_method
    def _on_hotkey_release(self) -> None:
        if self._state != "recording":
            return
        import audio
        self._state = "processing"
        self._overlay.set_state("processing")
        duration = time.monotonic() - self._record_start
        # Mark stop (non-blocking) so the CGEventTap callback returns fast;
        # flush_and_get() in the thread keeps recording 350ms more to capture word endings.
        audio.signal_stop()
        threading.Thread(
            target=self._stop_and_transcribe,
            args=(duration,),
            daemon=True,
        ).start()

    @objc.python_method
    def _stop_and_transcribe(self, duration: float) -> None:
        import audio
        audio_bytes = audio.flush_and_get()
        self._transcribe_and_inject(audio_bytes, duration)

    # ------------------------------------------------------------------ translate hotkey

    @objc.python_method
    def _on_translate_press(self) -> None:
        if self._state != "idle" or not self._libs_ready:
            return
        import audio
        try:
            audio.start_recording()
        except Exception as e:
            _log(f"Microphone error: {e}")
            return
        self._state = "translate_recording"
        self._record_start = time.monotonic()
        self._overlay.set_state("recording")   # same waveform bars as transcribe

    @objc.python_method
    def _on_translate_release(self) -> None:
        if self._state != "translate_recording":
            return
        import audio
        self._state = "processing"
        self._overlay.set_state("translating")  # purple "Translating…"
        duration = time.monotonic() - self._record_start
        audio.signal_stop()
        threading.Thread(
            target=self._stop_and_translate,
            args=(duration,),
            daemon=True,
        ).start()

    @objc.python_method
    def _stop_and_translate(self, duration: float) -> None:
        import audio
        audio_bytes = audio.flush_and_get()
        self._translate_and_inject(audio_bytes, duration)

    def _translate_and_inject(self, audio_bytes: bytes, duration: float) -> None:
        import transcribe, inject, dictionary, history
        try:
            if not audio_bytes:
                return
            text, lang = transcribe.translate(audio_bytes)
            text = dictionary.apply(text)
            if text.strip():
                history.write(text, duration, "translate", lang)
                inject.inject_text(text)
        except Exception as e:
            import traceback as _tb
            _log(f"Translation error: {e}\n{_tb.format_exc().strip()}")
            self._set_state_main("error")
            time.sleep(1.5)
        finally:
            self._set_state_main("idle_done")

    # ------------------------------------------------------------------ transcribe

    def _transcribe_and_inject(self, audio_bytes: bytes, duration: float) -> None:
        import transcribe, inject, dictionary, history
        try:
            # Empty audio: mic failed or user held hotkey for <1 frame — silently return
            if not audio_bytes:
                return

            text, lang = transcribe.transcribe(audio_bytes)
            text = dictionary.apply(text)

            if text.strip():
                history.write(text, duration, getattr(cfg, "api_provider", "local"), lang)
                inject.inject_text(text)
            # Empty result = silence — silently return to idle, no error

        except Exception as e:
            import traceback as _tb
            _log(f"Transcription error: {e}\n{_tb.format_exc().strip()}")
            self._set_state_main("error")
            time.sleep(1.5)
        finally:
            self._set_state_main("idle_done")

    def _set_state_main(self, state: str) -> None:
        def _update():
            if state == "error":
                self._overlay.set_state("error")
            elif state == "idle_done":
                self._state = "idle"
                self._overlay.set_state("idle")

        from Foundation import NSThread
        if NSThread.isMainThread():
            _update()
        else:
            self.performSelectorOnMainThread_withObject_waitUntilDone_("_runUpdate:", _update, False)

    def _runUpdate_(self, fn):
        fn()
