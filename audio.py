import io
import wave
import threading
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS    = 1
DTYPE       = "int16"
MAX_SECONDS = 60

_lock      = threading.Lock()
_buffer: list[np.ndarray] = []
_recording = False
_level     = 0.0
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


def stop_recording() -> bytes:
    global _recording, _level
    with _lock:
        if not _recording:
            return b""
        _recording = False

    # Wait 200 ms so any audio already in the callback pipeline gets flushed
    # into _buffer before we read it — prevents the last syllable being cut off.
    import time as _time
    _time.sleep(0.20)

    _level = 0.0

    if not _buffer:
        return b""

    audio = np.concatenate(_buffer, axis=0)
    audio = _trim_silence(audio)
    return _to_wav_bytes(audio)


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
