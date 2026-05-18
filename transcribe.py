import io
import base64
import sys
from config import cfg

_models: dict = {}   # size_str → WhisperModel


def _model_path(size: str) -> str:
    """Return bundled model path when frozen, otherwise fall back to HF download."""
    import os
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "models", size)
        if os.path.isdir(bundled):
            return bundled
    return size  # HuggingFace will download it


def _get_model(size: str):
    if size not in _models:
        from faster_whisper import WhisperModel
        path = _model_path(size)
        print(f"[transcribe] Loading Whisper '{size}'…", file=sys.stderr)
        _models[size] = WhisperModel(path, device="auto", compute_type="int8")
        print(f"[transcribe] Whisper '{size}' ready.", file=sys.stderr)
    return _models[size]


def _model_for(lang: str):
    """Return the right model for a detected language."""
    lang_models = getattr(cfg, "language_models", {}) or {}
    size = lang_models.get(lang) or getattr(cfg, "local_whisper_model", "base")
    return _get_model(size)


def _detect_model():
    """Model used for language detection (always the base/smallest available)."""
    lang_models = getattr(cfg, "language_models", {}) or {}
    sizes = list(lang_models.values()) + [getattr(cfg, "local_whisper_model", "base")]
    # Pick the smallest — sort by known order
    order = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
    best  = min(sizes, key=lambda s: order.index(s) if s in order else 99)
    return _get_model(best)


def transcribe(audio_bytes: bytes) -> tuple[str, str]:
    """Returns (text, language_code)."""
    provider = getattr(cfg, "api_provider", "local")

    if provider == "local":
        return _transcribe_local(audio_bytes)
    elif provider == "mlx":
        return _transcribe_mlx(audio_bytes)
    elif provider == "gemini":
        return _transcribe_gemini(audio_bytes), ""
    elif provider == "openai":
        return _transcribe_openai(audio_bytes), ""
    elif provider == "claude":
        text, lang = _transcribe_local(audio_bytes)
        return _polish_with_claude(text), lang
    else:
        print(f"[transcribe] Unknown provider '{provider}', falling back to local", file=sys.stderr)
        return _transcribe_local(audio_bytes)


def _transcribe_local(audio_bytes: bytes) -> tuple[str, str]:
    """Returns (text, language_code)."""
    import io as _io
    cfg_lang    = getattr(cfg, "language", None)
    lang_models = getattr(cfg, "language_models", {}) or {}

    # Single explicit language, no per-language models → simple path
    if isinstance(cfg_lang, str) and not lang_models:
        buf = _io.BytesIO(audio_bytes)
        segments, _ = _model_for(cfg_lang).transcribe(buf, beam_size=5, language=cfg_lang)
        return " ".join(s.text for s in segments).strip(), cfg_lang

    allowed = cfg_lang if isinstance(cfg_lang, list) and cfg_lang else None

    # Step 1 — detect language using the smallest/fastest model
    det_model = _detect_model()
    det_buf   = _io.BytesIO(audio_bytes)
    _, info   = det_model.transcribe(det_buf, beam_size=1, language=None,
                                     without_timestamps=True)
    detected  = info.language

    # Step 2 — resolve to an allowed language
    _CLOSER_TO_NL = {"de", "af", "fy", "lb"}
    if allowed and detected not in allowed:
        if "nl" in allowed and detected in _CLOSER_TO_NL:
            detected = "nl"
        else:
            detected = allowed[0]
        print(f"[transcribe] forced → '{detected}'", file=sys.stderr)
    else:
        print(f"[transcribe] detected '{detected}'", file=sys.stderr)

    # Step 3 — transcribe with the model best suited for this language
    model = _model_for(detected)
    if model is det_model:
        buf2 = _io.BytesIO(audio_bytes)
        segments, _ = model.transcribe(buf2, beam_size=5, language=detected)
    else:
        buf = _io.BytesIO(audio_bytes)
        segments, _ = model.transcribe(buf, beam_size=5, language=detected)

    return " ".join(s.text for s in segments).strip(), detected


def _transcribe_mlx(audio_bytes: bytes) -> tuple[str, str]:
    """Transcribe using MLX Whisper — optimised for Apple Silicon Neural Engine."""
    import os, tempfile
    import mlx_whisper

    cfg_lang = getattr(cfg, "language", None)
    model    = getattr(cfg, "mlx_whisper_model", "mlx-community/whisper-large-v3-turbo")
    allowed  = cfg_lang if isinstance(cfg_lang, list) and cfg_lang else None
    lang     = cfg_lang if isinstance(cfg_lang, str) else None

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp = f.name

    try:
        result   = mlx_whisper.transcribe(tmp, path_or_hf_repo=model, language=lang)
        detected = result.get("language", lang or "en")
        text     = result.get("text", "").strip()

        _CLOSER_TO_NL = {"de", "af", "fy", "lb"}
        if allowed and detected not in allowed:
            if "nl" in allowed and detected in _CLOSER_TO_NL:
                detected = "nl"
            else:
                detected = allowed[0]
            print(f"[transcribe] mlx forced → '{detected}'", file=sys.stderr)
            result   = mlx_whisper.transcribe(tmp, path_or_hf_repo=model, language=detected)
            text     = result.get("text", "").strip()
        else:
            print(f"[transcribe] mlx detected '{detected}'", file=sys.stderr)
    finally:
        os.unlink(tmp)

    return text, detected


def _transcribe_gemini(audio_bytes: bytes) -> str:
    import google.generativeai as genai
    genai.configure(api_key=cfg.gemini_api_key)
    model = genai.GenerativeModel(getattr(cfg, "gemini_model", "gemini-2.0-flash"))
    audio_b64 = base64.b64encode(audio_bytes).decode()
    response = model.generate_content([
        {"mime_type": "audio/wav", "data": audio_b64},
        "Transcribe this audio verbatim. Return only the spoken text, no commentary or labels.",
    ])
    return response.text.strip()


def _transcribe_openai(audio_bytes: bytes) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=cfg.openai_api_key)
    transcript = client.audio.transcriptions.create(
        model="whisper-1",
        file=("audio.wav", audio_bytes, "audio/wav"),
        language=getattr(cfg, "language", "en"),
    )
    return transcript.text.strip()


def _polish_with_claude(text: str) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=cfg.anthropic_api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                "Fix any transcription errors in the following voice dictation text. "
                "Preserve developer terminology, code names, and proper nouns. "
                "Fix obvious grammar and punctuation issues. "
                "Return only the corrected text, nothing else.\n\n"
                f"{text}"
            ),
        }],
    )
    return message.content[0].text.strip()


def translate(audio_bytes: bytes) -> tuple[str, str]:
    """Transcribe audio and translate to the configured target language.
    Returns (translated_text, source_language_code).
    """
    cfg_trans = getattr(cfg, "translation", {}) or {}
    provider  = cfg_trans.get("provider", "whisper")
    target    = cfg_trans.get("target_language", "en")

    if provider == "whisper":
        return _translate_whisper(audio_bytes)
    else:
        text, source_lang = _transcribe_local(audio_bytes)
        return _translate_with_llm(text, source_lang, target, provider), source_lang


def _translate_whisper(audio_bytes: bytes) -> tuple[str, str]:
    """Use Whisper's built-in translate task — always outputs English."""
    import io as _io
    cfg_trans  = getattr(cfg, "translation", {}) or {}
    model_size = cfg_trans.get("model", getattr(cfg, "local_whisper_model", "base"))
    model      = _get_model(model_size)
    buf        = _io.BytesIO(audio_bytes)
    segments, info = model.transcribe(buf, task="translate", beam_size=5)
    return " ".join(s.text for s in segments).strip(), info.language


def _translate_with_llm(text: str, source_lang: str,
                        target_lang: str, provider: str) -> str:
    """Translate text via an LLM API — supports any language pair."""
    _NAMES = {
        "en": "English", "nl": "Dutch", "fr": "French", "de": "German",
        "es": "Spanish", "it": "Italian", "pt": "Portuguese",
        "pl": "Polish",  "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    }
    src    = _NAMES.get(source_lang, source_lang)
    tgt    = _NAMES.get(target_lang, target_lang)
    prompt = (f"Translate the following text from {src} to {tgt}. "
              f"Return only the translated text, nothing else:\n\n{text}")

    if provider == "claude":
        from anthropic import Anthropic
        r = Anthropic(api_key=cfg.anthropic_api_key).messages.create(
            model="claude-sonnet-4-6", max_tokens=1024,
            messages=[{"role": "user", "content": prompt}])
        return r.content[0].text.strip()

    if provider == "openai":
        from openai import OpenAI
        r = OpenAI(api_key=cfg.openai_api_key).chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content.strip()

    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=cfg.gemini_api_key)
        r = genai.GenerativeModel(
            getattr(cfg, "gemini_model", "gemini-2.0-flash")
        ).generate_content(prompt)
        return r.text.strip()

    return text   # unknown provider — return original unchanged


def warm_up() -> None:
    provider = getattr(cfg, "api_provider", "local")
    if provider == "mlx":
        import mlx_whisper
        model = getattr(cfg, "mlx_whisper_model", "mlx-community/whisper-large-v3-turbo")
        print(f"[transcribe] Pre-loading MLX model '{model}'…", file=sys.stderr)
        mlx_whisper.transcribe(
            __import__("numpy").zeros(16000, dtype="float32"),
            path_or_hf_repo=model,
        )
        print("[transcribe] MLX model ready.", file=sys.stderr)
        return
    if provider != "local":
        return
    lang_models = getattr(cfg, "language_models", {}) or {}
    if lang_models:
        for lang, size in lang_models.items():
            print(f"[transcribe] Pre-loading model '{size}' for '{lang}'…", file=sys.stderr)
            _get_model(size)
    else:
        _get_model(getattr(cfg, "local_whisper_model", "base"))
