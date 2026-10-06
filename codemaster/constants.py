"""App identity, on-disk paths and static lookup tables."""

import os
from pathlib import Path

APP_NAME = "Jadiv Code Master"
APP_VERSION = "2.0.0"
DEFAULT_USERNAME = "jan-tdy"
DEFAULT_BRANCH = "main"
METADATA_FILE = "codemaster-metadata.json"

# The community catalog always lives in this project's own repository,
# independent of the "GitHub user" setting (which only controls whose own
# repos get scanned) — it is part of Code Master itself, not the user's data.
COMMUNITY_CATALOG_OWNER = "jan-tdy"
COMMUNITY_CATALOG_REPO = "codemaster"
COMMUNITY_CATALOG_BRANCH = "main"
COMMUNITY_CATALOG_DIR = "catalog"

# Ceiling for a single git network operation (clone/fetch/pull), so a dead
# or stalled connection surfaces as an error instead of hanging the
# background worker thread forever.
GIT_TIMEOUT_SECONDS = 120

CONFIG_DIR = Path.home() / ".config" / "codemaster"
DATA_DIR = Path.home() / ".local" / "share" / "codemaster"
APPS_DIR = DATA_DIR / "apps"
CONFIG_FILE = CONFIG_DIR / "config.json"
INSTALLED_FILE = CONFIG_DIR / "installed.json"

# Cache of the last scanned catalog, so apps show up instantly on next launch
# while a fresh scan runs in the background.
CACHE_DIR = DATA_DIR / "cache"
CATALOG_CACHE = CACHE_DIR / "catalog.json"
ICON_CACHE_DIR = CACHE_DIR / "icons"


def xdg_data_home():
    """Read $XDG_DATA_HOME the way install-launcher.sh's shell
    ``${XDG_DATA_HOME:-default}`` does: an unset *or* empty value falls
    back to the default, so the app agrees with the installer on where
    launchers live even on systems that export the variable empty."""
    value = os.environ.get("XDG_DATA_HOME")
    return Path(value) if value else Path.home() / ".local" / "share"


# Desktop launchers Code Master creates for installed apps.
APPLICATIONS_DIR = xdg_data_home() / "applications"
LAUNCHER_ICON_DIR = DATA_DIR / "launcher-icons"

# Maps a metadata category to freedesktop.org Categories= values.
DESKTOP_CATEGORIES = {
    "Astronomy": "Science;Education;",
    "Photography": "Graphics;Photography;",
    "Education": "Education;",
    "Developer Tools": "Development;",
    "Developer": "Development;",
}

# Directory the running Code Master lives in (used for self-update) — the
# package root's parent, since jadiv_code_master.py sits next to codemaster/.
SELF_DIR = Path(__file__).resolve().parent.parent

ICON_SIZE = 64
TILE_ICON_SIZE = 56

# Pseudo-categories system packages are grouped under in the Store sidebar.
# Kept separate from publisher-declared app categories so the two sort
# together but are visually/semantically distinguishable.
BACKEND_LABELS = {
    "git": "JapySoft apps",
    "apt": "APT packages",
    "snap": "Snap packages",
    "flatpak": "Flatpak packages",
}
