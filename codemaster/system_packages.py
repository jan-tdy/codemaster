"""apt / snap / flatpak backends: search, install, remove, launch.

Each backend exposes the same small surface (``available``, ``search``,
``installed``, plus the actions ``SystemPackageWorker`` drives), and
``to_app_dict`` turns a backend's native search/installed record into the
same unified app-dict shape "git" apps use, so the UI only ever has to
branch on ``app["backend"]``.
"""

import re
import shutil
import subprocess
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

FLATHUB_REMOTE_URL = "https://flathub.org/repo/flathub.flatpakrepo"

# Search results are capped — a broad query against apt's ~60k packages (or
# snap's/flatpak's full catalogs) can return hundreds of hits, which would
# both be slow to render as tiles and useless to scroll through; a tighter
# query is the actual fix, same as any package manager's own search.
MAX_SEARCH_RESULTS = 60


def _run(cmd, timeout=120):
    """Run a command, raising RuntimeError with its stderr on failure.
    stdin is closed so nothing can block this worker thread on a prompt."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timed out after {timeout}s waiting for: {' '.join(cmd)}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip()
                           or f"{cmd[0]} exited with {proc.returncode}")
    return proc.stdout


# --------------------------------------------------------------------------- #
#  .desktop file reading — the system already has nice names and icons for
#  installed apt/snap/flatpak packages; this is how we actually use them
#  instead of showing the bare package id with a lettered placeholder.
# --------------------------------------------------------------------------- #
ICON_THEME_DIRS = [
    Path.home() / ".local/share/flatpak/exports/share/icons",
    Path("/var/lib/flatpak/exports/share/icons"),
    Path("/usr/share/icons"),
    Path("/usr/local/share/icons"),
]
ICON_SIZE_DIRS = ["scalable", "256x256", "192x192", "128x128", "96x96",
                 "64x64", "48x48", "32x32"]
PIXMAP_DIRS = [
    Path.home() / ".local/share/flatpak/exports/share/pixmaps",
    Path("/var/lib/flatpak/exports/share/pixmaps"),
    Path("/usr/share/pixmaps"),
]


def _resolve_icon_path(icon):
    """A themed icon name (or an already-absolute path) -> an actual file
    on disk, searching the usual freedesktop icon-theme and pixmap
    locations (plus Flatpak's own per-user/system export dirs). Prefers
    vector/large sizes first for quality."""
    if not icon:
        return None
    direct = Path(icon)
    if direct.is_absolute():
        return direct if direct.exists() else None
    for base in ICON_THEME_DIRS:
        for size in ICON_SIZE_DIRS:
            for ext in ("svg", "png", "xpm"):
                candidate = base / "hicolor" / size / "apps" / f"{icon}.{ext}"
                if candidate.exists():
                    return candidate
    for base in PIXMAP_DIRS:
        for ext in ("png", "svg", "xpm"):
            candidate = base / f"{icon}.{ext}"
            if candidate.exists():
                return candidate
    return None


def resolve_icon_bytes(icon):
    path = _resolve_icon_path(icon)
    if not path:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def parse_desktop_entry(desktop_path):
    """{"name", "icon"} for a .desktop file, or None if it isn't a
    displayable GUI application: wrong Type, or Terminal=/NoDisplay=/
    Hidden=true — the same signal that marks e.g. python3.12.desktop or a
    JRE's policytool launcher as "has a menu entry but isn't really an
    app" rather than something that belongs in an app store."""
    try:
        content = Path(desktop_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    in_entry = False
    name = icon = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_entry = (line == "[Desktop Entry]")
            continue
        if not in_entry or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key == "Type" and value.lower() != "application":
            return None
        if key in ("Terminal", "NoDisplay", "Hidden") and value.lower() == "true":
            return None
        if key == "Name" and name is None:
            name = value
        elif key == "Icon" and icon is None:
            icon = value
    if name is None:
        return None
    return {"name": name, "icon": icon}


def _parse_table(output):
    """Parse a whitespace-column CLI table (the format ``snap``/``flatpak``
    print) into a list of {header: value} dicts, using the header row's
    own word-start offsets as the column boundaries. Tolerant of ragged
    trailing columns; never raises — a table it can't make sense of just
    yields no rows instead of a wrong guess."""
    lines = [ln for ln in output.splitlines() if ln.strip()]
    if len(lines) < 1:
        return []
    header = lines[0]
    # Column names are separated by 2+ spaces, which is how these tools
    # keep a multi-word header like "Application ID" together while still
    # splitting it from its neighbours.
    names = re.split(r"\s{2,}", header.strip())
    starts = []
    pos = 0
    for name in names:
        idx = header.index(name, pos)
        starts.append(idx)
        pos = idx + len(name)
    rows = []
    for line in lines[1:]:
        values = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else None
            values.append(line[start:end].strip() if start < len(line) else "")
        rows.append(dict(zip(names, values)))
    return rows


# --------------------------------------------------------------------------- #
#  apt
# --------------------------------------------------------------------------- #
class AptBackend:
    name = "apt"
    label = "APT"

    @staticmethod
    def available():
        return bool(shutil.which("apt-cache") and shutil.which("apt-get"))

    DPKG_INFO_DIR = Path("/var/lib/dpkg/info")

    @staticmethod
    def _is_gui_desktop_entry(desktop_path):
        """Whether a .desktop file describes something that belongs in a
        graphical app store — not a CLI tool that merely ships a menu
        entry (``Terminal=true``, e.g. python3.12.desktop or a JRE's
        policy-tool launcher) and not an entry meant to stay out of menus
        (``NoDisplay=true``/``Hidden=true``, common on exactly those same
        interpreter/runtime packages)."""
        return parse_desktop_entry(desktop_path) is not None

    @staticmethod
    def _desktop_paths_for_package(list_file):
        """Every ``share/applications/*.desktop`` path a package's dpkg
        file list mentions, in listed order."""
        try:
            with open(list_file, "r", encoding="utf-8", errors="ignore") as fh:
                return [line.rstrip() for line in fh
                       if "/share/applications/" in line and
                       line.rstrip().endswith(".desktop")]
        except OSError:
            return []

    @staticmethod
    def _desktop_entry_for_package(pkg):
        """The first real GUI {"name", "icon"} entry ``pkg`` ships, or
        None — used to show the package's actual application name/icon
        instead of its bare dpkg name and a lettered placeholder."""
        list_file = AptBackend.DPKG_INFO_DIR / f"{pkg}.list"
        if not list_file.exists():
            # Multi-arch packages are named "<pkg>:<arch>.list".
            matches = sorted(AptBackend.DPKG_INFO_DIR.glob(f"{pkg}:*.list"))
            if not matches:
                return None
            list_file = matches[0]
        for path in AptBackend._desktop_paths_for_package(list_file):
            entry = parse_desktop_entry(path)
            if entry:
                return entry
        return None

    @staticmethod
    def _installed_app_packages():
        """Names of installed apt packages that ship a genuine GUI desktop
        entry.

        dpkg tracks every package on the system — base libraries, fonts,
        kernel modules, thousands of them even on a minimal desktop — not
        just applications, and plenty of those still drop a menu entry for
        a command-line tool (python3.12, a JRE's policytool, ...). Reading
        dpkg's own per-package file lists first (no subprocess per package)
        keeps the common case fast; the handful of path hits that remain
        are then checked against ``_is_gui_desktop_entry`` to drop the
        terminal/hidden ones."""
        if not AptBackend.DPKG_INFO_DIR.is_dir():
            return set()
        app_packages = set()
        for list_file in AptBackend.DPKG_INFO_DIR.glob("*.list"):
            # Multi-arch packages are named "<pkg>:<arch>.list".
            pkg = list_file.stem.split(":", 1)[0]
            if any(AptBackend._is_gui_desktop_entry(path) for path in
                  AptBackend._desktop_paths_for_package(list_file)):
                app_packages.add(pkg)
        return app_packages

    @staticmethod
    def installed():
        """{package: {"version", "name", "icon_data"}}, restricted to
        installed apt packages that are actual applications (see
        ``_installed_app_packages``) — not every one of the thousands of
        packages dpkg happens to track. name/icon_data come from the
        package's own .desktop entry when it has one, the same name and
        icon its application-menu launcher already shows."""
        if not shutil.which("dpkg-query"):
            return {}
        app_packages = AptBackend._installed_app_packages()
        if not app_packages:
            return {}
        try:
            out = _run(["dpkg-query", "-W", "-f=${Package}\\t${Version}\\n"])
        except RuntimeError:
            return {}
        result = {}
        for line in out.splitlines():
            if "\t" not in line:
                continue
            pkg, version = line.split("\t", 1)
            if pkg not in app_packages:
                continue
            entry = AptBackend._desktop_entry_for_package(pkg) or {}
            result[pkg] = {
                "version": version,
                "name": entry.get("name") or pkg,
                "icon_data": resolve_icon_bytes(entry.get("icon")),
            }
        return result

    @staticmethod
    def search(query):
        try:
            out = _run(["apt-cache", "search", query])
        except RuntimeError:
            return []
        results = []
        for line in out.splitlines():
            if " - " not in line:
                continue
            pkg, summary = line.split(" - ", 1)
            results.append({"id": pkg.strip(), "name": pkg.strip(),
                            "summary": summary.strip(), "version": ""})
            if len(results) >= MAX_SEARCH_RESULTS:
                break
        return results

    @staticmethod
    def upgradable():
        """Names of installed apt packages with a pending update.

        ``apt list --upgradable`` prints lines like
        "python3/noble-updates 3.12.3-1 amd64 [upgradable from: 3.12.2-1]"
        (plus a "Listing..." line and, on stderr only, apt's own "not a
        stable CLI interface" warning — never on stdout, so it never ends
        up in ``out`` here)."""
        try:
            out = _run(["apt", "list", "--upgradable"])
        except RuntimeError:
            return set()
        names = set()
        for line in out.splitlines():
            if "/" not in line:
                continue
            names.add(line.split("/", 1)[0].strip())
        return names

    @staticmethod
    def install(pkg):
        pkexec = shutil.which("pkexec")
        if not pkexec:
            raise RuntimeError("pkexec is required to install apt packages "
                               "(no polkit agent found).")
        _run([pkexec, "apt-get", "install", "-y", pkg])

    @staticmethod
    def update(pkg):
        pkexec = shutil.which("pkexec")
        if not pkexec:
            raise RuntimeError("pkexec is required to update apt packages "
                               "(no polkit agent found).")
        _run([pkexec, "apt-get", "install", "-y", "--only-upgrade", pkg])

    @staticmethod
    def remove(pkg):
        pkexec = shutil.which("pkexec")
        if not pkexec:
            raise RuntimeError("pkexec is required to remove apt packages "
                               "(no polkit agent found).")
        _run([pkexec, "apt-get", "remove", "-y", pkg])

    @staticmethod
    def launch(pkg):
        # apt has no universal "run this package" command — the binary
        # name often differs from the package name, and apt-installed GUI
        # apps already register their own application-menu entry. Best
        # effort: a binary that happens to share the package's name.
        binary = shutil.which(pkg)
        if not binary:
            raise RuntimeError(
                f"No '{pkg}' executable found on PATH. Look for it in your "
                "application menu — apt packages add their own launcher.")
        subprocess.Popen([binary], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


# --------------------------------------------------------------------------- #
#  snap
# --------------------------------------------------------------------------- #
class SnapBackend:
    name = "snap"
    label = "Snap"

    @staticmethod
    def available():
        return bool(shutil.which("snap"))

    SNAP_DESKTOP_DIR = Path("/var/lib/snapd/desktop/applications")

    @staticmethod
    def _desktop_entry_for_snap(name):
        """snapd generates a .desktop launcher per app a snap exposes,
        named "<snap>_<app>.desktop" (or just "<snap>.desktop" when the
        snap's main app shares its name) — look for one so an installed
        snap shows the same name/icon its own launcher does, instead of
        its bare snap id and a lettered placeholder."""
        if not SnapBackend.SNAP_DESKTOP_DIR.is_dir():
            return None
        candidates = sorted(SnapBackend.SNAP_DESKTOP_DIR.glob(f"{name}.desktop")) + \
            sorted(SnapBackend.SNAP_DESKTOP_DIR.glob(f"{name}_*.desktop"))
        for path in candidates:
            entry = parse_desktop_entry(path)
            if entry:
                return entry
        return None

    @staticmethod
    def installed():
        try:
            out = _run(["snap", "list"])
        except RuntimeError:
            return {}
        result = {}
        for row in _parse_table(out):
            name = row.get("Name")
            if not name:
                continue
            entry = SnapBackend._desktop_entry_for_snap(name) or {}
            result[name] = {
                "version": row.get("Version", ""),
                "name": entry.get("name") or name,
                "icon_data": resolve_icon_bytes(entry.get("icon")),
            }
        return result

    @staticmethod
    def search(query):
        try:
            out = _run(["snap", "find", query])
        except RuntimeError:
            return []
        results = []
        for row in _parse_table(out):
            if not row.get("Name"):
                continue
            results.append({
                "id": row["Name"], "name": row["Name"],
                "summary": row.get("Summary", ""),
                "version": row.get("Version", ""),
            })
            if len(results) >= MAX_SEARCH_RESULTS:
                break
        return results

    @staticmethod
    def upgradable():
        """Names of installed snaps with a pending refresh. ``snap
        refresh --list`` prints a table of refreshable snaps, or a single
        "All snaps up to date." line (no real table, parses to no rows)
        when there's nothing to do — needs no root to just check."""
        try:
            out = _run(["snap", "refresh", "--list"])
        except RuntimeError:
            return set()
        return {row["Name"] for row in _parse_table(out) if row.get("Name")}

    @staticmethod
    def install(name):
        pkexec = shutil.which("pkexec")
        if not pkexec:
            raise RuntimeError("pkexec is required to install snaps "
                               "(no polkit agent found).")
        _run([pkexec, "snap", "install", name])

    @staticmethod
    def update(name):
        pkexec = shutil.which("pkexec")
        if not pkexec:
            raise RuntimeError("pkexec is required to refresh snaps "
                               "(no polkit agent found).")
        _run([pkexec, "snap", "refresh", name])

    @staticmethod
    def remove(name):
        pkexec = shutil.which("pkexec")
        if not pkexec:
            raise RuntimeError("pkexec is required to remove snaps "
                               "(no polkit agent found).")
        _run([pkexec, "snap", "remove", name])

    @staticmethod
    def launch(name):
        subprocess.Popen(["snap", "run", name], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


# --------------------------------------------------------------------------- #
#  flatpak
# --------------------------------------------------------------------------- #
class FlatpakBackend:
    name = "flatpak"
    label = "Flatpak"

    @staticmethod
    def available():
        return bool(shutil.which("flatpak"))

    @staticmethod
    def ensure_flathub_remote():
        """Add the Flathub remote (per-user, no root) if it isn't already
        configured — without it, search/install have nothing to look in.
        ``--if-not-exists`` makes this safe to call every time."""
        try:
            _run(["flatpak", "remote-add", "--user", "--if-not-exists",
                  "flathub", FLATHUB_REMOTE_URL])
        except RuntimeError:
            pass  # best-effort: search/install will just come back empty

    EXPORT_DESKTOP_DIRS = [
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
    ]

    @staticmethod
    def _desktop_entry_for_app_id(app_id):
        """Flatpak exports a "<app-id>.desktop" launcher for every
        installed app (user-scoped, since installs here always use
        ``--user``, but system-wide installs are checked too) — reading
        its Icon= gets the icon flatpak list's own columns don't carry."""
        for base in FlatpakBackend.EXPORT_DESKTOP_DIRS:
            entry = parse_desktop_entry(base / f"{app_id}.desktop")
            if entry:
                return entry
        return None

    @staticmethod
    def installed():
        try:
            out = _run(["flatpak", "list", "--app"])
        except RuntimeError:
            return {}
        result = {}
        for row in _parse_table(out):
            app_id = row.get("Application ID", "")
            if not app_id:
                continue
            entry = FlatpakBackend._desktop_entry_for_app_id(app_id) or {}
            result[app_id] = {
                "version": row.get("Version", ""),
                # flatpak list's own "Name" column is already the nice
                # display name (e.g. "GIMP", not "org.gimp.GIMP") —
                # prefer it, the exported .desktop's Name= as a fallback.
                "name": row.get("Name") or entry.get("name") or app_id,
                "icon_data": resolve_icon_bytes(entry.get("icon") or app_id),
            }
        return result

    @staticmethod
    def search(query):
        FlatpakBackend.ensure_flathub_remote()
        try:
            out = _run(["flatpak", "search", query])
        except RuntimeError:
            return []
        results = []
        for row in _parse_table(out):
            app_id = row.get("Application ID", "")
            if not app_id:
                continue
            results.append({
                "id": app_id, "name": row.get("Name", app_id),
                "summary": row.get("Description", ""),
                "version": row.get("Version", ""),
            })
            if len(results) >= MAX_SEARCH_RESULTS:
                break
        return results

    @staticmethod
    def upgradable():
        # Not probed, unlike apt/snap: flatpak has no equally simple,
        # version-stable "list pending updates without applying them"
        # command to build on with confidence. The Update button stays
        # always-available for installed flatpak apps instead (see
        # DetailsPage._actions) — running it is still correct, just not
        # pre-flagged here.
        return set()

    @staticmethod
    def install(app_id):
        FlatpakBackend.ensure_flathub_remote()
        _run(["flatpak", "install", "--user", "-y", "flathub", app_id])

    @staticmethod
    def update(app_id):
        _run(["flatpak", "update", "--user", "-y", app_id])

    @staticmethod
    def remove(app_id):
        _run(["flatpak", "uninstall", "--user", "-y", app_id])

    @staticmethod
    def launch(app_id):
        subprocess.Popen(["flatpak", "run", app_id], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


BACKENDS = {
    "apt": AptBackend,
    "snap": SnapBackend,
    "flatpak": FlatpakBackend,
}


def backend_for(name):
    return BACKENDS.get(name)


def available_backends():
    return [b for b in BACKENDS.values() if b.available()]


# The Store sidebar groups apt/snap/flatpak packages under a pseudo-category
# named after their backend; this maps that label back to the backend name
# so selecting it can show the right search bar and installed packages.
CATEGORY_TO_BACKEND = {f"{b.label} packages": name for name, b in BACKENDS.items()}


def to_app_dict(backend_name, record, installed_version=None):
    """A search or installed record from a backend -> the unified app-dict
    shape the Store grid, sidebar and details page all understand."""
    backend = BACKENDS[backend_name]
    pkg_id = record["id"]
    homepage = None
    if backend_name == "snap":
        homepage = f"https://snapcraft.io/{pkg_id}"
    elif backend_name == "flatpak":
        homepage = f"https://flathub.org/apps/{pkg_id}"
    return {
        "backend": backend_name,
        "key": f"{backend_name}:{pkg_id}",
        "pkg_id": pkg_id,
        "name": record.get("name", pkg_id),
        "tagline": record.get("summary", ""),
        "description": record.get("summary", ""),
        "category": f"{backend.label} packages",
        "version": installed_version or record.get("version", ""),
        "author": backend.label,
        "icon_data": record.get("icon_data"),
        "homepage": homepage,
        "maintained": True,
        "private": False,
    }


class SystemPackageWorker(QThread):
    """Install / update / remove / launch an apt, snap or flatpak package
    without freezing the UI."""
    done = pyqtSignal(bool, str)  # ok, message

    def __init__(self, action, backend_name, pkg_id):
        super().__init__()
        self.action = action
        self.backend_name = backend_name
        self.pkg_id = pkg_id

    def run(self):
        backend = BACKENDS.get(self.backend_name)
        if not backend:
            self.done.emit(False, f"Unknown backend: {self.backend_name}")
            return
        try:
            if self.action == "install":
                backend.install(self.pkg_id)
                self.done.emit(True, "Installed")
            elif self.action == "update":
                backend.update(self.pkg_id)
                self.done.emit(True, "Updated")
            elif self.action == "remove":
                backend.remove(self.pkg_id)
                self.done.emit(True, "Removed")
            else:
                self.done.emit(False, f"Unknown action: {self.action}")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, str(exc))


class InstalledScanWorker(QThread):
    """Collects {backend_name: {pkg_id: version}} and {backend_name:
    {pkg_id with a pending update}} for every available backend, off the
    UI thread — dpkg-query/snap list/flatpak list (and the upgradable
    checks) are fast individually but still worth keeping off a UI that
    just started."""
    loaded = pyqtSignal(dict, dict)  # installed, upgradable

    def run(self):
        installed = {}
        upgradable = {}
        for name, backend in BACKENDS.items():
            if not backend.available():
                installed[name] = {}
                upgradable[name] = set()
                continue
            try:
                installed[name] = backend.installed()
            except Exception:  # noqa: BLE001
                installed[name] = {}
            try:
                upgradable[name] = backend.upgradable()
            except Exception:  # noqa: BLE001
                upgradable[name] = set()
        self.loaded.emit(installed, upgradable)


class PackageSearchWorker(QThread):
    """Search one or more system-package backends without freezing the UI."""
    loaded = pyqtSignal(str, list)  # backend_name, results
    failed = pyqtSignal(str, str)   # backend_name, message

    def __init__(self, backend_names, query):
        super().__init__()
        self.backend_names = backend_names
        self.query = query

    def run(self):
        for backend_name in self.backend_names:
            backend = BACKENDS.get(backend_name)
            if not backend or not backend.available():
                continue
            try:
                self.loaded.emit(backend_name, backend.search(self.query))
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(backend_name, str(exc))
