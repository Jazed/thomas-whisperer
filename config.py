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

            added = {k: v for k, v in defaults.items() if k not in user}
            if added:
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
    "claude": ["openai_api_key", "anthropic_api_key"],
    "local": [],
}


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
