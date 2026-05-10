import io
import wave
import threading
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS    = 1
DTYPE       = "int16"
MAX_SECONDS = 60

_lock         = threading.Lock()
_buffer: list[np.ndarray] = []
_recording    = False
_stop_pending = False
_level        = 0.0
_stream: sd.InputStream | None = None


def _callback(indata: np.ndarray, frames: int, time, status) -> None:
    global _level
    rms    = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
    _level = min(rms / 3000.0, 1.0)
    if _recording:
        _buffer.append(indata.copy())


def request_permission(on_done: callable) -> None:
    """Ask for microphone permission via AVFoundation. Calls on_done(granted) when resolved."""
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        # 0=NotDetermined 1=Restricted 2=Denied 3=Authorized
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        if status == 3:          # already authorized
            on_done(True)
        elif status == 0:        # not determined — show dialog
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVMediaTypeAudio, on_done
            )
        else:                    # restricted or denied
            on_done(False)
    except Exception:
        on_done(True)            # AVFoundation unavailable — optimistically proceed


def warm() -> None:
    """Open the stream once at startup so recording starts with zero delay."""
    global _stream
    with _lock:
        if _stream is not None:
            return
        _stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=_callback,
            blocksize=256,   # 16 ms — low latency, low CPU
        )
        _stream.start()


def get_audio_level() -> float:
    return _level


def start_recording() -> None:
    global _recording
    warm()   # no-op if already open
    with _lock:
        if _recording:
            return
        _buffer.clear()
        _recording = True


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


def flush_and_get() -> bytes:
    """Wait for the audio tail, stop recording, then return buffered audio as WAV bytes.
    Call this in a background thread after signal_stop()."""
    import time as _time
    global _recording, _stop_pending, _level
    _time.sleep(0.35)   # keep recording 350ms after key release to capture word endings
    with _lock:
        _recording = False
        _stop_pending = False
    _time.sleep(0.05)   # let the last callback cycle complete
    _level = 0.0
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
    chunk = SAMPLE_RATE // 10   # 100 ms steps
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
