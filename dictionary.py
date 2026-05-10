import re
from config import cfg

_replacements: list[tuple[re.Pattern, str]] = []


def _build_patterns() -> list[tuple[re.Pattern, str]]:
    raw = getattr(cfg, "dictionary", {}) or {}
    # Sort by length descending to avoid partial-match clobbering
    pairs = sorted(raw.items(), key=lambda x: len(x[0]), reverse=True)
    return [
        (re.compile(r"\b" + re.escape(src) + r"\b", re.IGNORECASE), dst)
        for src, dst in pairs
    ]


_replacements = _build_patterns()


def apply(text: str) -> str:
    for pattern, replacement in _replacements:
        text = pattern.sub(replacement, text)
    return text
