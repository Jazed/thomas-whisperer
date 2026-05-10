"""
Overlay UI — drawRect_ based.
"""
import math, time, sys
import objc

import audio as _audio

from AppKit import (
    NSWindow, NSGradient, NSView, NSColor, NSBezierPath,
    NSScreen, NSMakeRect, NSFont, NSString,
    NSForegroundColorAttributeName, NSFontAttributeName,
    NSMutableDictionary,
)
from Foundation import NSObject, NSTimer, NSRunLoop, NSDefaultRunLoopMode

try:
    from AppKit import NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorStationary
except ImportError:
    NSWindowCollectionBehaviorCanJoinAllSpaces = 1
    NSWindowCollectionBehaviorStationary = 16

_LEVEL    = 25       # NSStatusWindowLevel
_SILENCE  = 0.035
_H_H      = 38       # pill height (all states)
_NUM_BARS = 10
_BAR_FREQS = [0.8, 1.3, 1.8, 1.1, 1.6, 2.2, 0.9, 1.5, 1.9, 1.2]
_BAR_W      = 5.0
_BAR_GAP    = 3.0
_WAVEFORM_W = _NUM_BARS * _BAR_W + (_NUM_BARS - 1) * _BAR_GAP  # 77px
_LOGO_GAP   = 4
_RIGHT_PAD  = 10
_IDLE_TEXT  = "ThomasWhisperer"
_IDLE_FONT  = 11.0
_MINI_W     = _H_H + 4   # minimised: just the logo circle


def _col(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def _measure_text(text, size, weight=0.35):
    attrs = NSMutableDictionary.dictionary()
    attrs[NSFontAttributeName] = NSFont.systemFontOfSize_weight_(size, weight)
    return NSString.stringWithString_(text).sizeWithAttributes_(attrs).width


class _Bars:
    def __init__(self):
        self.h = [0.06] * _NUM_BARS

    def update(self, level):
        now     = time.monotonic()
        boosted = min(level * 3.5, 1.0)  # amplify so normal speech fills the bars
        for i in range(_NUM_BARS):
            if level > _SILENCE:           # gate on raw level to avoid false triggers
                ph     = 0.5 + 0.5 * math.sin(now * _BAR_FREQS[i] * math.pi * 2)
                target = max(0.18, boosted * (0.45 + ph * 0.55))
            else:
                target = 0.06
            alpha = 0.55 if target > self.h[i] else 0.10
            self.h[i] += (target - self.h[i]) * alpha


class _PillView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_PillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._state      = "idle"
        self._minimized  = False
        self._t0         = time.monotonic()
        self._bars       = _Bars()
        self._click_cb   = None   # set by OverlayPanel
        self._down_pos   = None
        self._down_time  = 0.0
        return self

    def isOpaque(self):
        return False

    def mouseDown_(self, event):
        self._down_pos  = event.locationInWindow()
        self._down_time = time.monotonic()

    def mouseUp_(self, event):
        if self._down_pos is None:
            return
        up  = event.locationInWindow()
        dx  = up.x - self._down_pos.x
        dy  = up.y - self._down_pos.y
        if (dx*dx + dy*dy) < 64 and (time.monotonic() - self._down_time) < 0.35:
            if self._click_cb:
                self._click_cb()
        self._down_pos = None

    @objc.python_method
    def set_state(self, state):
        self._state = state
        self._t0    = time.monotonic()

    def tick_(self, _timer):
        level = _audio.get_audio_level()
        self._bars.update(level)
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        try:
            self._draw()
        except Exception as e:
            print(f"[overlay] draw error: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()

    @objc.python_method
    def _draw(self):
        state = self._state
        t     = time.monotonic() - self._t0
        b     = self.bounds()
        w     = b.size.width
        h     = b.size.height

        # Stroke width and inset so border isn't clipped by view edge
        active    = state in ("recording", "processing")
        stroke_w  = 2.0 if active else 1.5
        inset     = stroke_w / 2
        rx        = (min(w, h) - 2 * inset) / 2
        pill_rect = NSMakeRect(inset, inset, w - 2 * inset, h - 2 * inset)

        # 1. Clear
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(b)

        # 2. Gradient background
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(pill_rect, rx, rx)
        try:
            grad = NSGradient.alloc().initWithStartingColor_endingColor_(
                _col(0.07, 0.07, 0.16, 1.0),   # dark blue-black (top)
                _col(0.13, 0.07, 0.24, 1.0),   # dark purple (bottom)
            )
            grad.drawInBezierPath_angle_(pill, 270)
        except Exception:
            _col(0.08, 0.08, 0.14, 1.0).set()
            pill.fill()

        # 3. Border
        if active:
            _col(1, 1, 1, 0.95).set()
        else:
            _col(1, 1, 1, 0.65).set()
        pill.setLineWidth_(stroke_w)
        pill.stroke()

        # 4. Content
        mid_y   = h / 2
        logo_r  = h * 0.30
        logo_cx = h / 2
        right_x = h + _LOGO_GAP
        right_w = w - right_x - _RIGHT_PAD / 2

        if self._minimized and state == "idle":
            self._draw_logo(w / 2, mid_y, logo_r)
            return

        if state == "idle":
            self._draw_logo(logo_cx, mid_y, logo_r)
            self._draw_text(_IDLE_TEXT, _IDLE_FONT, 1.0, right_x, (h - 13) / 2, w)

        elif state == "recording":
            self._draw_logo(logo_cx, mid_y, logo_r)
            self._draw_waveform(right_x, right_w, h)

        elif state == "processing":
            pulse = 0.55 + 0.45 * abs(math.sin(t * math.pi * 0.9))
            self._draw_logo(logo_cx, mid_y, logo_r)
            self._draw_text("Transcribing…", _IDLE_FONT, pulse, right_x, (h - 13) / 2, w,
                            color=_col(0.55, 0.75, 1.0, pulse))

        elif state == "translating":
            pulse = 0.55 + 0.45 * abs(math.sin(t * math.pi * 0.9))
            self._draw_logo(logo_cx, mid_y, logo_r)
            self._draw_text("Translating…", _IDLE_FONT, pulse, right_x, (h - 13) / 2, w,
                            color=_col(0.75, 0.55, 1.0, pulse))  # purple — distinct from blue

        elif state == "success":
            self._draw_text("✓ Done", 13, 1.0, 0, (h - 15) / 2, w,
                            center=True, color=_col(0.2, 0.85, 0.4, 0.95))

        elif state == "error":
            self._draw_text("Error", 13, 1.0, 0, (h - 15) / 2, w,
                            center=True, color=_col(1.0, 0.25, 0.2, 0.95))

    @objc.python_method
    def _draw_logo(self, cx, cy, r):
        # Deep purple-blue circle
        _col(0.18, 0.10, 0.42, 1.0).set()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - r, cy - r, r * 2, r * 2)
        ).fill()
        # Blue highlight overlay (simulates gradient)
        _col(0.28, 0.38, 0.82, 0.45).set()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - r * 0.62, cy - r * 0.62, r * 1.24, r * 1.24)
        ).fill()
        # Waveform bars inside (5 bars, symmetric)
        bar_heights = [0.40, 0.68, 1.00, 0.68, 0.40]
        n     = len(bar_heights)
        bw    = r * 0.15
        gap   = r * 0.12
        total = n * bw + (n - 1) * gap
        x0    = cx - total / 2
        max_h = r * 0.80
        for i, frac in enumerate(bar_heights):
            bh = frac * max_h
            bx = x0 + i * (bw + gap)
            by = cy - bh / 2
            _col(1.0, 1.0, 1.0, 0.92).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(bx, by, bw, bh), bw / 2, bw / 2
            ).fill()

    @objc.python_method
    def _draw_waveform(self, x0, area_w, pill_h):
        # Use fixed bar_w and gap; bars are evenly spaced across area_w
        gap   = _BAR_GAP
        bar_w = _BAR_W
        max_h = pill_h - 8
        min_h = 10.0
        for i, height in enumerate(self._bars.h):
            bh    = min_h + height * (max_h - min_h)
            bx    = x0 + i * (bar_w + gap)
            by    = (pill_h - bh) / 2
            alpha = 0.38 + height * 0.62
            _col(1, 1, 1, alpha).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(bx, by, bar_w, bh), bar_w / 2, bar_w / 2
            ).fill()

    @objc.python_method
    def _draw_spinner(self, cx, cy, r, t):
        angle = (t * 300) % 360
        arc   = NSBezierPath.bezierPath()
        arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
            (cx, cy), r, angle, angle + 255
        )
        arc.setLineWidth_(2.5)
        arc.setLineCapStyle_(1)
        _col(0.35, 0.65, 1.0, 0.95).set()
        arc.stroke()

    @objc.python_method
    def _draw_text(self, text, size, alpha, x, y, w, center=False, color=None):
        attrs = NSMutableDictionary.dictionary()
        attrs[NSForegroundColorAttributeName] = color or _col(1, 1, 1, alpha)
        attrs[NSFontAttributeName] = NSFont.systemFontOfSize_weight_(size, 0.35)
        ns = NSString.stringWithString_(text)
        if center:
            x = (w - ns.sizeWithAttributes_(attrs).width) / 2
        ns.drawAtPoint_withAttributes_((x, y), attrs)


class OverlayPanel:
    def __init__(self):
        scr          = NSScreen.mainScreen().frame()
        vis          = NSScreen.mainScreen().visibleFrame()
        self._sw     = scr.size.width
        self._sh     = scr.size.height
        self._menu_h = scr.size.height - vis.size.height - vis.origin.y

        # Measure text once so idle pill is pixel-exact
        text_w          = _measure_text(_IDLE_TEXT, _IDLE_FONT)
        self._idle_w    = int(math.ceil(_H_H + _LOGO_GAP + text_w + _RIGHT_PAD))
        self._record_w  = int(math.ceil(_H_H + _LOGO_GAP + _WAVEFORM_W + _RIGHT_PAD))
        spinner_diam    = _H_H * 0.28 * 2
        self._process_w = int(math.ceil(_H_H + _LOGO_GAP + spinner_diam + _RIGHT_PAD))

        self._minimized = False   # must be set before _frame_for is called
        f = self._frame_for("idle")
        self._panel = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            f, 0, 2, False,
        )
        self._panel.setLevel_(_LEVEL)
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setHasShadow_(False)
        self._panel.setIgnoresMouseEvents_(False)
        self._panel.setMovableByWindowBackground_(True)
        self._panel.setAlphaValue_(1.0)
        try:
            self._panel.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
            )
        except Exception:
            pass

        self._view = _PillView.alloc().initWithFrame_(NSMakeRect(0, 0, f.size.width, f.size.height))
        self._view._click_cb = self.toggle_minimized
        self._panel.setContentView_(self._view)

        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.033, self._view, "tick:", None, True
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSDefaultRunLoopMode)

        self._panel.orderFrontRegardless()
        print(f"[overlay] visible={self._panel.isVisible()} level={self._panel.level()} "
              f"pos=({f.origin.x},{f.origin.y}) idle_w={self._idle_w}", file=sys.stderr)

    def _frame_for(self, state):
        if state == "recording":
            nw = self._record_w
        elif self._minimized and state == "idle":
            nw = _MINI_W
        else:
            nw = self._idle_w

        # Use current window position if already placed; default to top-center on first launch
        try:
            cur = self._panel.frame()
            cx  = cur.origin.x + cur.size.width / 2   # keep center x
            ny  = cur.origin.y                          # keep y (user may have dragged)
        except Exception:
            top_y = self._sh - self._menu_h - _H_H - 12
            cx    = self._sw / 2
            ny    = top_y

        return NSMakeRect(cx - nw / 2, ny, nw, _H_H)

    def toggle_minimized(self):
        _run_on_main(self._do_toggle)

    def _do_toggle(self):
        self._minimized = not self._minimized
        self._view._minimized = self._minimized
        if self._view._state == "idle":
            self._update("idle")

    def hide(self):
        _run_on_main(lambda: self._panel.orderOut_(None))

    def show(self):
        _run_on_main(lambda: self._panel.orderFrontRegardless())

    def set_state(self, state: str):
        _run_on_main(lambda: self._update(state))

    def _update(self, state: str):
        nf = self._frame_for(state)
        nw = nf.size.width
        self._panel.setFrame_display_animate_(nf, True, True)
        self._view.setFrame_(NSMakeRect(0, 0, nw, _H_H))
        self._view.set_state(state)
        self._panel.orderFrontRegardless()
        self._view.setNeedsDisplay_(True)


def _run_on_main(fn):
    from Foundation import NSThread
    if NSThread.isMainThread():
        fn()
    else:
        _Dispatcher.alloc().init().dispatch(fn)


class _Dispatcher(NSObject):
    @objc.python_method
    def dispatch(self, fn):
        self.performSelectorOnMainThread_withObject_waitUntilDone_("execute:", fn, False)

    def execute_(self, fn):
        fn()
