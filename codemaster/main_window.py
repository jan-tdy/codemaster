"""The CodeMaster main window: wires the catalog loader, git worker,
system-package backends and the tile-grid UI together."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QStackedWidget,
    QTabWidget, QVBoxLayout, QWidget,
)

from .catalog_loader import CatalogLoader
from .constants import (
    APP_NAME, APP_VERSION, APPLICATIONS_DIR, APPS_DIR,
    DEFAULT_BRANCH, DEFAULT_USERNAME, DESKTOP_CATEGORIES, LAUNCHER_ICON_DIR,
    METADATA_FILE, SELF_DIR,
)
from .desktop_entry import desktop_exec_line
from .git_worker import GitWorker
from .icons import pixmap_from_bytes, placeholder_pixmap
from .persistence import (
    load_config, load_installed, load_catalog_cache, migrate_installed_keys,
    read_json, save_catalog_cache, save_config, save_installed,
)
from .style import STYLE
from .system_packages import (
    BACKENDS, CATEGORY_TO_BACKEND, InstalledScanWorker, PackageSearchWorker,
    SystemPackageWorker, available_backends, to_app_dict,
)
from .widgets import (
    AppTile, CategorySidebar, DetailsPage, FilterBar, SystemPackageSearchBar,
    TileGrid,
)

# Fixed "Source" filter options — always offered (even with zero current
# results) so switching to a backend the user hasn't searched yet is one
# click away, same reasoning as the sidebar's backend pseudo-categories.
SOURCE_OPTIONS = [
    ("JapySoft scan (jan-tdy)", "jan-tdy"),
    ("Community catalog", "community"),
    ("APT", "apt"),
    ("Snap", "snap"),
    ("Flatpak", "flatpak"),
]


def app_source(app):
    """Where an app dict came from: the jan-tdy repo scan, a community
    catalog submission, or a system-package backend name."""
    backend = app.get("backend", "git")
    if backend != "git":
        return backend
    return app.get("catalog_source", "jan-tdy")


class CodeMaster(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setGeometry(100, 100, 1180, 760)

        self.config = load_config()
        self.installed = load_installed()
        if migrate_installed_keys(self.installed, self.config["username"]):
            save_installed(self.installed)
        # Show the previously-scanned catalog instantly; a fresh scan runs in
        # the background right after the window is up.
        self.catalog = load_catalog_cache()
        self.system_installed = {name: {} for name in BACKENDS}
        self.system_upgradable = {name: set() for name in BACKENDS}
        self._system_search_results = {name: [] for name in BACKENDS}
        self._workers = set()
        # Keys (str(repo_root)) with a GitWorker currently cloning/pulling/
        # installing deps into them — guards against a second click (or a
        # Remove) racing a running git operation on the same clone. Several
        # app keys can share one repo_root, so this is keyed on the clone
        # path itself, not the app key.
        self._busy = set()
        self._self_commit = None
        # "Update All" state: apps still to process, and whether the queue
        # is actively running — kept separate from the queue being empty,
        # since the last queued app is still mid-update for a moment after
        # it's popped.
        self._update_queue = []
        self._update_all_running = False

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        root.addWidget(self._build_header())

        self.content_stack = QStackedWidget()
        root.addWidget(self.content_stack, 1)

        self.tabs = QTabWidget()
        self.content_stack.addWidget(self.tabs)
        self.details_page = DetailsPage(self)
        self.details_page.backRequested.connect(
            lambda: self.content_stack.setCurrentWidget(self.tabs))
        self.content_stack.addWidget(self.details_page)

        (self.store_sidebar, self.store_filter_bar, self.store_search_bar,
         self.store_grid) = self._build_store_tab()
        self.installed_grid = TileGrid()
        self.updates_grid = TileGrid()
        self.tabs.addTab(self._wrap(self.store_sidebar,
                                    [self.store_filter_bar, self.store_search_bar],
                                    self.store_grid), "Store")
        self.tabs.addTab(self._wrap(None, [], self.installed_grid), "Installed")
        self.update_all_btn = QPushButton("Update All")
        self.update_all_btn.setObjectName("Primary")
        self.update_all_btn.setCursor(Qt.PointingHandCursor)
        self.update_all_btn.setEnabled(False)
        self.update_all_btn.clicked.connect(self.update_all)
        updates_bar = QWidget()
        updates_bar_lay = QHBoxLayout(updates_bar)
        updates_bar_lay.setContentsMargins(18, 12, 18, 0)
        updates_bar_lay.addWidget(self.update_all_btn)
        updates_bar_lay.addStretch()
        self.tabs.addTab(self._wrap(None, [updates_bar], self.updates_grid), "Updates")
        self.tabs.addTab(self._build_manual_tab(), "Manual & Settings")

        self.setStyleSheet(STYLE)
        self.refresh_views()
        self.load_catalog()
        self._scan_system_installed()

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
        # Only the Store grid reads the search box (_matches_search is only
        # used there) — wiring this to refresh_views() used to rebuild
        # Installed/Updates/Manual too on every keystroke for nothing.
        self.search.textChanged.connect(self.rebuild_store)
        lay.addWidget(self.search, 1)

        self.refresh_btn = QPushButton("⟳ Refresh")
        self.refresh_btn.setObjectName("Primary")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        lay.addWidget(self.refresh_btn)
        return bar

    def _on_refresh_clicked(self):
        self.load_catalog()
        self._scan_system_installed()

    # -- tab construction -------------------------------------------------- #
    def _wrap(self, sidebar, top_widgets, grid):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if sidebar is not None:
            lay.addWidget(sidebar)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        for widget in top_widgets:
            right_lay.addWidget(widget)
        right_lay.addWidget(grid, 1)
        lay.addWidget(right, 1)
        return page

    def _build_store_tab(self):
        sidebar = CategorySidebar()
        sidebar.categoryChanged.connect(self._on_store_category_changed)
        filter_bar = FilterBar()
        filter_bar.set_sources(SOURCE_OPTIONS)
        filter_bar.changed.connect(self.rebuild_store)
        search_bar = SystemPackageSearchBar("")
        search_bar.hide()
        search_bar.searchRequested.connect(self._on_system_package_search)
        grid = TileGrid()
        return sidebar, filter_bar, search_bar, grid

    def _on_store_category_changed(self, _category):
        backend = CATEGORY_TO_BACKEND.get(self.store_sidebar.current_category())
        if backend:
            self.store_search_bar.set_backend_label(BACKENDS[backend].label)
            self.store_search_bar.show()
        else:
            self.store_search_bar.hide()
        self.rebuild_store()

    # -- manual & settings tab ------------------------------------------- #
    def _build_manual_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(14)

        intro = QLabel(
            "Register apps you installed manually. Point Code Master to a "
            "folder that contains a <b>codemaster-metadata.json</b> file "
            "and its apps will appear under <b>Installed</b>.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        add_btn = QPushButton("➕  Add app folder…")
        add_btn.setObjectName("Primary")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self.add_manual_folder)
        lay.addWidget(add_btn, alignment=Qt.AlignLeft)

        self.manual_list_holder = QWidget()
        self.manual_list_box = QVBoxLayout(self.manual_list_holder)
        self.manual_list_box.setContentsMargins(0, 0, 0, 0)
        self.manual_list_box.setSpacing(6)
        lay.addWidget(self.manual_list_holder)

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
        self.token_in.setPlaceholderText(
            "optional – required to see/update private repos, also raises "
            "GitHub rate limit")
        form.addRow("GitHub user:", self.username_in)
        form.addRow("Metadata branch:", self.branch_in)
        form.addRow("GitHub token:", self.token_in)
        lay.addLayout(form)

        save_btn = QPushButton("Save settings")
        save_btn.setObjectName("Primary")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self.save_settings)
        lay.addWidget(save_btn, alignment=Qt.AlignLeft)

        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        lay.addWidget(line2)

        backends_note = QLabel(
            "<b>System packages</b> — " + ", ".join(
                f"{b.label} {'✓ available' if b.available() else '✗ not found'}"
                for b in BACKENDS.values()))
        backends_note.setObjectName("AppMeta")
        backends_note.setWordWrap(True)
        lay.addWidget(backends_note)

        line3 = QFrame()
        line3.setFrameShape(QFrame.HLine)
        lay.addWidget(line3)

        lay.addWidget(QLabel(f"<b>About Code Master</b> — v{APP_VERSION}"))
        self_update_row = QHBoxLayout()
        self.self_update_btn = QPushButton("Update Code Master")
        self.self_update_btn.setObjectName("Primary")
        self.self_update_btn.setCursor(Qt.PointingHandCursor)
        self.self_update_btn.clicked.connect(self.self_update)
        self_update_row.addWidget(self.self_update_btn)
        self_update_row.addStretch()
        lay.addLayout(self_update_row)
        self.self_update_note = QLabel()
        self.self_update_note.setObjectName("AppMeta")
        self.self_update_note.setWordWrap(True)
        lay.addWidget(self.self_update_note)

        lay.addStretch()
        self._refresh_manual_list()
        self._refresh_self_update_note()
        return page

    # -- catalog ---------------------------------------------------------- #
    def load_catalog(self):
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("⟳ Loading…")
        # If we already have a cached catalog on screen, keep it visible and
        # just refresh in the background; otherwise show the (slow scan) notice.
        if not self.catalog:
            self.store_grid.set_placeholder(
                "Scanning every jan-tdy repository on GitHub (and the "
                "community catalog) for apps…\n"
                "This can take up to a minute on the first run.")
        loader = CatalogLoader(self.config["username"], self.config["branch"],
                               self.config["token"], cached_apps=self.catalog)
        loader.loaded.connect(self.on_catalog_loaded)
        loader.failed.connect(self.on_catalog_failed)
        loader.status.connect(
            lambda msg: self.refresh_btn.setText("⟳ " + msg[:18]))
        loader.finished.connect(lambda: self._workers.discard(loader))
        self._workers.add(loader)
        loader.start()

    def on_catalog_loaded(self, apps):
        self.catalog = apps
        save_catalog_cache(apps)
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("⟳ Refresh")
        self.refresh_views()
        self._toast(f"Found {len(apps)} app(s)")

    def on_catalog_failed(self, message):
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("⟳ Refresh")
        # Keep the cached catalog on screen if the refresh failed (e.g. offline).
        if self.catalog:
            self._toast(f"Refresh failed, showing cached apps: {message}")
        else:
            self.store_grid.set_placeholder(f"Could not load catalog:\n{message}")

    def _scan_system_installed(self):
        worker = InstalledScanWorker()
        worker.loaded.connect(self._on_system_installed_loaded)
        worker.finished.connect(lambda: self._workers.discard(worker))
        self._workers.add(worker)
        worker.start()

    def _on_system_installed_loaded(self, installed, upgradable):
        self.system_installed = installed
        self.system_upgradable = upgradable
        self.refresh_views()

    # -- view rebuilding -------------------------------------------------- #
    def refresh_views(self, *_):
        self.rebuild_store()
        self.rebuild_installed()
        self.rebuild_updates()
        if hasattr(self, "manual_list_box"):
            self._refresh_manual_list()
        self._refresh_self_update_note()

    def _matches_search(self, app, query):
        if not query:
            return True
        haystack = " ".join([
            app.get("name", ""), app.get("tagline", ""),
            app.get("description", ""), app.get("category", ""),
        ]).lower()
        return query in haystack

    def _system_apps(self, include_search=True):
        apps = []
        seen = set()
        for backend_name in BACKENDS:
            for pkg_id, info in self.system_installed.get(backend_name, {}).items():
                seen.add(f"{backend_name}:{pkg_id}")
                apps.append(to_app_dict(backend_name, {
                    "id": pkg_id, "name": info.get("name", pkg_id),
                    "icon_data": info.get("icon_data"),
                }, installed_version=info.get("version", "")))
            if include_search:
                for record in self._system_search_results.get(backend_name, []):
                    key = f"{backend_name}:{record['id']}"
                    if key in seen:
                        continue
                    seen.add(key)
                    apps.append(to_app_dict(backend_name, record))
        return apps

    def rebuild_store(self):
        query = self.search.text().strip().lower()
        all_apps = self.catalog + self._system_apps(include_search=True)
        categories = sorted({a.get("category", "Other") for a in all_apps} |
                            {f"{b.label} packages" for b in available_backends()})
        self.store_sidebar.set_categories(categories)
        publishers = sorted({a.get("publisher") for a in self.catalog
                             if a.get("backend", "git") == "git" and a.get("publisher")})
        self.store_filter_bar.set_publishers(publishers)
        category = self.store_sidebar.current_category()
        source = self.store_filter_bar.current_source()
        publisher = self.store_filter_bar.current_publisher()
        apps = [a for a in all_apps if self._matches_search(a, query)]
        if category:
            apps = [a for a in apps if a.get("category") == category]
        if source:
            apps = [a for a in apps if app_source(a) == source]
        if publisher:
            apps = [a for a in apps if a.get("backend", "git") == "git"
                   and a.get("publisher") == publisher]
        if not apps:
            if category in CATEGORY_TO_BACKEND:
                self.store_grid.set_placeholder(
                    "No packages yet — type a search above and press Search.")
            else:
                self.store_grid.set_placeholder(
                    "No apps found." if self.catalog
                    else "No apps yet — press Refresh to load from GitHub.")
            return
        self.store_grid.set_tiles([AppTile(a, self) for a in apps])

    def rebuild_installed(self):
        apps = self._installed_apps()
        if not apps:
            self.installed_grid.set_placeholder(
                "Nothing installed yet.\nInstall apps from the Store, or "
                "register a manual folder under Manual & Settings.")
            return
        self.installed_grid.set_tiles([AppTile(a, self) for a in apps])

    def rebuild_updates(self):
        apps = [a for a in self._installed_apps() if self.has_update(a)]
        idx = self.tabs.indexOf(self.tabs.widget(2)) if hasattr(self, "tabs") else -1
        if hasattr(self, "tabs") and idx >= 0:
            self.tabs.setTabText(idx, f"Updates ({len(apps)})" if apps else "Updates")
        if hasattr(self, "update_all_btn"):
            self.update_all_btn.setEnabled(bool(apps) and not self._update_all_running)
        if not apps:
            self.updates_grid.set_placeholder("Everything is up to date. 🎉")
            return
        self.updates_grid.set_tiles([AppTile(a, self) for a in apps])

    def _installed_apps(self):
        """Merge live catalog data over the stored installed records, plus
        every currently-installed apt/snap/flatpak package."""
        catalog_by_key = {a["key"]: a for a in self.catalog}
        out = []
        for key, rec in self.installed.items():
            app = dict(catalog_by_key.get(key, {}))
            app.update({k: v for k, v in rec.items() if v is not None
                        or k not in app})
            app.setdefault("key", key)
            app.setdefault("backend", "git")
            app.setdefault("name", rec.get("name", key))
            app.setdefault("category", rec.get("category", "Other"))
            app.setdefault("version", rec.get("version", ""))
            app.setdefault("repo", rec.get("repo", ""))
            app.setdefault("publisher", rec.get("publisher", self.config["username"]))
            # keep icon from catalog if the stored record has none
            if not app.get("icon_data") and key in catalog_by_key:
                app["icon_data"] = catalog_by_key[key].get("icon_data")
            out.append(app)
        out.extend(self._system_apps(include_search=False))
        out.sort(key=lambda a: a["name"].lower())
        return out

    # -- state queries ---------------------------------------------------- #
    def is_installed(self, app):
        backend = app.get("backend", "git")
        if backend == "git":
            return app.get("key") in self.installed
        return app.get("pkg_id") in self.system_installed.get(backend, {})

    @staticmethod
    def effective_version(app):
        """The version we compare on: a release tag for 'release' git
        apps, otherwise the declared/installed version string."""
        if app.get("backend", "git") != "git":
            return str(app.get("version", ""))
        if app.get("update_method") == "release" and app.get("release_tag"):
            return str(app["release_tag"])
        return str(app.get("version", ""))

    def has_update(self, app):
        backend = app.get("backend", "git")
        if backend != "git":
            # apt/snap are actually probed (InstalledScanWorker); flatpak
            # isn't — its Update button stays always-available instead of
            # pre-flagged (see FlatpakBackend.upgradable and
            # DetailsPage._actions).
            return app.get("pkg_id") in self.system_upgradable.get(backend, set())
        rec = self.installed.get(app["key"])
        if not rec:
            return False
        catalog = next((a for a in self.catalog if a["key"] == app["key"]), None)
        if not catalog:
            return False
        if catalog.get("update_method", "sync") != "release":
            # "sync" apps track the latest commit on a branch, not a version
            # string a publisher might forget to bump — compare commit SHAs
            # so an update is still offered when only the code moved.
            latest_commit = catalog.get("latest_commit")
            if not latest_commit:
                return False
            return latest_commit != rec.get("commit")
        latest = self.effective_version(catalog)
        if not latest:
            return False
        return latest != str(rec.get("version", ""))

    def has_launcher(self, app):
        return self._launcher_path(app).exists()

    # -- confirmations ------------------------------------------------------ #
    def _confirm_third_party(self, app):
        if app.get("catalog_source") != "community":
            return True
        confirmed = self.config.setdefault("confirmed_third_party", [])
        if app["key"] in confirmed:
            return True
        resp = QMessageBox.question(
            self, "Community app",
            f"{app.get('name', app['key'])} is a community catalog "
            f"submission published by {app.get('publisher')}, not "
            f"JapySoft/jan-tdy.\n\nInstalling it clones code from "
            f"https://github.com/{app.get('publisher')}/{app.get('repo')} "
            "onto your computer, and that code can do anything your user "
            "account can. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return False
        confirmed.append(app["key"])
        try:
            save_config(self.config)
        except OSError:
            pass
        return True

    def _confirm_run(self, app, run):
        """Ask the user to confirm a publisher-supplied run command before
        it is ever executed or baked into a launcher. The command comes
        verbatim from the app's metadata, so anyone who can set it controls
        what this runs. Re-asking is skipped once the exact command has
        already been confirmed for this app; a changed command (e.g. after
        an update) prompts again."""
        confirmed = self.config.setdefault("confirmed_commands", {})
        if confirmed.get(app["key"]) == run:
            return True
        resp = QMessageBox.question(
            self, "Confirm command",
            f"{app.get('name', app['key'])} will run this command on your "
            f"machine:\n\n{run}\n\n"
            "This comes from the app's publisher metadata. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return False
        confirmed[app["key"]] = run
        try:
            save_config(self.config)
        except OSError as exc:
            QMessageBox.warning(
                self, "Settings",
                f"Confirmation was not saved, so this will ask again next "
                f"time:\n{exc}")
        return True

    # -- install / update / launch (git apps) ----------------------------- #
    def _resolve(self, app):
        """Overlay the stored installed record onto an app dict.

        Tiles built from the raw catalog dict have no ``source``/``repo_root``;
        without this, acting on a manually-added app from the Store would
        wrongly target the default apps/ path instead of the folder it was
        installed from."""
        if not app:
            return app
        rec = self.installed.get(app.get("key"))
        if not rec:
            return app
        return {**app, **{k: v for k, v in rec.items() if v is not None}}

    def _repo_root(self, app):
        # Trust the installed record (looked up by key), not the passed dict.
        if not app:
            return APPS_DIR
        rec = self.installed.get(app.get("key"))
        if rec and rec.get("repo_root"):
            return Path(rec["repo_root"])
        publisher = app.get("publisher", self.config["username"])
        return APPS_DIR / publisher / app.get("repo", "")

    def is_busy(self, app):
        """Whether a GitWorker is currently cloning/pulling/installing deps
        into this app's clone — used to keep Install/Update/Remove/Install
        deps disabled while one runs, so a second click (possibly from a
        different tab's tile for the same repo) can't start a second git
        operation on the same directory."""
        if app.get("backend", "git") != "git":
            return False
        return str(self._repo_root(app)) in self._busy

    def open_details(self, app):
        resolved = self._resolve(app) if app.get("backend", "git") == "git" else app
        self.details_page.show_app(resolved)
        self.content_stack.setCurrentWidget(self.details_page)

    def install_app(self, app):
        if app.get("backend", "git") != "git":
            self._install_system_package(app)
            return
        if not self._confirm_third_party(app):
            return
        publisher = app.get("publisher", self.config["username"])
        repo_root = APPS_DIR / publisher / app["repo"]
        busy_key = str(repo_root)
        if busy_key in self._busy:
            self._toast(f"{app['name']} already has an operation in progress")
            return
        self._busy.add(busy_key)
        worker = GitWorker("install", publisher, app["repo"], repo_root,
                           app.get("branch", self.config["branch"]),
                           method=app.get("update_method", "sync"),
                           release_tag=app.get("release_tag"),
                           token=self.config["token"])

        def finished(ok, msg, commit=""):
            self._workers.discard(worker)
            self._busy.discard(busy_key)
            if not ok:
                QMessageBox.warning(self, "Install failed", msg)
                self.refresh_views()
                return
            self.installed[app["key"]] = {
                "name": app["name"], "repo": app["repo"],
                "publisher": publisher,
                "category": app["category"],
                "version": self.effective_version(app),
                # Only ever the commit GitWorker actually checked out — never
                # the pre-fetch catalog SHA. If HEAD resolution failed
                # (rev-parse error after an otherwise successful clone/pull),
                # this comes back empty, and the app is simply flagged as
                # updatable again next time rather than trusting a guess.
                "commit": commit,
                "subdir": app.get("subdir", "."), "run": app.get("run", ""),
                "requirements": app.get("requirements"),
                "mime_types": app.get("mime_types") or [],
                "update_method": app.get("update_method", "sync"),
                "repo_root": str(repo_root), "source": "store",
            }
            save_installed(self.installed)
            self.refresh_views()
            self._toast(f"Installed {app['name']}")

        worker.done.connect(finished)
        self._workers.add(worker)
        worker.start()
        self.refresh_views()
        self._toast(f"Installing {app['name']}…")

    def update_app(self, app, on_done=None):
        if app.get("backend", "git") != "git":
            self._update_system_package(app, on_done=on_done)
            return
        app = self._resolve(app)
        repo_root = self._repo_root(app)
        busy_key = str(repo_root)
        if busy_key in self._busy:
            self._toast(f"{app['name']} already has an operation in progress")
            if on_done:
                on_done()
            return
        self._busy.add(busy_key)
        publisher = app.get("publisher", self.config["username"])
        catalog = next((a for a in self.catalog if a["key"] == app["key"]),
                       None) or app
        worker = GitWorker("update", publisher, app["repo"], repo_root,
                           app.get("branch", self.config["branch"]),
                           method=catalog.get("update_method", "sync"),
                           release_tag=catalog.get("release_tag"),
                           token=self.config["token"])

        def finished(ok, msg, commit=""):
            self._workers.discard(worker)
            self._busy.discard(busy_key)
            if not ok:
                QMessageBox.warning(self, "Update failed", msg)
                self.refresh_views()
                if on_done:
                    on_done()
                return
            # Refreshing the clone updates every installed app from this repo —
            # sync each one's stored version to the freshly installed one.
            for key, inst in list(self.installed.items()):
                if inst.get("repo") == app["repo"] and \
                        inst.get("publisher", self.config["username"]) == publisher:
                    cat = next((a for a in self.catalog
                                if a["key"] == key), None)
                    if cat:
                        inst["version"] = self.effective_version(cat)
                        # Same rule as install_app: never substitute the
                        # catalog's pre-fetch SHA for a failed HEAD resolve.
                        inst["commit"] = commit
                        # Pick up a publisher's changed run command too —
                        # _resolve() overlays this stored value over the
                        # catalog's, so a stale one here would silently keep
                        # launching the old command (and never re-prompt via
                        # _confirm_run, since that compares against this).
                        inst["run"] = cat.get("run", "")
            save_installed(self.installed)
            self.refresh_views()
            self._toast(f"Updated {app['name']}")
            if on_done:
                on_done()

        worker.done.connect(finished)
        self._workers.add(worker)
        worker.start()
        self.refresh_views()
        self._toast(f"Updating {app['name']}…")

    def update_all(self):
        """Update every app currently listed in the Updates tab, one at a
        time — apt/snap updates go through pkexec, and running several at
        once would just fight over the package manager's lock."""
        if self._update_all_running:
            return
        apps = [a for a in self._installed_apps() if self.has_update(a)]
        if not apps:
            return
        self._update_queue = apps
        self._update_all_running = True
        self.update_all_btn.setEnabled(False)
        self._toast(f"Updating {len(apps)} app(s)…")
        self._run_next_queued_update()

    def _run_next_queued_update(self):
        if not self._update_queue:
            self._update_all_running = False
            self._toast("Update All finished")
            self.refresh_views()
            return
        app = self._update_queue.pop(0)
        self.update_app(app, on_done=self._run_next_queued_update)

    def install_deps(self, app):
        app = self._resolve(app)
        repo_root = self._repo_root(app)
        req = repo_root / app.get("subdir", ".") / app["requirements"]
        if not req.exists():
            QMessageBox.warning(self, "Dependencies",
                                f"Requirements file not found:\n{req}")
            return
        busy_key = str(repo_root)
        if busy_key in self._busy:
            self._toast(f"{app['name']} already has an operation in progress")
            return
        self._busy.add(busy_key)
        publisher = app.get("publisher", self.config["username"])
        worker = GitWorker("deps", publisher, app["repo"],
                           repo_root, app.get("branch"), req_path=req)

        def finished(ok, msg, _commit=""):
            self._workers.discard(worker)
            self._busy.discard(busy_key)
            self.refresh_views()
            QMessageBox.information(
                self, "Dependencies",
                msg if ok else f"Failed to install dependencies:\n{msg}")

        worker.done.connect(finished)
        self._workers.add(worker)
        worker.start()
        self.refresh_views()
        self._toast(f"Installing dependencies for {app['name']}…")

    def launch_app(self, app):
        if app.get("backend", "git") != "git":
            try:
                BACKENDS[app["backend"]].launch(app["pkg_id"])
                self._toast(f"Launched {app['name']}")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Launch failed", str(exc))
            return
        app = self._resolve(app)
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
        if not self._confirm_run(app, run):
            return
        try:
            argv = shlex.split(run)
        except ValueError as exc:
            QMessageBox.warning(self, "Launch failed",
                                f"Could not parse run command:\n{exc}")
            return
        # Capture stderr to a temp file (not a pipe — a long-running app that
        # logs a lot would eventually fill an unread pipe buffer and hang) so
        # that if the process dies right away (e.g. a missing dependency) we
        # can show *why* instead of silently reporting "Launched" while
        # nothing actually opens.
        log_fd, log_path = tempfile.mkstemp(prefix="codemaster-launch-")
        try:
            # No shell=True: argv is executed directly, so shell
            # metacharacters in a publisher-supplied run command can't be
            # used to inject extra commands.
            proc = subprocess.Popen(argv, cwd=str(cwd),
                                    stdout=subprocess.DEVNULL, stderr=log_fd)
        except Exception as exc:  # noqa: BLE001
            os.unlink(log_path)
            QMessageBox.warning(self, "Launch failed", str(exc))
            return
        finally:
            # Popen(stderr=<fd>) duplicates it into the child; our copy must
            # be closed too, or the file descriptor is never released.
            os.close(log_fd)
        self._toast(f"Launched {app['name']}")

        def check_alive():
            ret = proc.poll()
            err = ""
            if ret not in (None, 0):
                try:
                    err = Path(log_path).read_text(
                        encoding="utf-8", errors="replace").strip()
                except OSError:
                    pass
            # Whether it exited or is still running, this is the only check
            # we ever do — nothing will read the log again, so unlink it now
            # rather than leaking one file per launch into the temp dir.
            try:
                os.unlink(log_path)
            except OSError:
                pass
            if ret not in (None, 0):
                msg = f"{app['name']} exited immediately (code {ret})."
                if err:
                    msg += "\n\n" + err[-2000:]
                QMessageBox.warning(self, "Launch failed", msg)

        QTimer.singleShot(1500, check_alive)

    # -- install / update / remove (system packages) ----------------------- #
    def _install_system_package(self, app):
        self._run_system_worker("install", app, f"Installing {app['name']}…")

    def _update_system_package(self, app, on_done=None):
        self._run_system_worker("update", app, f"Updating {app['name']}…",
                                on_done=on_done)

    def _run_system_worker(self, action, app, toast, on_done=None):
        backend_name = app["backend"]
        pkg_id = app["pkg_id"]
        worker = SystemPackageWorker(action, backend_name, pkg_id)

        def finished(ok, msg):
            self._workers.discard(worker)
            if not ok:
                QMessageBox.warning(self, f"{action.capitalize()} failed", msg)
                if on_done:
                    on_done()
                return
            bucket = self.system_installed.setdefault(backend_name, {})
            # Whatever just happened (installed, updated, or removed), the
            # package can't still be "upgradable" — an actual rescan will
            # confirm that properly, but clearing it now avoids it still
            # showing up in Updates for the few minutes until the next one.
            self.system_upgradable.setdefault(backend_name, set()).discard(pkg_id)
            if action == "remove":
                bucket.pop(pkg_id, None)
            else:
                # Good enough until the next full rescan: the tile that
                # triggered this already carries whatever name/icon the
                # search (or a previous scan) found for it.
                bucket[pkg_id] = {"version": app.get("version", ""),
                                  "name": app.get("name", pkg_id),
                                  "icon_data": app.get("icon_data")}
            self.refresh_views()
            self._toast(f"{msg}: {app['name']}")
            if on_done:
                on_done()

        worker.done.connect(finished)
        self._workers.add(worker)
        worker.start()
        self._toast(toast)

    def _on_system_package_search(self, query):
        backend_name = CATEGORY_TO_BACKEND.get(self.store_sidebar.current_category())
        if not backend_name:
            return
        worker = PackageSearchWorker([backend_name], query)
        worker.loaded.connect(self._on_package_search_loaded)
        worker.failed.connect(self._on_package_search_failed)
        worker.finished.connect(lambda: self._workers.discard(worker))
        self._workers.add(worker)
        worker.start()
        self._toast(f"Searching {BACKENDS[backend_name].label}…")

    def _on_package_search_loaded(self, backend_name, results):
        self._system_search_results[backend_name] = results
        self.rebuild_store()
        self._toast(f"Found {len(results)} {BACKENDS[backend_name].label} package(s)")

    def _on_package_search_failed(self, backend_name, message):
        QMessageBox.warning(self, f"{BACKENDS[backend_name].label} search failed", message)

    # -- desktop launchers (git apps only) --------------------------------- #
    def _launcher_path(self, app):
        safe = app["key"].replace("/", "-").replace(" ", "_")
        return APPLICATIONS_DIR / f"codemaster-{safe}.desktop"

    def _launcher_icon_path(self, app):
        safe = app["key"].replace("/", "__").replace(" ", "_")
        return LAUNCHER_ICON_DIR / f"{safe}.png"

    @staticmethod
    def _update_desktop_db():
        try:
            subprocess.run(["update-desktop-database", str(APPLICATIONS_DIR)],
                           capture_output=True)
        except Exception:
            pass  # not fatal — the launcher still works without the cache

    def create_launcher(self, app):
        app = self._resolve(app)
        cwd = self._repo_root(app) / app.get("subdir", ".")
        run = app.get("run") or (f"python3 {app.get('entrypoint')}"
                                 if app.get("entrypoint") else "")
        if not run:
            QMessageBox.warning(self, "Launcher",
                                "No run command defined for this app.")
            return
        if not cwd.exists():
            QMessageBox.warning(self, "Launcher",
                                f"App folder not found:\n{cwd}\n"
                                "Install the app first.")
            return
        if not self._confirm_run(app, run):
            return
        cwd = os.path.normpath(str(cwd))

        # Render the app's icon to a stable PNG the .desktop file can point at.
        LAUNCHER_ICON_DIR.mkdir(parents=True, exist_ok=True)
        icon_png = self._launcher_icon_path(app)
        pm = pixmap_from_bytes(app.get("icon_data"), 128) \
            or placeholder_pixmap(app.get("name", "?"), 128)
        pm.save(str(icon_png))

        # Keep the Path key for desktop environments that honour it, but make
        # Exec self-contained (see desktop_exec_line) so the launcher also
        # works on the ones that don't.
        mime_types = app.get("mime_types") or []
        exec_line = desktop_exec_line(cwd, run, mime_types)
        categories = DESKTOP_CATEGORIES.get(app.get("category", ""), "Utility;")
        # Comment must be a single line per the Desktop Entry Spec.
        comment = (app.get("tagline")
                   or app.get("description", "")).replace("\n", " ")
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Version=1.0\n"
            f"Name={app.get('name', app['key'])}\n"
            f"Comment={comment}\n"
            f"Exec={exec_line}\n"
            f"Path={cwd}\n"
            f"Icon={icon_png}\n"
            "Terminal=false\n"
            f"Categories={categories}\n"
        )
        if mime_types:
            content += f"MimeType={';'.join(mime_types)};\n"
        content += (
            "StartupNotify=true\n"
            f"X-CodeMaster-Key={app['key']}\n"
        )
        try:
            APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
            path = self._launcher_path(app)
            path.write_text(content, encoding="utf-8")
            path.chmod(0o755)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Launcher", f"Could not write launcher:\n{exc}")
            return
        self._update_desktop_db()
        if mime_types:
            self._register_mime_defaults(path.name, mime_types)
            self._toast(f"Added '{app['name']}' to your application menu "
                        "and set it as the default app for its file types")
        else:
            self._toast(f"Added '{app['name']}' to your application menu")
        self.refresh_views()

    @staticmethod
    def _register_mime_defaults(desktop_id, mime_types):
        """Make this launcher the default handler for its declared MIME
        types, so double-clicking a matching file actually opens the app
        instead of just adding it to the menu."""
        if not shutil.which("xdg-mime"):
            return
        for mime in mime_types:
            try:
                subprocess.run(["xdg-mime", "default", desktop_id, mime],
                               capture_output=True)
            except Exception:
                pass  # not fatal — the launcher still works, just not as default

    def remove_launcher(self, app):
        self._launcher_path(app).unlink(missing_ok=True)
        self._launcher_icon_path(app).unlink(missing_ok=True)
        self._update_desktop_db()
        self.refresh_views()
        self._toast(f"Removed '{app['name']}' from your application menu")

    def uninstall_app(self, app):
        if app.get("backend", "git") != "git":
            confirm = QMessageBox.question(
                self, "Remove package",
                f"Remove {app['name']} ({BACKENDS[app['backend']].label})?")
            if confirm != QMessageBox.Yes:
                return
            self._run_system_worker("remove", app, f"Removing {app['name']}…")
            return
        if self.is_busy(app):
            QMessageBox.warning(
                self, "Remove app",
                f"{app.get('name', app.get('key'))} has an install or "
                "update in progress — wait for it to finish before "
                "removing it.")
            return
        confirm = QMessageBox.question(
            self, "Remove app",
            f"Remove {app['name']} from your installed apps?")
        if confirm != QMessageBox.Yes:
            return
        # Drop any desktop launcher we created for it.
        if self.has_launcher(app):
            self._launcher_path(app).unlink(missing_ok=True)
            self._launcher_icon_path(app).unlink(missing_ok=True)
            self._update_desktop_db()
        rec = self.installed.pop(app["key"], None)
        save_installed(self.installed)
        # Delete the cloned repo only if no other installed app still uses it
        if rec and rec.get("source") != "manual":
            repo = rec.get("repo")
            publisher = rec.get("publisher", self.config["username"])
            still_used = any(r.get("repo") == repo and
                             r.get("publisher", self.config["username"]) == publisher
                             for r in self.installed.values())
            repo_root = Path(rec.get("repo_root", APPS_DIR / publisher / repo))
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
        meta = read_json(meta_path, None)
        if not meta or "apps" not in meta:
            QMessageBox.warning(self, "Invalid metadata",
                                "The metadata file is missing an 'apps' list.")
            return
        repo = meta.get("repo", Path(folder).name)
        publisher = meta.get("publisher", self.config["username"])
        count = 0
        for app in meta["apps"]:
            key = f"{publisher}/{repo}/{app.get('id')}"
            self.installed[key] = {
                "name": app.get("name", app.get("id")),
                "repo": repo, "publisher": publisher,
                "category": app.get("category", "Other"),
                "version": str(app.get("version", "")),
                "subdir": app.get("subdir", "."), "run": app.get("run", ""),
                "requirements": app.get("requirements"),
                "mime_types": app.get("mime_types") or [],
                "repo_root": folder, "source": "manual",
            }
            count += 1
        if folder not in self.config["manual_paths"]:
            self.config["manual_paths"].append(folder)
            try:
                save_config(self.config)
            except OSError as exc:
                QMessageBox.warning(
                    self, "Settings",
                    f"Could not save the manual folder list:\n{exc}")
        save_installed(self.installed)
        self.refresh_views()
        self._toast(f"Registered {count} app(s) from {repo}")

    def remove_manual_folder(self, path):
        confirm = QMessageBox.question(
            self, "Remove location",
            f"Stop tracking this custom app location?\n\n{path}\n\n"
            "Apps registered from it will be removed from Installed — the "
            "folder itself is left untouched.")
        if confirm != QMessageBox.Yes:
            return
        if path in self.config.get("manual_paths", []):
            self.config["manual_paths"].remove(path)
            try:
                save_config(self.config)
            except OSError as exc:
                QMessageBox.warning(self, "Settings",
                                    f"Could not save settings:\n{exc}")
        removed = 0
        for key, rec in list(self.installed.items()):
            if rec.get("source") == "manual" and rec.get("repo_root") == path:
                if self.has_launcher({"key": key}):
                    self._launcher_path({"key": key}).unlink(missing_ok=True)
                    self._launcher_icon_path({"key": key}).unlink(missing_ok=True)
                self.installed.pop(key, None)
                removed += 1
        if removed:
            save_installed(self.installed)
            self._update_desktop_db()
        self.refresh_views()
        self._toast(f"Removed location and {removed} app(s) from Installed")

    def _refresh_manual_list(self):
        while self.manual_list_box.count():
            item = self.manual_list_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        paths = self.config.get("manual_paths", [])
        if not paths:
            self.manual_list_box.addWidget(
                QLabel("<i>No manual app folders registered yet.</i>"))
            return
        for path in paths:
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(path)
            lbl.setObjectName("AppMeta")
            lbl.setWordWrap(True)
            row_lay.addWidget(lbl, 1)
            btn = QPushButton("Remove")
            btn.setObjectName("Ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=None, p=path: self.remove_manual_folder(p))
            row_lay.addWidget(btn)
            self.manual_list_box.addWidget(row)

    def save_settings(self):
        self.config["username"] = self.username_in.text().strip() \
            or DEFAULT_USERNAME
        self.config["branch"] = self.branch_in.text().strip() or DEFAULT_BRANCH
        self.config["token"] = self.token_in.text().strip()
        try:
            save_config(self.config)
        except OSError as exc:
            # This is the one save that writes the GitHub token — refuse to
            # report success if it couldn't be written with owner-only
            # permissions, rather than silently leaving it exposed.
            QMessageBox.warning(
                self, "Settings",
                f"Could not save settings securely, so they were not "
                f"written to disk:\n{exc}")
            return
        self._toast("Settings saved")
        self.load_catalog()

    # -- self update ------------------------------------------------------ #
    def _self_catalog_entry(self):
        """Code Master's own entry in the catalog, if one was fetched.

        Matched by publisher, repo *and* app id: the codemaster repo could
        in principle publish more than one app, so the first match isn't
        necessarily Code Master itself.
        """
        return next((a for a in self.catalog
                    if a.get("publisher") == DEFAULT_USERNAME
                    and a.get("repo") == "codemaster"
                    and a.get("id") == "codemaster"), None)

    def _self_head_commit(self):
        """The commit Code Master itself is actually running from.

        Cached after the first lookup since it can't change without a
        restart.
        """
        if getattr(self, "_self_commit", None) is None:
            try:
                proc = subprocess.run(
                    ["git", "-C", str(SELF_DIR), "rev-parse", "HEAD"],
                    capture_output=True, text=True)
                self._self_commit = (
                    proc.stdout.strip() if proc.returncode == 0 else "")
            except OSError:
                self._self_commit = ""
        return self._self_commit

    def _self_update_status(self):
        """(has_update, detail) for Code Master's own catalog entry.

        Mirrors has_update(): a 'sync' entry (codemaster's normal case) is
        compared by commit SHA rather than the metadata version string,
        since a publisher can forget to bump it on every commit — see
        https://github.com/jan-tdy/codemaster/issues/9. A 'release' entry
        falls back to the version-string comparison.
        """
        entry = self._self_catalog_entry()
        if not entry:
            return False, ""
        if entry.get("update_method", "sync") == "release":
            latest = self.effective_version(entry)
            if not latest:
                return False, ""
            if latest.startswith("v"):
                latest = latest[1:]
            if not latest or latest == APP_VERSION:
                return False, ""
            return True, f"v{latest} (you have v{APP_VERSION})"
        latest_commit = entry.get("latest_commit")
        current = self._self_head_commit()
        if not latest_commit or not current or latest_commit == current:
            return False, ""
        return True, f"commit {latest_commit[:7]} (you have {current[:7]})"

    def _refresh_self_update_note(self):
        if not hasattr(self, "self_update_note"):
            return
        is_git = (SELF_DIR / ".git").exists()
        if not is_git:
            self.self_update_note.setText(
                f"Running from {SELF_DIR} (not a git checkout). Re-clone or "
                "git pull manually to update.")
            self.self_update_btn.setEnabled(False)
            return
        self.self_update_btn.setEnabled(True)
        has_update, detail = self._self_update_status()
        if has_update:
            self.self_update_note.setText(
                f"Update available: {detail}. Updates Code Master in place "
                "via git; restart to apply.")
        else:
            self.self_update_note.setText(
                "Pulls the latest Code Master from git; restart to apply.")

    def self_update(self):
        if not (SELF_DIR / ".git").exists():
            QMessageBox.information(
                self, "Update Code Master",
                "Code Master isn't running from a git checkout, so it can't "
                f"update itself automatically.\n\nLocation:\n{SELF_DIR}")
            return
        busy_key = str(SELF_DIR)
        if busy_key in self._busy:
            self._toast("Code Master is already updating…")
            return
        self._busy.add(busy_key)
        entry = self._self_catalog_entry()
        branch = entry.get("branch") if entry else None
        worker = GitWorker("update", DEFAULT_USERNAME, "codemaster",
                           SELF_DIR, branch, token=self.config["token"])
        self.self_update_btn.setEnabled(False)
        self.self_update_btn.setText("Updating…")

        def finished(ok, msg, _commit=""):
            self._workers.discard(worker)
            self._busy.discard(busy_key)
            self.self_update_btn.setText("Update Code Master")
            self.self_update_btn.setEnabled(True)
            if not ok:
                QMessageBox.warning(self, "Update Code Master",
                                    f"Update failed:\n{msg}")
                return
            QMessageBox.information(
                self, "Update Code Master",
                "Code Master was updated. Restart it to apply the changes.")
            self._refresh_self_update_note()

        worker.done.connect(finished)
        self._workers.add(worker)
        worker.start()
        self._toast("Updating Code Master…")

    # -- misc ------------------------------------------------------------- #
    def _toast(self, message):
        self.statusBar().showMessage(message, 4000)

    def closeEvent(self, event):
        """Don't let Qt tear down a live GitWorker/CatalogLoader mid-
        operation — killing a thread mid `git clone`/`pull` (or mid pip
        install) can leave a clone half-written, and Qt itself warns
        "QThread: Destroyed while thread is still running" when a QThread
        is deleted while running. Give the user the choice to wait for
        in-flight work before the window actually closes."""
        running = [w for w in self._workers if w.isRunning()]
        if not running:
            super().closeEvent(event)
            return
        box = QMessageBox(self)
        box.setWindowTitle("Operations in progress")
        box.setText(
            f"{len(running)} background operation(s) (install/update/"
            "dependency/catalog scan) are still running. Closing now "
            "could interrupt a git clone or pull mid-write.")
        wait_btn = box.addButton("Wait, then close", QMessageBox.AcceptRole)
        close_btn = box.addButton("Close now", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(wait_btn)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is close_btn:
            event.accept()
            return
        if clicked is not wait_btn:
            event.ignore()
            return
        for worker in running:
            worker.wait(10000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # Without these, a window opened by plain "python3 jadiv_code_master.py"
    # (as the installed .desktop launcher's Exec= does) carries no icon
    # hint at all: Qt never sets one on its own, and on Wayland/GNOME the
    # dock/taskbar matches a running window to its icon via the XDG desktop
    # file id (setDesktopFileName, matching the installed codemaster.desktop)
    # rather than X11 WM_CLASS, so without it the dock falls back to a
    # generic placeholder icon. setWindowIcon covers X11 desktops and acts
    # as a fallback wherever the desktop-file match doesn't apply.
    app.setDesktopFileName("codemaster")
    icon_path = SELF_DIR / "assets" / "codemaster.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = CodeMaster()
    window.show()
    sys.exit(app.exec_())
