import json
import sys
import threading
import time
import shutil
from pathlib import Path
from types import SimpleNamespace

# Bundled default (inside .app or source tree)
_BUNDLED_CONFIG = Path(__file__).parent / "config.json"

# User config — lives outside the bundle so it survives rebuilds/reinstalls
_USER_CONFIG = Path.home() / ".thomas-voice" / "config.json"


CONFIG_VERSION = 2


def _migrate(user: dict) -> bool:
    """One-time upgrades for keys the non-destructive merge can't fix.
    Returns True if the user config was changed."""
    if user.get("config_version", 1) >= CONFIG_VERSION:
        return False
    # v2: local transcription moved from api_provider ("local"/"mlx") to "engine".
    # "claude" polish is replaced by the free local cleanup step.
    if user.get("api_provider", "local") in ("local", "mlx", "claude"):
        user["api_provider"] = "local"
        user["engine"] = "auto"
    user["config_version"] = CONFIG_VERSION
    print("[config] Migrated user config to version 2 (engine: auto).", file=sys.stderr)
    return True


def _resolve_config_path() -> Path:
    """Return the active config path.

    - First launch: copies the bundled default to ~/.thomas-voice/config.json.
    - Subsequent launches: merges any NEW top-level keys from the bundled default
      into the existing user config, so new features always have their defaults
      without ever overwriting settings the user has changed.
    """
    _USER_CONFIG.parent.mkdir(exist_ok=True)

    if not _USER_CONFIG.exists():
        if _BUNDLED_CONFIG.exists():
            shutil.copy2(_BUNDLED_CONFIG, _USER_CONFIG)
        return _USER_CONFIG

    # Merge new keys from bundled default → user config (non-destructive)
    if _BUNDLED_CONFIG.exists():
        try:
            with open(_BUNDLED_CONFIG) as f:
                defaults = json.load(f)
            with open(_USER_CONFIG) as f:
                user = json.load(f)

            migrated = _migrate(user)
            added = {k: v for k, v in defaults.items() if k not in user}
            if added or migrated:
                user.update(added)
                with open(_USER_CONFIG, "w") as f:
                    json.dump(user, f, indent=2)
                print(f"[config] Added new keys to user config: {list(added)}", file=sys.stderr)
        except Exception as e:
            print(f"[config] Config merge failed (non-fatal): {e}", file=sys.stderr)

    return _USER_CONFIG


CONFIG_PATH = _resolve_config_path()

REQUIRED_KEYS = {
    "gemini": ["gemini_api_key"],
    "openai": ["openai_api_key"],
    "local": [],
}

# Default model repos per local engine. Overridable via "engine_models" in config.
ENGINE_REPOS = {
    "parakeet":       "mlx-community/parakeet-tdt-0.6b-v3",
    "qwen3_asr":      "mlx-community/Qwen3-ASR-0.6B-8bit",
    "cohere":         "CohereLabs/cohere-transcribe-03-2026",
    "voxtral":        "mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit",
    "whisper":        "mlx-community/whisper-large-v3-turbo",
    "faster_whisper": "small",
}

# Hardware profiles — picked from RAM when "profile" is "auto".
# small: M1 8 GB → one compact ASR model + a small cleanup LLM.
# large: ≥16 GB (e.g. M5 24 GB) → bigger models, both kept resident.
PROFILES = {
    "small": {
        "engine": "parakeet",
        "engine_models": {},
        # 1.7B answers questions and translates jargon; 4B follows the prompt.
        "cleanup_model": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
    },
    "large": {
        "engine": "qwen3_asr",
        "engine_models": {"qwen3_asr": "mlx-community/Qwen3-ASR-1.7B-8bit"},
        "cleanup_model": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
    },
}


def machine_profile() -> str:
    """Resolve the active hardware profile ("small" or "large")."""
    p = getattr(cfg, "profile", "auto")
    if p in PROFILES:
        return p
    try:
        import subprocess
        mem = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))
    except Exception:
        return "small"
    return "large" if mem >= 16 * 1024 ** 3 else "small"


def active_engine() -> tuple[str, str]:
    """Return (engine_name, model_repo) after applying profile defaults.
    Explicit keys in the user config always win over the profile."""
    prof   = PROFILES[machine_profile()]
    engine = getattr(cfg, "engine", "auto")
    if engine in (None, "", "auto") or engine not in ENGINE_REPOS:
        engine = prof["engine"]
    user_models = getattr(cfg, "engine_models", {}) or {}
    repo = (user_models.get(engine)
            or prof["engine_models"].get(engine)
            or ENGINE_REPOS[engine])
    return engine, repo


def cleanup_settings() -> dict:
    """Return the cleanup config with the profile's default model filled in."""
    c = dict(getattr(cfg, "cleanup", {}) or {})
    if not c.get("model") or c.get("model") == "auto":
        c["model"] = PROFILES[machine_profile()]["cleanup_model"]
    return c


def save(updates: dict) -> None:
    """Write top-level keys to the user config. The file watcher picks it up."""
    with open(CONFIG_PATH) as f:
        data = json.load(f)
    data.update(updates)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(CONFIG_PATH)


def _validate(data: dict) -> None:
    provider = data.get("api_provider", "local")
    for key in REQUIRED_KEYS.get(provider, []):
        if not data.get(key):
            print(f"[config] ERROR: '{key}' is required for provider '{provider}'",
                  file=sys.stderr)
            sys.exit(1)


def load() -> SimpleNamespace:
    if not CONFIG_PATH.exists():
        print(f"[config] ERROR: config not found at {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        data = json.load(f)
    _validate(data)
    return SimpleNamespace(**data)


cfg = load()


def reload() -> bool:
    """Re-read config and update cfg in-place. Returns True on success."""
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        for key, value in data.items():
            setattr(cfg, key, value)
        print("[config] Reloaded.", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[config] Reload failed: {e}", file=sys.stderr)
        return False


def watch(on_reload: callable = None) -> None:
    """Watch CONFIG_PATH for changes and reload cfg automatically."""
    def _loop():
        try:
            last_mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            last_mtime = 0
        while True:
            time.sleep(2)
            try:
                mtime = CONFIG_PATH.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    if reload() and on_reload:
                        on_reload()
            except OSError:
                pass

    threading.Thread(target=_loop, daemon=True, name="config-watch").start()
