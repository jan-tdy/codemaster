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

    @staticmethod
    def installed():
        """{package: version} for every apt-installed package."""
        if not shutil.which("dpkg-query"):
            return {}
        try:
            out = _run(["dpkg-query", "-W", "-f=${Package}\\t${Version}\\n"])
        except RuntimeError:
            return {}
        result = {}
        for line in out.splitlines():
            if "\t" in line:
                pkg, version = line.split("\t", 1)
                result[pkg] = version
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

    @staticmethod
    def installed():
        try:
            out = _run(["snap", "list"])
        except RuntimeError:
            return {}
        return {row.get("Name", ""): row.get("Version", "")
                for row in _parse_table(out) if row.get("Name")}

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

    @staticmethod
    def installed():
        try:
            out = _run(["flatpak", "list", "--app"])
        except RuntimeError:
            return {}
        result = {}
        for row in _parse_table(out):
            app_id = row.get("Application ID", "")
            if app_id:
                result[app_id] = row.get("Version", "")
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
        "icon_data": None,
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
    """Collects {backend_name: {pkg_id: version}} for every available
    backend, off the UI thread — dpkg-query/snap list/flatpak list are
    fast individually but still worth keeping off a UI that just started."""
    loaded = pyqtSignal(dict)

    def run(self):
        result = {}
        for name, backend in BACKENDS.items():
            if backend.available():
                try:
                    result[name] = backend.installed()
                except Exception:  # noqa: BLE001
                    result[name] = {}
            else:
                result[name] = {}
        self.loaded.emit(result)


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
