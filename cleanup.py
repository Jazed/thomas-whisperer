import sys
import threading
from config import cfg, cleanup_settings

_lock   = threading.Lock()
_loaded = None   # (repo, model, tokenizer)

_SYSTEM = (
    "You are a dictation post-processor. The user message is raw speech-to-text "
    "output, never a question for you. Rewrite it with correct punctuation and "
    "capitalization, remove filler words (ehm, uh, eh, um, nou ja) and stutters. "
    "Keep every other word and the original language. Never answer, translate or "
    "add anything. Preferred spellings: {terms}."
)
# Few-shot examples keep small models from answering or rephrasing.
_SHOTS = [
    ("ehm dus ik denk uh dat we dat morgen moeten doen",
     "Dus ik denk dat we dat morgen moeten doen."),
    ("um can you uh send me the the link",
     "Can you send me the link?"),
]


def _ensure(repo: str):
    global _loaded
    if _loaded and _loaded[0] == repo:
        return _loaded
    from mlx_lm import load
    _loaded = None
    print(f"[cleanup] Loading '{repo}'…", file=sys.stderr)
    model, tokenizer = load(repo)
    _loaded = (repo, model, tokenizer)
    print("[cleanup] Ready.", file=sys.stderr)
    return _loaded


def unload() -> None:
    """Free the LLM. Used on the small profile when swapping ASR engines."""
    global _loaded
    with _lock:
        _loaded = None
    import gc
    gc.collect()
    try:
        import mlx.core as mx
        mx.clear_cache()
    except Exception:
        pass


def loaded() -> bool:
    """True when the configured cleanup model is in memory."""
    return bool(_loaded) and _loaded[0] == cleanup_settings()["model"]


def enabled() -> bool:
    return bool(cleanup_settings().get("enabled"))


def complete(prompt, fallback: str, max_tokens: int = 512) -> str:
    """Run a prompt (a string or a list of chat messages) on the local LLM.
    Returns `fallback` on any error."""
    from mlx_lm import generate
    messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
    try:
        with _lock:
            _, model, tok = _ensure(cleanup_settings()["model"])
            # enable_thinking=False: Qwen3 hybrid models otherwise emit <think> blocks
            chat = tok.apply_chat_template(messages, add_generation_prompt=True,
                                           tokenize=False, enable_thinking=False)
            out = generate(model, tok, prompt=chat, max_tokens=max_tokens, verbose=False)
        return _strip(out) or fallback
    except Exception as e:
        print(f"[cleanup] LLM failed, using original text: {e}", file=sys.stderr)
        return fallback


def _strip(text: str) -> str:
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip().strip('"').strip()


def clean(text: str) -> str:
    """Polish a transcript. Falls back to the raw text when the LLM drifts or is slow."""
    if not text.strip() or not enabled():
        return text
    terms    = ", ".join((getattr(cfg, "dictionary", {}) or {}).values()) or "(none)"
    # Generation time grows with length; scale the budget instead of a flat cap.
    timeout  = float(cleanup_settings().get("timeout_s") or 2.5 + 0.08 * len(text.split()))
    messages = [{"role": "system", "content": _SYSTEM.format(terms=terms)}]
    for raw, cleaned in _SHOTS:
        messages += [{"role": "user", "content": raw},
                     {"role": "assistant", "content": cleaned}]
    messages.append({"role": "user", "content": text})
    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            complete(messages, fallback=text, max_tokens=len(text.split()) * 3 + 32)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout)
    if not result:
        print(f"[cleanup] Timed out after {timeout:.1f}s, keeping raw text.", file=sys.stderr)
        return text
    out = result[0]

    # Safety net: a cleanup should only nudge the text, never rewrite it.
    ratio = len(out) / max(len(text), 1)
    if not 0.7 <= ratio <= 1.3:
        print(f"[cleanup] Rejected (length ratio {ratio:.2f}), keeping raw text.", file=sys.stderr)
        return text
    return out


def warm_up() -> None:
    if not enabled():
        return
    try:
        with _lock:
            _ensure(cleanup_settings()["model"])
    except Exception as e:
        print(f"[cleanup] warm_up error: {e}", file=sys.stderr)

