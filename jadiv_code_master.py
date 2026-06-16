#!/usr/bin/env python3
"""Jadiv Code Master – a desktop app store for jan-tdy applications.

Apps are discovered from GitHub: every jan-tdy repository that ships a
``codemaster-metadata.json`` file is read and its apps are listed in the store.
A single repository can publish several apps (see ``devcontrolenterpise``).

Features:
  * Store     – browse & install every published jan-tdy app
  * Installed – launch / remove what you installed
  * Updates   – see which installed apps have a newer published version
  * Manual    – register jan-tdy apps you installed by hand + settings
  * Code Runner – a small editor/runner kept from the classic Code Master
"""

import sys
import os
import json
import shutil
import subprocess
import hashlib
from pathlib import Path

import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QTextEdit, QLineEdit, QLabel, QScrollArea, QFrame, QGridLayout,
    QSizePolicy, QFileDialog, QMessageBox, QCheckBox, QFormLayout
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt5.QtGui import QPixmap, QColor, QPainter, QFont

APP_NAME = "Jadiv Code Master"
APP_VERSION = "2.0.0"
DEFAULT_USERNAME = "jan-tdy"
DEFAULT_BRANCH = "main"
METADATA_FILE = "codemaster-metadata.json"

CONFIG_DIR = Path.home() / ".config" / "codemaster"
DATA_DIR = Path.home() / ".local" / "share" / "codemaster"
APPS_DIR = DATA_DIR / "apps"
CONFIG_FILE = CONFIG_DIR / "config.json"
INSTALLED_FILE = CONFIG_DIR / "installed.json"

ICON_SIZE = 64


# --------------------------------------------------------------------------- #
#  Persistence helpers
# --------------------------------------------------------------------------- #
def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def load_config():
    cfg = _read_json(CONFIG_FILE, {})
    cfg.setdefault("username", DEFAULT_USERNAME)
    cfg.setdefault("branch", DEFAULT_BRANCH)
    cfg.setdefault("token", "")
    cfg.setdefault("manual_paths", [])
    return cfg


def save_config(cfg):
    _write_json(CONFIG_FILE, cfg)


def load_installed():
    return _read_json(INSTALLED_FILE, {})


def save_installed(data):
    _write_json(INSTALLED_FILE, data)


# --------------------------------------------------------------------------- #
#  Icon helpers
# --------------------------------------------------------------------------- #
def pixmap_from_bytes(data, size=ICON_SIZE):
    if not data:
        return None
    pm = QPixmap()
    if pm.loadFromData(data):
        return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    try:  # vector fallback (e.g. JadivCalc ships an SVG icon)
        from PyQt5.QtSvg import QSvgRenderer
        from PyQt5.QtCore import QByteArray
        renderer = QSvgRenderer(QByteArray(data))
        if renderer.isValid():
            img = QPixmap(size, size)
            img.fill(Qt.transparent)
            painter = QPainter(img)
            renderer.render(painter)
            painter.end()
            return img
    except Exception:
        pass
    return None


def placeholder_pixmap(name, size=ICON_SIZE):
    """A coloured rounded tile with the app's initial – used when no icon."""
    palette = ["#4f8cff", "#ff6b6b", "#1ec98b", "#ffa94d", "#9775fa", "#22b8cf"]
    digest = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
    color = QColor(palette[digest % len(palette)])

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(color)
    painter.setPen(Qt.NoPen)
    radius = size * 0.22
    painter.drawRoundedRect(0, 0, size, size, radius, radius)
    painter.setPen(QColor("white"))
    font = QFont()
    font.setPixelSize(int(size * 0.5))
    font.setBold(True)
    painter.setFont(font)
    letter = (name.strip()[:1] or "?").upper()
    painter.drawText(pm.rect(), Qt.AlignCenter, letter)
    painter.end()
    return pm


# --------------------------------------------------------------------------- #
#  Background workers
# --------------------------------------------------------------------------- #
class CatalogLoader(QThread):
    """Discover every jan-tdy app published through codemaster-metadata.json."""
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, username, branch, token=""):
        super().__init__()
        self.username = username
        self.branch = branch
        self.token = token

    def _headers(self):
        return {"Authorization": f"token {self.token}"} if self.token else {}

    def _list_repos(self):
        repos, page = [], 1
        while True:
            url = (f"https://api.github.com/users/{self.username}/repos"
                   f"?per_page=100&page={page}")
            resp = requests.get(url, headers=self._headers(), timeout=20)
            resp.raise_for_status()
            chunk = resp.json()
            if not chunk:
                break
            repos.extend((r["name"], r.get("default_branch", "main"))
                         for r in chunk)
            if len(chunk) < 100:
                break
            page += 1
        return repos

    def _fetch_metadata(self, repo, default_branch):
        branches = []
        for b in (self.branch, default_branch):
            if b and b not in branches:
                branches.append(b)
        for branch in branches:
            raw = (f"https://raw.githubusercontent.com/{self.username}/"
                   f"{repo}/{branch}/{METADATA_FILE}")
            try:
                resp = requests.get(raw, headers=self._headers(), timeout=20)
            except requests.RequestException:
                continue
            if resp.status_code == 200:
                try:
                    return resp.json(), branch
                except ValueError:
                    return None, None
        return None, None

    def _fetch_icon(self, repo, branch, icon_path):
        if not icon_path:
            return None
        raw = (f"https://raw.githubusercontent.com/{self.username}/"
               f"{repo}/{branch}/{icon_path}")
        try:
            resp = requests.get(raw, headers=self._headers(), timeout=20)
            if resp.status_code == 200:
                return resp.content
        except requests.RequestException:
            return None
        return None

    def run(self):
        try:
            self.status.emit("Contacting GitHub…")
            apps = []
            repos = self._list_repos()
            for repo, default_branch in repos:
                self.status.emit(f"Scanning {repo}…")
                meta, branch = self._fetch_metadata(repo, default_branch)
                if not meta:
                    continue
                for app in meta.get("apps", []):
                    icon_data = self._fetch_icon(repo, branch, app.get("icon"))
                    apps.append({
                        "key": f"{repo}/{app.get('id')}",
                        "repo": repo,
                        "branch": branch,
                        "id": app.get("id"),
                        "name": app.get("name", app.get("id", "?")),
                        "tagline": app.get("tagline", ""),
                        "description": app.get("description", ""),
                        "category": app.get("category", "Other"),
                        "version": str(app.get("version", "")),
                        "author": app.get("author", self.username),
                        "icon_data": icon_data,
                        "subdir": app.get("subdir", "."),
                        "entrypoint": app.get("entrypoint", ""),
                        "run": app.get("run", ""),
                        "requirements": app.get("requirements"),
                        "maintained": app.get("maintained", True),
                        "homepage": app.get("homepage")
                                    or meta.get("homepage"),
                    })
            apps.sort(key=lambda a: (a["category"].lower(), a["name"].lower()))
            self.loaded.emit(apps)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class GitWorker(QThread):
    """Clone / update / install-deps without freezing the UI."""
    done = pyqtSignal(bool, str)

    def __init__(self, action, username, repo, repo_root, branch,
                 req_path=None):
        super().__init__()
        self.action = action
        self.username = username
        self.repo = repo
        self.repo_root = Path(repo_root)
        self.branch = branch
        self.req_path = req_path

    def _run(self, cmd, cwd=None):
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return proc.stdout

    def run(self):
        try:
            url = f"https://github.com/{self.username}/{self.repo}.git"
            if self.action == "install":
                if (self.repo_root / ".git").exists():
                    self._run(["git", "-C", str(self.repo_root), "pull",
                               "--ff-only"])
                else:
                    if self.repo_root.exists():
                        shutil.rmtree(self.repo_root)
                    self.repo_root.parent.mkdir(parents=True, exist_ok=True)
                    cmd = ["git", "clone", "--depth", "1"]
                    if self.branch:
                        cmd += ["--branch", self.branch]
                    cmd += [url, str(self.repo_root)]
                    self._run(cmd)
                self.done.emit(True, "Installed")
            elif self.action == "update":
                self._run(["git", "-C", str(self.repo_root), "pull",
                           "--ff-only"])
                self.done.emit(True, "Updated")
            elif self.action == "deps":
                self._run([sys.executable, "-m", "pip", "install", "-r",
                           str(self.req_path)])
                self.done.emit(True, "Dependencies installed")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, str(exc))


# --------------------------------------------------------------------------- #
#  App card widget
# --------------------------------------------------------------------------- #
class AppCard(QFrame):
    def __init__(self, app, store, context="store"):
        super().__init__()
        self.app = app
        self.store = store
        self.context = context
        self.setObjectName("AppCard")
        self.setFrameShape(QFrame.StyledPanel)
        self._build()

    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(14)

        icon = QLabel()
        pm = pixmap_from_bytes(self.app.get("icon_data")) \
            or placeholder_pixmap(self.app["name"])
        icon.setPixmap(pm)
        icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        icon.setAlignment(Qt.AlignTop)
        outer.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(2)

        title_row = QHBoxLayout()
        title = QLabel(self.app["name"])
        title.setObjectName("AppTitle")
        title_row.addWidget(title)
        if not self.app.get("maintained", True):
            badge = QLabel("unmaintained")
            badge.setObjectName("BadgeWarn")
            title_row.addWidget(badge)
        title_row.addStretch()
        text.addLayout(title_row)

        meta_bits = [self.app["category"]]
        if self.app["version"]:
            meta_bits.append("v" + self.app["version"])
        meta_bits.append(self.app["repo"])
        meta = QLabel("  ·  ".join(meta_bits))
        meta.setObjectName("AppMeta")
        text.addWidget(meta)

        desc = QLabel(self.app.get("tagline")
                      or self.app.get("description", ""))
        desc.setObjectName("AppDesc")
        desc.setWordWrap(True)
        text.addWidget(desc)
        outer.addLayout(text, 1)

        outer.addLayout(self._actions())

    def _actions(self):
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addStretch()
        installed = self.store.is_installed(self.app["key"])

        if self.context in ("installed", "updates") or installed:
            if self.store.has_update(self.app):
                col.addWidget(self._btn("Update", "Primary",
                                        self.store.update_app))
            launch = self._btn("Open", "Primary" if not
                               self.store.has_update(self.app) else "Ghost",
                               self.store.launch_app)
            col.addWidget(launch)
            if self.app.get("requirements"):
                col.addWidget(self._btn("Install deps", "Ghost",
                                        self.store.install_deps))
            col.addWidget(self._btn("Remove", "Ghost",
                                    self.store.uninstall_app))
        else:
            col.addWidget(self._btn("Install", "Primary",
                                    self.store.install_app))
            if self.app.get("homepage"):
                col.addWidget(self._btn("Details", "Ghost",
                                        self.store.open_homepage))
        col.addStretch()
        return col

    def _btn(self, label, kind, slot):
        btn = QPushButton(label)
        btn.setObjectName(kind)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumWidth(110)
        btn.clicked.connect(lambda: slot(self.app))
        return btn


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #
class CodeMaster(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setGeometry(100, 100, 980, 720)

        self.config = load_config()
        self.installed = load_installed()
        self.catalog = []
        self._workers = set()

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        root.addWidget(self._build_header())

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.store_area, self.store_box = self._scroll_page()
        self.installed_area, self.installed_box = self._scroll_page()
        self.updates_area, self.updates_box = self._scroll_page()
        self.tabs.addTab(self.store_area, "Store")
        self.tabs.addTab(self.installed_area, "Installed")
        self.tabs.addTab(self.updates_area, "Updates")
        self.tabs.addTab(self._build_manual_tab(), "Manual & Settings")
        self.tabs.addTab(self._build_runner_tab(), "Code Runner")

        self.setStyleSheet(STYLE)
        self.refresh_views()
        self.load_catalog()

    # -- header ----------------------------------------------------------- #
    def _build_header(self):
        bar = QWidget()
        bar.setObjectName("Header")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 12, 18, 12)

        title = QLabel("🛍  " + APP_NAME)
        title.setObjectName("HeaderTitle")
        lay.addWidget(title)
        lay.addSpacing(20)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setObjectName("Search")
        self.search.textChanged.connect(self.rebuild_store)
        lay.addWidget(self.search, 1)

        self.refresh_btn = QPushButton("⟳ Refresh")
        self.refresh_btn.setObjectName("Primary")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.load_catalog)
        lay.addWidget(self.refresh_btn)
        return bar

    def _scroll_page(self):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setObjectName("Page")
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(18, 18, 18, 18)
        box.setSpacing(12)
        box.addStretch()
        area.setWidget(holder)
        return area, box

    # -- manual & settings tab ------------------------------------------- #
    def _build_manual_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(14)

        intro = QLabel(
            "Register jan-tdy apps you installed manually. Point Code Master "
            "to a folder that contains a <b>codemaster-metadata.json</b> file "
            "and its apps will appear under <b>Installed</b>.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        add_btn = QPushButton("➕  Add app folder…")
        add_btn.setObjectName("Primary")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self.add_manual_folder)
        lay.addWidget(add_btn, alignment=Qt.AlignLeft)

        self.manual_list = QLabel()
        self.manual_list.setWordWrap(True)
        self.manual_list.setObjectName("AppMeta")
        lay.addWidget(self.manual_list)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        lay.addWidget(line)

        settings = QLabel("<b>Settings</b>")
        lay.addWidget(settings)

        form = QFormLayout()
        self.username_in = QLineEdit(self.config["username"])
        self.branch_in = QLineEdit(self.config["branch"])
        self.token_in = QLineEdit(self.config["token"])
        self.token_in.setEchoMode(QLineEdit.Password)
        self.token_in.setPlaceholderText("optional – raises GitHub rate limit")
        form.addRow("GitHub user:", self.username_in)
        form.addRow("Metadata branch:", self.branch_in)
        form.addRow("GitHub token:", self.token_in)
        lay.addLayout(form)

        save_btn = QPushButton("Save settings")
        save_btn.setObjectName("Primary")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self.save_settings)
        lay.addWidget(save_btn, alignment=Qt.AlignLeft)

        lay.addStretch()
        self._refresh_manual_list()
        return page

    def _build_runner_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 18, 18, 18)

        self.code_editor = QTextEdit()
        self.code_editor.setPlaceholderText("Write or paste Python code here…")
        lay.addWidget(self.code_editor)

        run_btn = QPushButton("Run code")
        run_btn.setObjectName("Primary")
        run_btn.setCursor(Qt.PointingHandCursor)
        run_btn.clicked.connect(self.run_code)
        lay.addWidget(run_btn, alignment=Qt.AlignLeft)

        lay.addWidget(QLabel("Output:"))
        self.code_output = QTextEdit()
        self.code_output.setReadOnly(True)
        lay.addWidget(self.code_output)
        return page

    # -- catalog ---------------------------------------------------------- #
    def load_catalog(self):
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("⟳ Loading…")
        self._set_placeholder(self.store_box, "Loading apps from GitHub…")
        loader = CatalogLoader(self.config["username"], self.config["branch"],
                               self.config["token"])
        loader.loaded.connect(self.on_catalog_loaded)
        loader.failed.connect(self.on_catalog_failed)
        loader.status.connect(
            lambda msg: self.refresh_btn.setText("⟳ " + msg[:18]))
        loader.finished.connect(lambda: self._workers.discard(loader))
        self._workers.add(loader)
        loader.start()

    def on_catalog_loaded(self, apps):
        self.catalog = apps
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("⟳ Refresh")
        self.refresh_views()

    def on_catalog_failed(self, message):
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("⟳ Refresh")
        self._set_placeholder(
            self.store_box, f"Could not load catalog:\n{message}")

    # -- view rebuilding -------------------------------------------------- #
    def refresh_views(self):
        self.rebuild_store()
        self.rebuild_installed()
        self.rebuild_updates()
        if hasattr(self, "manual_list"):
            self._refresh_manual_list()

    def _clear(self, box):
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _set_placeholder(self, box, text):
        self._clear(box)
        lbl = QLabel(text)
        lbl.setObjectName("Placeholder")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        box.addWidget(lbl)
        box.addStretch()

    def _section(self, box, title):
        lbl = QLabel(title)
        lbl.setObjectName("Section")
        box.addWidget(lbl)

    def rebuild_store(self):
        box = self.store_box
        self._clear(box)
        query = self.search.text().strip().lower() if hasattr(
            self, "search") else ""
        apps = [a for a in self.catalog
                if query in a["name"].lower()
                or query in a["tagline"].lower()
                or query in a["description"].lower()
                or query in a["category"].lower()]
        if not apps:
            self._set_placeholder(
                box, "No apps found." if self.catalog
                else "No apps yet — press Refresh to load from GitHub.")
            return
        current = None
        for app in apps:
            if app["category"] != current:
                current = app["category"]
                self._section(box, current)
            box.addWidget(AppCard(app, self, context="store"))
        box.addStretch()

    def rebuild_installed(self):
        box = self.installed_box
        self._clear(box)
        apps = self._installed_apps()
        if not apps:
            self._set_placeholder(
                box, "Nothing installed yet.\nInstall apps from the Store, "
                "or register a manual folder under Manual & Settings.")
            return
        for app in apps:
            box.addWidget(AppCard(app, self, context="installed"))
        box.addStretch()

    def rebuild_updates(self):
        box = self.updates_box
        self._clear(box)
        apps = [a for a in self._installed_apps() if self.has_update(a)]
        idx = self.tabs.indexOf(self.updates_area) if hasattr(
            self, "tabs") else -1
        if hasattr(self, "tabs") and idx >= 0:
            self.tabs.setTabText(idx, f"Updates ({len(apps)})"
                                 if apps else "Updates")
        if not apps:
            self._set_placeholder(box, "Everything is up to date. 🎉")
            return
        header = QHBoxLayout()
        update_all = QPushButton("Update all")
        update_all.setObjectName("Primary")
        update_all.setCursor(Qt.PointingHandCursor)
        # One git pull per repo — several apps can share a clone, and parallel
        # pulls in the same directory collide on .git/index.lock.
        seen_repos = set()
        unique_apps = []
        for a in apps:
            if a["repo"] not in seen_repos:
                seen_repos.add(a["repo"])
                unique_apps.append(a)
        update_all.clicked.connect(
            lambda: [self.update_app(a) for a in unique_apps])
        wrap = QWidget()
        wrap.setLayout(header)
        header.addStretch()
        header.addWidget(update_all)
        box.addWidget(wrap)
        for app in apps:
            box.addWidget(AppCard(app, self, context="updates"))
        box.addStretch()

    def _installed_apps(self):
        """Merge live catalog data over the stored installed records."""
        catalog_by_key = {a["key"]: a for a in self.catalog}
        out = []
        for key, rec in self.installed.items():
            app = dict(catalog_by_key.get(key, {}))
            app.update({k: v for k, v in rec.items() if v is not None
                        or k not in app})
            app.setdefault("key", key)
            app.setdefault("name", rec.get("name", key))
            app.setdefault("category", rec.get("category", "Other"))
            app.setdefault("version", rec.get("version", ""))
            app.setdefault("repo", rec.get("repo", key.split("/")[0]))
            # keep icon from catalog if the stored record has none
            if not app.get("icon_data") and key in catalog_by_key:
                app["icon_data"] = catalog_by_key[key].get("icon_data")
            out.append(app)
        out.sort(key=lambda a: a["name"].lower())
        return out

    # -- state queries ---------------------------------------------------- #
    def is_installed(self, key):
        return key in self.installed

    def has_update(self, app):
        rec = self.installed.get(app["key"])
        if not rec:
            return False
        catalog = next((a for a in self.catalog if a["key"] == app["key"]),
                       None)
        if not catalog or not catalog.get("version"):
            return False
        return str(catalog["version"]) != str(rec.get("version", ""))

    # -- install / update / launch --------------------------------------- #
    def _repo_root(self, app):
        if app.get("source") == "manual" and app.get("repo_root"):
            return Path(app["repo_root"])
        return APPS_DIR / app["repo"]

    def install_app(self, app):
        repo_root = APPS_DIR / app["repo"]
        worker = GitWorker("install", self.config["username"], app["repo"],
                           repo_root, app.get("branch", self.config["branch"]))

        def finished(ok, msg):
            self._workers.discard(worker)
            if not ok:
                QMessageBox.warning(self, "Install failed", msg)
                return
            self.installed[app["key"]] = {
                "name": app["name"], "repo": app["repo"],
                "category": app["category"], "version": app["version"],
                "subdir": app.get("subdir", "."), "run": app.get("run", ""),
                "requirements": app.get("requirements"),
                "repo_root": str(repo_root), "source": "store",
            }
            save_installed(self.installed)
            self.refresh_views()
            self._toast(f"Installed {app['name']}")

        worker.done.connect(finished)
        self._workers.add(worker)
        worker.start()
        self._toast(f"Installing {app['name']}…")

    def update_app(self, app):
        repo_root = self._repo_root(app)
        worker = GitWorker("update", self.config["username"], app["repo"],
                           repo_root, app.get("branch", self.config["branch"]))

        def finished(ok, msg):
            self._workers.discard(worker)
            if not ok:
                QMessageBox.warning(self, "Update failed", msg)
                return
            # A pull refreshes the whole clone, so every installed app from
            # this repo is now up to date — sync all their versions.
            for key, inst in list(self.installed.items()):
                if inst.get("repo") == app["repo"]:
                    cat = next((a for a in self.catalog
                                if a["key"] == key), None)
                    if cat:
                        inst["version"] = cat["version"]
            save_installed(self.installed)
            self.refresh_views()
            self._toast(f"Updated {app['name']}")

        worker.done.connect(finished)
        self._workers.add(worker)
        worker.start()
        self._toast(f"Updating {app['name']}…")

    def install_deps(self, app):
        repo_root = self._repo_root(app)
        req = repo_root / app.get("subdir", ".") / app["requirements"]
        if not req.exists():
            QMessageBox.warning(self, "Dependencies",
                                f"Requirements file not found:\n{req}")
            return
        worker = GitWorker("deps", self.config["username"], app["repo"],
                           repo_root, app.get("branch"), req_path=req)

        def finished(ok, msg):
            self._workers.discard(worker)
            QMessageBox.information(
                self, "Dependencies",
                msg if ok else f"Failed to install dependencies:\n{msg}")

        worker.done.connect(finished)
        self._workers.add(worker)
        worker.start()
        self._toast(f"Installing dependencies for {app['name']}…")

    def launch_app(self, app):
        repo_root = self._repo_root(app)
        cwd = repo_root / app.get("subdir", ".")
        run = app.get("run") or (f"python3 {app.get('entrypoint')}"
                                 if app.get("entrypoint") else "")
        if not run:
            QMessageBox.warning(self, "Launch",
                                "No run command defined for this app.")
            return
        if not cwd.exists():
            QMessageBox.warning(self, "Launch",
                                f"App folder not found:\n{cwd}")
            return
        try:
            subprocess.Popen(run, shell=True, cwd=str(cwd))
            self._toast(f"Launched {app['name']}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Launch failed", str(exc))

    def uninstall_app(self, app):
        confirm = QMessageBox.question(
            self, "Remove app",
            f"Remove {app['name']} from your installed apps?")
        if confirm != QMessageBox.Yes:
            return
        rec = self.installed.pop(app["key"], None)
        save_installed(self.installed)
        # Delete the cloned repo only if no other installed app still uses it
        if rec and rec.get("source") != "manual":
            repo = rec.get("repo")
            still_used = any(r.get("repo") == repo
                             for r in self.installed.values())
            repo_root = Path(rec.get("repo_root", APPS_DIR / repo))
            if not still_used and repo_root.exists() and APPS_DIR in \
                    repo_root.parents:
                shutil.rmtree(repo_root, ignore_errors=True)
        self.refresh_views()
        self._toast(f"Removed {app['name']}")

    def open_homepage(self, app):
        url = app.get("homepage")
        if url:
            from PyQt5.QtGui import QDesktopServices
            from PyQt5.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(url))

    # -- manual & settings ----------------------------------------------- #
    def add_manual_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select a folder containing codemaster-metadata.json")
        if not folder:
            return
        meta_path = Path(folder) / METADATA_FILE
        if not meta_path.exists():
            QMessageBox.warning(
                self, "No metadata",
                f"{METADATA_FILE} was not found in:\n{folder}")
            return
        meta = _read_json(meta_path, None)
        if not meta or "apps" not in meta:
            QMessageBox.warning(self, "Invalid metadata",
                                "The metadata file is missing an 'apps' list.")
            return
        repo = meta.get("repo", Path(folder).name)
        count = 0
        for app in meta["apps"]:
            key = f"{repo}/{app.get('id')}"
            self.installed[key] = {
                "name": app.get("name", app.get("id")),
                "repo": repo, "category": app.get("category", "Other"),
                "version": str(app.get("version", "")),
                "subdir": app.get("subdir", "."), "run": app.get("run", ""),
                "requirements": app.get("requirements"),
                "repo_root": folder, "source": "manual",
            }
            count += 1
        if folder not in self.config["manual_paths"]:
            self.config["manual_paths"].append(folder)
            save_config(self.config)
        save_installed(self.installed)
        self.refresh_views()
        self._toast(f"Registered {count} app(s) from {repo}")

    def _refresh_manual_list(self):
        manual = [f"• {p}" for p in self.config.get("manual_paths", [])]
        self.manual_list.setText(
            "<br>".join(manual) if manual
            else "<i>No manual app folders registered yet.</i>")

    def save_settings(self):
        self.config["username"] = self.username_in.text().strip() \
            or DEFAULT_USERNAME
        self.config["branch"] = self.branch_in.text().strip() or DEFAULT_BRANCH
        self.config["token"] = self.token_in.text().strip()
        save_config(self.config)
        self._toast("Settings saved")
        self.load_catalog()

    # -- code runner ------------------------------------------------------ #
    def run_code(self):
        code = self.code_editor.toPlainText()
        temp_file = os.path.join(DATA_DIR, "temp_code.py")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(temp_file, "w", encoding="utf-8") as fh:
                fh.write(code)
            proc = subprocess.run([sys.executable, temp_file],
                                  capture_output=True, text=True, timeout=15)
            self.code_output.setText(proc.stdout + proc.stderr)
        except subprocess.TimeoutExpired:
            self.code_output.setText("Execution timed out after 15 seconds.")
        except Exception as exc:  # noqa: BLE001
            self.code_output.setText(str(exc))

    # -- misc ------------------------------------------------------------- #
    def _toast(self, message):
        self.statusBar().showMessage(message, 4000)


STYLE = """
QMainWindow, QWidget { background: #14161c; color: #e7e9ee;
    font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; }
#Header { background: #1b1e26; border-bottom: 1px solid #2a2e3a; }
#HeaderTitle { font-size: 18px; font-weight: 700; }
#Search { background: #262b36; border: 1px solid #333a48; border-radius: 8px;
    padding: 7px 12px; color: #e7e9ee; }
#Search:focus { border: 1px solid #4f8cff; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #9aa3b2; padding: 10px 18px;
    border: none; font-weight: 600; }
QTabBar::tab:selected { color: #e7e9ee; border-bottom: 2px solid #4f8cff; }
QTabBar::tab:hover { color: #e7e9ee; }
#Page { background: #14161c; border: none; }
#AppCard { background: #1b1e26; border: 1px solid #262b36; border-radius: 12px; }
#AppCard:hover { border: 1px solid #3a4458; }
#AppTitle { font-size: 15px; font-weight: 700; }
#AppMeta { color: #8b93a4; font-size: 12px; }
#AppDesc { color: #b6bdca; }
#Section { font-size: 13px; font-weight: 700; color: #7f8aa0;
    text-transform: uppercase; letter-spacing: 1px; margin-top: 8px; }
#Placeholder { color: #7f8aa0; font-size: 14px; padding: 60px; }
#BadgeWarn { background: #4a3214; color: #ffb347; border-radius: 6px;
    padding: 1px 8px; font-size: 11px; font-weight: 600; }
QPushButton#Primary { background: #4f8cff; color: white; border: none;
    border-radius: 8px; padding: 8px 16px; font-weight: 600; }
QPushButton#Primary:hover { background: #3d7bf0; }
QPushButton#Primary:disabled { background: #2f3947; color: #8b93a4; }
QPushButton#Ghost { background: transparent; color: #cdd3df;
    border: 1px solid #333a48; border-radius: 8px; padding: 8px 16px; }
QPushButton#Ghost:hover { border: 1px solid #4f8cff; color: #fff; }
QTextEdit, QLineEdit { background: #1b1e26; border: 1px solid #2a2e3a;
    border-radius: 8px; padding: 8px; color: #e7e9ee; }
QScrollBar:vertical { background: #14161c; width: 10px; }
QScrollBar::handle:vertical { background: #333a48; border-radius: 5px; }
QStatusBar { background: #1b1e26; color: #9aa3b2; }
"""


def main():
    app = QApplication(sys.argv)
    window = CodeMaster()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
