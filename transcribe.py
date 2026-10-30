import io
import gc
import sys
import wave
import threading
import numpy as np
from config import cfg, active_engine

# Qwen3-ASR wants language names, the rest of the app uses ISO codes.
_LANG_NAMES = {
    "en": "English", "nl": "Dutch", "fr": "French", "de": "German",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "pl": "Polish",  "sv": "Swedish", "da": "Danish", "fi": "Finnish",
}
_LANG_CODES = {v.lower(): k for k, v in _LANG_NAMES.items()}

# Languages Whisper often confuses with Dutch for Dutch speakers
_CLOSER_TO_NL = {"de", "af", "fy", "lb"}

_lock   = threading.RLock()   # one engine in memory, one transcription at a time
_active = None                # (engine_name, repo, model)
_detector = None              # tiny Whisper used for language ID (Cohere only)


# ---------------------------------------------------------------- helpers

def _wav_to_float(audio_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(audio_bytes)) as wf:
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _allowed_langs() -> list | None:
    lang = getattr(cfg, "language", None)
    if isinstance(lang, str):
        return [lang]
    return list(lang) if lang else None


def _resolve_lang(detected: str, allowed: list | None) -> str:
    """Map a detected language onto the allowed list."""
    if not allowed or detected in allowed:
        return detected
    if "nl" in allowed and detected in _CLOSER_TO_NL:
        return "nl"
    return allowed[0]


def _vocabulary() -> list[str]:
    """Words the model should prefer: dictionary targets + optional 'vocabulary' list."""
    words = list((getattr(cfg, "dictionary", {}) or {}).values())
    words += list(getattr(cfg, "vocabulary", []) or [])
    return list(dict.fromkeys(w for w in words if w))


def _whisper_detect(model, audio: np.ndarray, allowed: list | None) -> str:
    """Language ID with an mlx_whisper model, restricted to the allowed languages."""
    import mlx.core as mx
    from mlx_whisper.audio import log_mel_spectrogram, pad_or_trim, N_FRAMES
    mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels)
    mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
    _, probs = model.detect_language(mel)
    if allowed:
        return max(allowed, key=lambda l: probs.get(l, 0.0))
    return max(probs, key=probs.get)


def _free_memory() -> None:
    gc.collect()
    try:
        import mlx.core as mx
        mx.clear_cache()
    except Exception:
        pass


# ---------------------------------------------------------------- engines

def _load(engine: str, repo: str):
    if engine == "faster_whisper":
        from faster_whisper import WhisperModel
        return WhisperModel(_faster_whisper_path(repo), device="auto", compute_type="int8")
    if engine == "whisper":
        # Load through mlx_whisper's own cache so transcribe() reuses this instance
        # instead of holding a second copy in memory.
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder
        return ModelHolder.get_model(repo, mx.float16)
    from mlx_audio.stt.utils import load_model
    # strict: a checkpoint whose weight names don't match must fail loudly —
    # non-strict silently leaves layers random and the output is gibberish.
    return load_model(repo, strict=True)


def _faster_whisper_path(size: str) -> str:
    """Return bundled model path when frozen, otherwise fall back to HF download."""
    import os
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "models", size)
        if os.path.isdir(bundled):
            return bundled
    return size


def _run(engine: str, repo: str, model, audio: np.ndarray) -> tuple[str, str]:
    """Transcribe float32 16 kHz mono audio. Returns (text, language_code)."""
    allowed = _allowed_langs()
    vocab   = _vocabulary()

    if engine == "parakeet":
        import mlx.core as mx
        out = model.generate(mx.array(audio))
        return out.text.strip(), ""

    if engine == "qwen3_asr":
        lang = _LANG_NAMES.get(allowed[0]) if allowed and len(allowed) == 1 else None
        out  = model.generate(audio, language=lang, hotwords=vocab or None)
        detected = out.language[0] if isinstance(out.language, list) else out.language
        detected = _LANG_CODES.get((detected or "").lower(), "")
        if allowed and detected and detected not in allowed:
            forced = _resolve_lang(detected, allowed)
            print(f"[transcribe] qwen3 detected '{detected}', forcing '{forced}'", file=sys.stderr)
            out = model.generate(audio, language=_LANG_NAMES.get(forced, forced),
                                 hotwords=vocab or None)
            detected = forced
        return out.text.strip(), detected

    if engine == "cohere":
        # Cohere needs the language up front — detect it with a tiny Whisper.
        lang = allowed[0] if allowed and len(allowed) == 1 else _detect_with_tiny(audio, allowed)
        out  = model.generate(audio, language=lang)
        return out.text.strip(), lang

    if engine == "voxtral":
        out = model.generate(audio)
        return out.text.strip(), getattr(out, "language", "") or ""

    if engine == "whisper":
        import mlx_whisper
        lang = allowed[0] if allowed and len(allowed) == 1 else _whisper_detect(model, audio, allowed)
        result = mlx_whisper.transcribe(
            audio, path_or_hf_repo=repo, language=lang,
            initial_prompt=", ".join(vocab) or None,
        )
        return result.get("text", "").strip(), lang

    if engine == "faster_whisper":
        lang = allowed[0] if allowed and len(allowed) == 1 else None
        if lang is None:
            _, _, probs = model.detect_language(audio)
            probs = dict(probs)
            lang  = (max(allowed, key=lambda l: probs.get(l, 0.0)) if allowed
                     else max(probs, key=probs.get))
        segments, _ = model.transcribe(audio, beam_size=5, language=lang,
                                       initial_prompt=", ".join(vocab) or None)
        return " ".join(s.text for s in segments).strip(), lang

    raise ValueError(f"Unknown engine '{engine}'")


def _detect_with_tiny(audio: np.ndarray, allowed: list | None) -> str:
    global _detector
    if _detector is None:
        import mlx.core as mx
        from mlx_whisper.load_models import load_model
        _detector = load_model("mlx-community/whisper-tiny", dtype=mx.float16)
    return _whisper_detect(_detector, audio, allowed)


def _ensure_engine():
    """Load the configured engine, unloading the previous one first."""
    global _active, _detector
    engine, repo = active_engine()
    with _lock:
        if _active and _active[:2] == (engine, repo):
            return _active
        if _active:
            print(f"[transcribe] Unloading '{_active[0]}' ({_active[1]})…", file=sys.stderr)
            if _active[0] == "whisper":
                from mlx_whisper.transcribe import ModelHolder
                ModelHolder.model = ModelHolder.model_path = None
            _active = None
            if engine != "cohere":
                _detector = None
            _free_memory()
        print(f"[transcribe] Loading engine '{engine}' ({repo})…", file=sys.stderr)
        model   = _load(engine, repo)
        _active = (engine, repo, model)
        print(f"[transcribe] Engine '{engine}' ready.", file=sys.stderr)
        return _active


def engine_loaded() -> bool:
    """True when the configured engine is in memory (or none is needed)."""
    if getattr(cfg, "api_provider", "local") not in ("local", "mlx"):
        return True
    return bool(_active) and _active[:2] == active_engine()


# ---------------------------------------------------------------- public API

def transcribe(audio_bytes: bytes) -> tuple[str, str]:
    """Returns (text, language_code)."""
    provider = getattr(cfg, "api_provider", "local")

    if provider == "gemini":
        return _transcribe_gemini(audio_bytes), ""
    if provider == "openai":
        return _transcribe_openai(audio_bytes), ""
    if provider not in ("local", "mlx"):
        print(f"[transcribe] Unknown provider '{provider}', using local engine", file=sys.stderr)

    with _lock:
        engine, repo, model = _ensure_engine()
        return _run(engine, repo, model, _wav_to_float(audio_bytes))


def _transcribe_gemini(audio_bytes: bytes) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=cfg.gemini_api_key)
    response = client.models.generate_content(
        model=getattr(cfg, "gemini_model", "gemini-flash-latest"),
        contents=[
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
            "Transcribe this audio verbatim. Return only the spoken text, no commentary or labels.",
        ],
    )
    return response.text.strip()


def _transcribe_openai(audio_bytes: bytes) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=cfg.openai_api_key)
    allowed = _allowed_langs()
    transcript = client.audio.transcriptions.create(
        model=getattr(cfg, "openai_model", "gpt-4o-transcribe"),
        file=("audio.wav", audio_bytes, "audio/wav"),
        language=allowed[0] if allowed and len(allowed) == 1 else None,
    )
    return transcript.text.strip()


def translate(audio_bytes: bytes) -> tuple[str, str]:
    """Transcribe audio and translate it.
    Returns (translated_text, source_language_code).
    """
    cfg_trans = getattr(cfg, "translation", {}) or {}
    provider  = cfg_trans.get("provider", "local")
    text, source_lang = transcribe(audio_bytes)
    target = _translation_target(cfg_trans.get("target_language", "auto"), source_lang)
    if not text or source_lang == target:
        return text, source_lang
    return _translate_with_llm(text, source_lang, target, provider), source_lang


def _translation_target(target: str, source_lang: str) -> str:
    """'auto' flips between the configured languages (nl → en, en → nl)."""
    if target != "auto":
        return target
    others = [l for l in (_allowed_langs() or ["nl", "en"]) if l != source_lang]
    return others[0] if others else "en"


def _translate_with_llm(text: str, source_lang: str,
                        target_lang: str, provider: str) -> str:
    """Translate text via the local LLM or a cloud API — supports any language pair."""
    src    = _LANG_NAMES.get(source_lang, source_lang or "the source language")
    tgt    = _LANG_NAMES.get(target_lang, target_lang)
    prompt = (f"Translate the following text from {src} to {tgt}. "
              f"Return only the translated text, nothing else:\n\n{text}")

    if provider == "local":
        import cleanup
        return cleanup.complete(prompt, fallback=text)

    if provider == "openai":
        from openai import OpenAI
        r = OpenAI(api_key=cfg.openai_api_key).chat.completions.create(
            model=getattr(cfg, "openai_chat_model", "gpt-4.1-mini"),
            messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content.strip()

    if provider == "gemini":
        from google import genai
        r = genai.Client(api_key=cfg.gemini_api_key).models.generate_content(
            model=getattr(cfg, "gemini_model", "gemini-flash-latest"), contents=prompt)
        return r.text.strip()

    return text   # unknown provider — return original unchanged


def warm_up() -> None:
    if getattr(cfg, "api_provider", "local") not in ("local", "mlx"):
        return
    with _lock:
        engine, repo, model = _ensure_engine()
        # One silent pass compiles the kernels so the first real dictation is fast.
        try:
            _run(engine, repo, model, np.zeros(16000, dtype=np.float32))
        except Exception as e:
            print(f"[transcribe] warm-up pass failed (non-fatal): {e}", file=sys.stderr)
