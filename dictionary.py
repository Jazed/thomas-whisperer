"""Words and names that must always be written a certain way.

config "dictionary": {misheard: correct} — rewritten after every transcription.
config "vocabulary": [term, ...]         — terms without a known mishearing.

Every correct spelling from both is also passed to the models that take hints
(Qwen3-ASR, Whisper), to the cleanup LLM, and to translation, which must keep
them unchanged.
"""
import re
from config import cfg

_cache: tuple = ((), [])   # (dictionary items, compiled patterns)


def _patterns() -> list[tuple[re.Pattern, str]]:
    """Compiled replacements, rebuilt whenever the config's dictionary changes."""
    global _cache
    items = tuple(sorted((getattr(cfg, "dictionary", {}) or {}).items()))
    if items != _cache[0]:
        # Longest first so "use effect hook" wins over "use effect"
        pairs = sorted(items, key=lambda x: len(x[0]), reverse=True)
        _cache = (items, [(re.compile(r"\b" + re.escape(src) + r"\b", re.IGNORECASE), dst)
                          for src, dst in pairs if src])
    return _cache[1]


def apply(text: str) -> str:
    for pattern, replacement in _patterns():
        text = pattern.sub(replacement, text)
    return text


def terms() -> list[str]:
    """Every correct spelling, without duplicates."""
    words = list((getattr(cfg, "dictionary", {}) or {}).values())
    words += list(getattr(cfg, "vocabulary", []) or [])
    return list(dict.fromkeys(w for w in words if w))


def entries() -> list[tuple[str, list[str]]]:
    """(correct spelling, [mishearings]) for every term, for the menu."""
    heard: dict[str, list[str]] = {t: [] for t in terms()}
    for src, dst in (getattr(cfg, "dictionary", {}) or {}).items():
        heard.setdefault(dst, []).append(src)
    return sorted(heard.items(), key=lambda e: e[0].lower())


def add(term: str, misheard: list[str]) -> None:
    """Remember a spelling, plus the ways a model gets it wrong (may be empty)."""
    term = term.strip()
    if not term:
        return
    mapping = dict(getattr(cfg, "dictionary", {}) or {})
    for m in misheard:
        m = m.strip()
        if m and m.lower() != term.lower():
            mapping[m] = term
    vocab = list(getattr(cfg, "vocabulary", []) or [])
    if term not in vocab and term not in mapping.values():
        vocab.append(term)
    _store(mapping, vocab)


def remove(term: str) -> None:
    """Forget a spelling and all mishearings that map to it."""
    mapping = {k: v for k, v in (getattr(cfg, "dictionary", {}) or {}).items() if v != term}
    vocab   = [w for w in (getattr(cfg, "vocabulary", []) or []) if w != term]
    _store(mapping, vocab)


def _store(mapping: dict, vocab: list) -> None:
    from config import save
    save({"dictionary": mapping, "vocabulary": vocab})
    # Take effect now; the file watcher reloads the same values shortly after.
    cfg.dictionary = mapping
    cfg.vocabulary = vocab
