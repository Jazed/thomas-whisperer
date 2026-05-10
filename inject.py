import time
import threading
import sys

# kVK_ANSI_V = 9
_V_KEY = 9


def inject_text(text: str) -> None:
    try:
        from AppKit import NSPasteboard, NSStringPboardType
        import Quartz

        pb = NSPasteboard.generalPasteboard()
        original = pb.stringForType_(NSStringPboardType)

        pb.clearContents()
        pb.setString_forType_(text, NSStringPboardType)

        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        keydown = Quartz.CGEventCreateKeyboardEvent(src, _V_KEY, True)
        keyup = Quartz.CGEventCreateKeyboardEvent(src, _V_KEY, False)
        Quartz.CGEventSetFlags(keydown, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(keyup, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, keydown)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, keyup)

        def _restore():
            time.sleep(0.2)
            pb.clearContents()
            if original:
                pb.setString_forType_(original, NSStringPboardType)

        threading.Thread(target=_restore, daemon=True).start()

    except Exception as e:
        print(f"[inject] ERROR: {e}", file=sys.stderr)
        print(f"[inject] Make sure Accessibility permission is granted in System Settings.", file=sys.stderr)
