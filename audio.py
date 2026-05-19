import io
import sys
import wave
import threading
import subprocess
import time as _time
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS    = 1
DTYPE       = "int16"
MAX_SECONDS = 60

_lock           = threading.Lock()
_buffer: list[np.ndarray] = []
_recording      = False
_stop_pending   = False
_level          = 0.0
_stream: sd.InputStream | None = None
_stream_device_name: str | None = None  # name of the device the stream is using
_prev_volume: int | None = None          # None = we didn't touch volume


def _callback(indata: np.ndarray, frames: int, time, status) -> None:
    global _level
    if status:
        print(f"[audio] stream status: {status}", file=sys.stderr)
    rms    = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
    _level = min(rms / 3000.0, 1.0)
    if _recording:
        _buffer.append(indata.copy())


def _configured_device():
    """Return the user-configured device (name/index), or None for system default."""
    try:
        from config import cfg
        v = getattr(cfg, "input_device", None)
        return v if v else None
    except Exception:
        return None


def _default_input_name() -> str | None:
    """Return the name of the current system default input device."""
    try:
        return sd.query_devices(kind="input")["name"]
    except Exception:
        return None


def _open_stream() -> None:
    """Open (or reopen) the input stream. Must be called with _lock held."""
    global _stream, _stream_device_name
    if _stream is not None:
        try:
            _stream.stop()
            _stream.close()
        except Exception:
            pass
        _stream = None

    device = _configured_device()
    _stream = sd.InputStream(
        device=device,
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=_callback,
        blocksize=256,
    )
    _stream.start()
    # Resolve which device we ended up on for change detection
    if device is None:
        _stream_device_name = _default_input_name()
    else:
        _stream_device_name = str(device)
    print(f"[audio] Stream opened on: {_stream_device_name}", file=sys.stderr)


def _list_devices() -> None:
    """Print available input devices to stderr for reference."""
    try:
        devices = sd.query_devices()
        default_idx = sd.default.device[0]
        print("[audio] Available input devices:", file=sys.stderr)
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                marker = " ← default" if i == default_idx else ""
                print(f"  [{i}] {d['name']}{marker}", file=sys.stderr)
    except Exception as e:
        print(f"[audio] Could not list devices: {e}", file=sys.stderr)


def _is_likely_bluetooth(name: str) -> bool:
    """Return True if the device name looks like a Bluetooth/wireless device."""
    lower = (name or "").lower()
    return any(ind in lower for ind in ("airpods", "bluetooth", "headset", "wireless", "beats"))


def _duck_output_for_recording() -> None:
    """Lower system output volume during Bluetooth recording to prevent mic bleed.
    Saves the previous volume so _restore_output_after_recording can undo it."""
    global _prev_volume
    try:
        from config import cfg
        if not getattr(cfg, "mute_output_during_bluetooth_recording", True):
            return
        duck_level = int(getattr(cfg, "bluetooth_recording_duck_volume", 10))
    except Exception:
        duck_level = 10
    if not _is_likely_bluetooth(_stream_device_name or ""):
        return
    try:
        result = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, timeout=2,
        )
        current = int(result.stdout.strip())
        if current > duck_level:
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {duck_level}"],
                capture_output=True, timeout=2,
            )
            _prev_volume = current
            print(f"[audio] Output ducked to {duck_level}% (was {current}%) for Bluetooth recording.",
                  file=sys.stderr)
    except Exception as e:
        print(f"[audio] Could not duck output: {e}", file=sys.stderr)


def _restore_output_after_recording() -> None:
    """Restore system output volume if we reduced it when recording started."""
    global _prev_volume
    if _prev_volume is not None:
        vol = _prev_volume
        _prev_volume = None
        try:
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {vol}"],
                capture_output=True, timeout=2,
            )
            print(f"[audio] Output volume restored to {vol}% after recording.", file=sys.stderr)
        except Exception as e:
            print(f"[audio] Could not restore output volume: {e}", file=sys.stderr)


def _device_monitor() -> None:
    """Watch for device changes. When idle the stream is closed; only reopen if it
    dies mid-recording. Keeping the stream closed while idle lets AirPods stay in
    high-quality AAC mode instead of switching to HFP."""
    global _stream_device_name
    _time.sleep(4)
    while True:
        _time.sleep(2)
        try:
            current = _default_input_name() if _configured_device() is None else None
            with _lock:
                if _recording and (_stream is None or not _stream.active):
                    # Stream died mid-recording — recover immediately
                    print("[audio] Stream died during recording, reopening.", file=sys.stderr)
                    _open_stream()
                elif not _recording and current:
                    # Track the default device passively so start_recording() uses the right one
                    _stream_device_name = current
        except Exception as e:
            print(f"[audio] Device monitor error: {e}", file=sys.stderr)


threading.Thread(target=_device_monitor, daemon=True, name="audio-device-monitor").start()


def request_permission(on_done: callable) -> None:
    """Ask for microphone permission via AVFoundation. Calls on_done(granted) when resolved."""
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        # 0=NotDetermined 1=Restricted 2=Denied 3=Authorized
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        if status == 3:
            on_done(True)
        elif status == 0:
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVMediaTypeAudio, on_done
            )
        else:
            on_done(False)
    except Exception:
        on_done(True)


def warm() -> None:
    """List available devices and pre-open the input stream so the first recording
    is instant — no stream setup on the CGEventTap callback thread."""
    _list_devices()
    with _lock:
        _open_stream()


def get_audio_level() -> float:
    return _level


def start_recording() -> None:
    global _recording
    with _lock:
        if _recording:
            return
        if _stream is None:
            _open_stream()  # only open if the idle-timeout already closed it
        _buffer.clear()
        _recording = True
    # osascript blocks 100-400 ms — unsafe to run on the CGEventTap callback thread
    threading.Thread(target=_duck_output_for_recording, daemon=True).start()


def signal_stop() -> bool:
    """Mark recording for stop. MUST stay non-blocking — called on the CGEventTap
    callback thread. macOS disables the tap if the callback blocks (even 200 ms
    is enough to kill both hotkeys). Heavy work goes in flush_and_get(), which
    must run in a background thread."""
    global _stop_pending, _recording
    with _lock:
        if not _recording or _stop_pending:
            return False
        _stop_pending = True
    return True


def _close_stream_after_idle(delay: float) -> None:
    """Close the input stream after `delay` seconds of non-recording so Bluetooth
    devices (AirPods) can exit HFP and return to high-quality AAC."""
    global _stream
    _time.sleep(delay)
    with _lock:
        if not _recording and _stream is not None:
            try:
                _stream.stop()
                _stream.close()
            except Exception:
                pass
            _stream = None
            print("[audio] Stream closed after idle timeout (AirPods → AAC).", file=sys.stderr)


def flush_and_get() -> bytes:
    """Wait for the audio tail, stop recording, then return buffered audio as WAV bytes.
    Call this in a background thread after signal_stop()."""
    global _recording, _stop_pending, _level
    _time.sleep(0.35)  # keep recording 350ms after key release to capture word endings
    with _lock:
        _recording = False
        _stop_pending = False
    _time.sleep(0.05)  # let the last callback cycle complete
    _level = 0.0
    _restore_output_after_recording()
    # Defer stream close — keeps the stream ready for the next recording while still
    # allowing AirPods to exit HFP after 30 s of inactivity.
    threading.Thread(target=_close_stream_after_idle, args=(30.0,), daemon=True).start()
    if not _buffer:
        return b""
    audio = np.concatenate(_buffer, axis=0)
    audio = _trim_silence(audio)
    return _to_wav_bytes(audio)


def stop_recording() -> bytes:
    """Stop recording and return audio. Blocks ~200 ms for pipeline flush.
    Prefer signal_stop() + flush_and_get() when called from a callback thread."""
    if not signal_stop():
        return b""
    return flush_and_get()


def _trim_silence(audio: np.ndarray, threshold_db: float = -40.0) -> np.ndarray:
    threshold = 10 ** (threshold_db / 20.0) * 32768
    chunk = SAMPLE_RATE // 10  # 100 ms steps
    end   = len(audio)
    while end > chunk:
        rms = np.sqrt(np.mean(audio[end - chunk:end].astype(np.float32) ** 2))
        if rms > threshold:
            break
        end -= chunk
    # Keep 300 ms after the last detected speech — gives natural word endings room
    return audio[:min(end + SAMPLE_RATE * 3 // 10, len(audio))]


def _to_wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()
