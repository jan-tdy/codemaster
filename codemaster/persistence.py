"""Config / installed-registry / catalog-cache JSON persistence."""

import json
import os
import tempfile
from pathlib import Path

from .constants import (
    CATALOG_CACHE, CONFIG_DIR, CONFIG_FILE, DEFAULT_BRANCH, DEFAULT_USERNAME,
    ICON_CACHE_DIR, INSTALLED_FILE,
)


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def write_json(path, data, mode=None):
    # Write to a sibling temp file and atomically replace, so a crash mid-write
    # can never leave a half-written (corrupt) config behind. The temp name
    # is unique per call (mkstemp), not a fixed "<name>.tmp" — two processes
    # (or two saves racing within one) saving the same path concurrently
    # would otherwise share one temp file and could corrupt each other's
    # write before either gets to the atomic replace.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        if mode is not None:
            # Fix the mode on the fd before any content is written,
            # overriding umask, rather than writing first and chmod-ing
            # afterwards, which left it at default permissions for the
            # whole write. A failure here is not swallowed — the caller
            # decides whether an unsecured write is acceptable.
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def load_config():
    # Tighten permissions on every load too, not just after a save, so an
    # install that predates this permission fix gets locked down as soon as
    # Code Master next runs rather than only after its first settings save.
    try:
        if CONFIG_DIR.exists():
            CONFIG_DIR.chmod(0o700)
        if CONFIG_FILE.exists():
            CONFIG_FILE.chmod(0o600)
    except OSError:
        pass
    cfg = read_json(CONFIG_FILE, {})
    cfg.setdefault("username", DEFAULT_USERNAME)
    cfg.setdefault("branch", DEFAULT_BRANCH)
    cfg.setdefault("token", "")
    cfg.setdefault("manual_paths", [])
    cfg.setdefault("confirmed_commands", {})
    cfg.setdefault("confirmed_third_party", [])
    cfg.setdefault("flatpak_remote_added", False)
    return cfg


def save_config(cfg):
    # The token (when set) grants repo-scoped GitHub access, so keep the
    # config file and its directory readable only by the owner. Unlike
    # load_config()'s best-effort tightening of an existing install, a
    # failure here is not swallowed: it's the moment the token is actually
    # written, so callers must know if it couldn't be secured.
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    write_json(CONFIG_FILE, cfg, mode=0o600)


def load_installed():
    return read_json(INSTALLED_FILE, {})


def save_installed(data):
    write_json(INSTALLED_FILE, data)


def migrate_installed_keys(installed, default_publisher):
    """Pre-v2 installed records were keyed "repo/id" — every scanned app
    was necessarily the configured GitHub account's own. v2 disambiguates
    by publisher too, since the community catalog can list apps from any
    GitHub account, so keys became "publisher/repo/id". Upgrade old keys
    in place so existing installs aren't silently orphaned. Returns True
    if anything was migrated (caller should persist the result)."""
    changed = False
    for key in list(installed.keys()):
        if key.count("/") == 1:
            rec = installed.pop(key)
            rec.setdefault("publisher", default_publisher)
            installed[f"{rec['publisher']}/{key}"] = rec
            changed = True
    return changed


def _cache_icon_name(key):
    return key.replace("/", "__").replace(" ", "_").replace(":", "_")


def save_catalog_cache(apps):
    """Persist the scanned catalog so it can be shown instantly next launch.
    Icon bytes don't fit in JSON, so each icon is written to its own file and
    referenced by name."""
    try:
        ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        serial = []
        for app in apps:
            record = {k: v for k, v in app.items() if k != "icon_data"}
            icon = app.get("icon_data")
            if icon:
                fname = _cache_icon_name(app["key"]) + ".img"
                (ICON_CACHE_DIR / fname).write_bytes(icon)
                record["icon_file"] = fname
            else:
                record["icon_file"] = None
            serial.append(record)
        write_json(CATALOG_CACHE, {"apps": serial})
    except Exception:
        pass  # caching is best-effort; never break a successful scan over it


def load_catalog_cache():
    data = read_json(CATALOG_CACHE, None)
    if not data:
        return []
    apps = []
    for record in data.get("apps", []):
        app = dict(record)
        icon_file = app.pop("icon_file", None)
        app["icon_data"] = None
        if icon_file:
            path = ICON_CACHE_DIR / icon_file
            if path.exists():
                try:
                    app["icon_data"] = path.read_bytes()
                except Exception:
                    pass
        apps.append(app)
    return apps
