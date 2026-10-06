"""Store UI: reflowing tile grid, category sidebar and the app details page.

``controller`` throughout this module is the CodeMaster main window: these
widgets never touch install state, config or workers directly, they only
call back into it (``controller.install_app(app)``, ``controller.is_installed(app)``,
...) so all of that stays in one place.
"""

import requests
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QTextEdit,
    QVBoxLayout, QWidget,
)

from .constants import TILE_ICON_SIZE
from .icons import pixmap_from_bytes, placeholder_pixmap

TILE_WIDTH = 220
TILE_SPACING = 16


def format_version(version):
    """"1.5.0" -> "v1.5.0" for display — and "v1.5.0" (a release tag
    already carrying the prefix, which is the usual GitHub tagging
    convention) -> "v1.5.0", not "vv1.5.0". Strips at most one leading
    v/V before adding the one we display, so it's a no-op either way."""
    if not version:
        return ""
    version = str(version).strip()
    if not version:
        return ""
    if version[0] in "vV":
        version = version[1:]
    return f"v{version}"


# --------------------------------------------------------------------------- #
#  Reflowing tile grid
# --------------------------------------------------------------------------- #
class TileGrid(QScrollArea):
    """A grid of tiles that re-wraps its columns as the window is resized,
    instead of the old fixed-width single-column list of rows."""

    def __init__(self, tile_width=TILE_WIDTH, spacing=TILE_SPACING):
        super().__init__()
        self.setWidgetResizable(True)
        self.setObjectName("Page")
        self._tile_width = tile_width
        self._spacing = spacing
        self._tiles = []
        self._holder = QWidget()
        self._grid = QGridLayout(self._holder)
        self._grid.setSpacing(spacing)
        self._grid.setContentsMargins(18, 18, 18, 18)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._placeholder = QLabel()
        self._placeholder.setObjectName("Placeholder")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self.setWidget(self._holder)

    def set_placeholder(self, text):
        self.set_tiles([])
        self._grid.addWidget(self._placeholder, 0, 0)
        self._placeholder.setText(text)
        self._placeholder.show()

    def set_tiles(self, tiles):
        for i in reversed(range(self._grid.count())):
            item = self._grid.takeAt(i)
            w = item.widget()
            if w and w is not self._placeholder:
                w.deleteLater()
        self._placeholder.hide()
        self._tiles = tiles
        self._reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self):
        for i in reversed(range(self._grid.count())):
            self._grid.takeAt(i)
        if not self._tiles:
            return
        width = max(self.viewport().width(), self._tile_width)
        cols = max(1, (width + self._spacing) // (self._tile_width + self._spacing))
        for idx, tile in enumerate(self._tiles):
            row, col = divmod(idx, cols)
            self._grid.addWidget(tile, row, col)


# --------------------------------------------------------------------------- #
#  App tile
# --------------------------------------------------------------------------- #
class AppTile(QFrame):
    """One app's card in the Store/Installed/Updates grid: icon, name, a
    couple of meta badges, and a single button that installs an
    uninstalled app or opens its Details page."""

    def __init__(self, app, controller):
        super().__init__()
        self.app = app
        self.controller = controller
        self.setObjectName("AppTile")
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedWidth(TILE_WIDTH)
        self.setCursor(Qt.PointingHandCursor)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 14)
        lay.setSpacing(6)

        icon = QLabel()
        pm = pixmap_from_bytes(self.app.get("icon_data"), TILE_ICON_SIZE) \
            or placeholder_pixmap(self.app.get("name", "?"), TILE_ICON_SIZE)
        icon.setPixmap(pm)
        icon.setFixedSize(TILE_ICON_SIZE, TILE_ICON_SIZE)
        icon.setAlignment(Qt.AlignCenter)
        icon_row = QHBoxLayout()
        icon_row.addStretch()
        icon_row.addWidget(icon)
        icon_row.addStretch()
        lay.addLayout(icon_row)

        title = QLabel(self.app.get("name", "?"))
        title.setObjectName("TileTitle")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        lay.addWidget(title)

        meta = QLabel(self._meta_text())
        meta.setObjectName("TileMeta")
        meta.setAlignment(Qt.AlignCenter)
        meta.setWordWrap(True)
        lay.addWidget(meta)

        badges = self._badge_text()
        if badges:
            badge_lbl = QLabel(badges)
            badge_lbl.setObjectName("TileBadges")
            badge_lbl.setAlignment(Qt.AlignCenter)
            badge_lbl.setWordWrap(True)
            lay.addWidget(badge_lbl)

        lay.addStretch()

        installed = self.controller.is_installed(self.app)
        btn = QPushButton("Details" if installed else "Install")
        btn.setObjectName("Primary" if not installed else "Ghost")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self._on_button)
        lay.addWidget(btn)

    def _meta_text(self):
        bits = [self.app.get("category", "")]
        version = self.controller.effective_version(self.app)
        if version:
            bits.append(format_version(version))
        return "  ·  ".join(b for b in bits if b)

    def _badge_text(self):
        badges = []
        if self.app.get("private"):
            badges.append("🔒 private")
        if self.app.get("catalog_source") == "community":
            badges.append("community")
        if self.app.get("backend") == "git" and not self.app.get("maintained", True):
            badges.append("unmaintained")
        if self.controller.has_update(self.app):
            badges.append("⟳ update available")
        return "  ·  ".join(badges)

    def _on_button(self):
        if self.controller.is_installed(self.app):
            self.controller.open_details(self.app)
        else:
            self.controller.install_app(self.app)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.controller.open_details(self.app)
        super().mousePressEvent(event)


# --------------------------------------------------------------------------- #
#  Category sidebar
# --------------------------------------------------------------------------- #
class CategorySidebar(QListWidget):
    """The Store's left-hand category list. "All apps" plus every distinct
    category currently in the catalog (including the apt/snap/flatpak
    pseudo-categories, so those backends are reachable even before any
    search has been run)."""

    categoryChanged = pyqtSignal(str)  # "" means "All apps"

    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(200)
        self.currentItemChanged.connect(self._emit_change)

    def set_categories(self, categories):
        # Rebuilding the list always creates fresh QListWidgetItems, so
        # Qt sees a "new" current item even when the selected category's
        # text is unchanged — signals stay blocked through the restore too,
        # or that spurious currentItemChanged would re-trigger
        # categoryChanged, which re-triggers the caller's rebuild, forever.
        previous = self.current_category()
        self.blockSignals(True)
        self.clear()
        all_item = QListWidgetItem("All apps")
        all_item.setData(Qt.UserRole, "")
        self.addItem(all_item)
        for cat in categories:
            item = QListWidgetItem(cat)
            item.setData(Qt.UserRole, cat)
            self.addItem(item)
        self._restore_selection(previous)
        self.blockSignals(False)

    def _restore_selection(self, category):
        for i in range(self.count()):
            if self.item(i).data(Qt.UserRole) == category:
                self.setCurrentRow(i)
                return
        self.setCurrentRow(0)

    def current_category(self):
        item = self.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def _emit_change(self, current, _previous):
        self.categoryChanged.emit(current.data(Qt.UserRole) if current else "")


# --------------------------------------------------------------------------- #
#  Source / publisher filter bar
# --------------------------------------------------------------------------- #
class FilterBar(QWidget):
    """Narrows the Store grid by where an app came from (the jan-tdy scan,
    the community catalog, or one of the system-package backends) and, for
    git apps, by which GitHub account actually publishes it — independent
    of the category sidebar."""

    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 0)
        lay.setSpacing(8)

        lay.addWidget(QLabel("Source:"))
        self.source_box = QComboBox()
        self.source_box.currentIndexChanged.connect(lambda _: self.changed.emit())
        lay.addWidget(self.source_box)

        lay.addWidget(QLabel("Publisher:"))
        self.publisher_box = QComboBox()
        self.publisher_box.currentIndexChanged.connect(lambda _: self.changed.emit())
        lay.addWidget(self.publisher_box)

        lay.addStretch()
        self.set_sources([])
        self.set_publishers([])

    def set_sources(self, sources):
        """``sources``: [(label, value), ...] besides the fixed "All sources".

        Signals stay blocked through the restore too — clear()+repopulate
        always perturbs currentIndex, so restoring it after unblocking
        would re-emit ``changed`` and re-trigger the caller's rebuild."""
        current = self.current_source()
        self.source_box.blockSignals(True)
        self.source_box.clear()
        self.source_box.addItem("All sources", "")
        for label, value in sources:
            self.source_box.addItem(label, value)
        self._restore(self.source_box, current)
        self.source_box.blockSignals(False)

    def set_publishers(self, publishers):
        current = self.current_publisher()
        self.publisher_box.blockSignals(True)
        self.publisher_box.clear()
        self.publisher_box.addItem("All publishers", "")
        for publisher in publishers:
            self.publisher_box.addItem(publisher, publisher)
        self._restore(self.publisher_box, current)
        self.publisher_box.blockSignals(False)

    @staticmethod
    def _restore(box, value):
        idx = box.findData(value)
        box.setCurrentIndex(idx if idx >= 0 else 0)

    def current_source(self):
        return self.source_box.currentData() or ""

    def current_publisher(self):
        return self.publisher_box.currentData() or ""


# --------------------------------------------------------------------------- #
#  System-package search bar (apt/snap/flatpak)
# --------------------------------------------------------------------------- #
class SystemPackageSearchBar(QWidget):
    searchRequested = pyqtSignal(str)

    def __init__(self, backend_label):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 0)
        lay.setSpacing(6)
        self.label = QLabel()
        self.label.setObjectName("AppMeta")
        self.label.setWordWrap(True)
        lay.addWidget(self.label)

        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setObjectName("Search")
        self.query.returnPressed.connect(self._emit_search)
        row.addWidget(self.query, 1)
        search_btn = QPushButton("Search")
        search_btn.setObjectName("Primary")
        search_btn.setCursor(Qt.PointingHandCursor)
        search_btn.clicked.connect(self._emit_search)
        row.addWidget(search_btn)
        lay.addLayout(row)

        self.set_backend_label(backend_label)

    def _emit_search(self):
        query = self.query.text().strip()
        if query:
            self.searchRequested.emit(query)

    def set_backend_label(self, label):
        self.label.setText(
            f"Search {label} packages — the full catalog is too large to "
            f"list here, so type a name or keyword and press Search:")


# --------------------------------------------------------------------------- #
#  README loading (git-backed apps only)
# --------------------------------------------------------------------------- #
class ReadmeLoader(QThread):
    """Fetch an app's README from its repo, off the UI thread."""
    loaded = pyqtSignal(str, str)  # key, markdown text ("" if none found)

    CANDIDATES = ("README.md", "readme.md", "Readme.md", "README")

    def __init__(self, key, publisher, repo, branch, subdir="."):
        super().__init__()
        self.key = key
        self.publisher = publisher
        self.repo = repo
        self.branch = branch
        self.subdir = subdir

    def _try(self, path):
        url = (f"https://raw.githubusercontent.com/{self.publisher}/"
               f"{self.repo}/{self.branch}/{path}")
        try:
            resp = requests.get(url, timeout=15)
        except requests.RequestException:
            return None
        return resp.text if resp.status_code == 200 else None

    def run(self):
        prefix = "" if self.subdir in (".", "", None) else \
            self.subdir.strip("/") + "/"
        for candidate in self.CANDIDATES:
            text = self._try(prefix + candidate) if prefix else None
            if text is not None:
                self.loaded.emit(self.key, text)
                return
        for candidate in self.CANDIDATES:
            text = self._try(candidate)
            if text is not None:
                self.loaded.emit(self.key, text)
                return
        self.loaded.emit(self.key, "")


# --------------------------------------------------------------------------- #
#  Details page
# --------------------------------------------------------------------------- #
class DetailsPage(QScrollArea):
    """The app details view: README (git apps) or description (system
    packages), plus every action button that used to crowd the card
    itself — Install/Update/Open, Add to menu, Install deps, Remove."""

    backRequested = pyqtSignal()

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.app = None
        self.setWidgetResizable(True)
        self.setObjectName("Page")
        self._readme_cache = {}
        self._readme_loader = None
        self.setWidget(QWidget())  # placeholder; show_app() builds the real one

    def show_app(self, app):
        self.app = app
        # A fresh holder widget each time, rather than clearing the old
        # QVBoxLayout in place: items added with addLayout() (the header
        # and action rows below) have no widget() of their own, so a
        # takeAt()-based clear never deletes the buttons/labels nested
        # inside them — they'd stay on screen, overlapping the new ones on
        # every app switch. QScrollArea.setWidget() deletes the previous
        # widget (and that whole child tree) for us.
        holder = QWidget()
        self._lay = QVBoxLayout(holder)
        self._lay.setContentsMargins(24, 18, 24, 24)
        self._lay.setSpacing(12)

        back = QPushButton("←  Back")
        back.setObjectName("Ghost")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self.backRequested.emit)
        self._lay.addWidget(back, alignment=Qt.AlignLeft)

        header = QHBoxLayout()
        icon = QLabel()
        pm = pixmap_from_bytes(app.get("icon_data"), 96) \
            or placeholder_pixmap(app.get("name", "?"), 96)
        icon.setPixmap(pm)
        icon.setFixedSize(96, 96)
        header.addWidget(icon)

        text = QVBoxLayout()
        title = QLabel(app.get("name", "?"))
        title.setObjectName("AppTitle")
        text.addWidget(title)
        meta = QLabel(self._meta_line())
        meta.setObjectName("AppMeta")
        meta.setWordWrap(True)
        text.addWidget(meta)
        tagline = app.get("tagline") or app.get("description", "")
        if tagline:
            tagline_lbl = QLabel(tagline)
            tagline_lbl.setObjectName("AppDesc")
            tagline_lbl.setWordWrap(True)
            text.addWidget(tagline_lbl)
        header.addLayout(text, 1)
        self._lay.addLayout(header)

        actions = QHBoxLayout()
        for label, kind, slot in self._actions():
            btn = QPushButton(label)
            btn.setObjectName(kind)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=None, s=slot: s(self.app))
            actions.addWidget(btn)
        actions.addStretch()
        self._lay.addLayout(actions)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        self._lay.addWidget(line)

        if app.get("backend") == "git":
            self._lay.addWidget(QLabel("<b>README</b>"))
            self.readme_view = QTextEdit()
            self.readme_view.setReadOnly(True)
            self.readme_view.setMinimumHeight(320)
            self._lay.addWidget(self.readme_view)
            self._load_readme(app)
        else:
            desc = app.get("description") or "No further description available."
            desc_lbl = QLabel(desc)
            desc_lbl.setWordWrap(True)
            self._lay.addWidget(desc_lbl)
            self._lay.addStretch()

        self.setWidget(holder)

    def _meta_line(self):
        app = self.app
        bits = [app.get("category", "")]
        version = self.controller.effective_version(app)
        if version:
            bits.append(format_version(version))
        if app.get("backend") == "git":
            bits.append(f"{app.get('publisher')}/{app.get('repo')}")
            if app.get("private"):
                bits.append("🔒 private")
            if app.get("catalog_source") == "community":
                bits.append("community submission")
            bits.append("↻ release" if app.get("update_method") == "release"
                        else "↻ latest code")
            if not app.get("maintained", True):
                bits.append("unmaintained")
        else:
            bits.append(app.get("author", ""))
        return "  ·  ".join(b for b in bits if b)

    def _actions(self):
        """(label, button-style-id, controller-method) for every button
        this app's state calls for."""
        app = self.app
        c = self.controller
        installed = c.is_installed(app)
        actions = []
        if not installed:
            actions.append(("Install", "Primary", c.install_app))
            if app.get("homepage"):
                actions.append(("Open homepage", "Ghost", c.open_homepage))
            return actions
        # Flatpak updates aren't probed (FlatpakBackend.upgradable() is
        # always empty — see its docstring), so has_update() is always
        # False for it; its Update button is offered unconditionally
        # instead of never, trusting flatpak itself to no-op if there's
        # nothing to do.
        show_update = c.has_update(app) or app.get("backend") == "flatpak"
        if show_update:
            actions.append(("Update", "Primary", c.update_app))
        actions.append(("Open", "Primary" if not show_update else "Ghost",
                        c.launch_app))
        if app.get("backend") == "git":
            if c.has_launcher(app):
                actions.append(("Remove launcher", "Ghost", c.remove_launcher))
            else:
                actions.append(("Add to menu", "Ghost", c.create_launcher))
            if app.get("requirements"):
                actions.append(("Install deps", "Ghost", c.install_deps))
        actions.append(("Remove", "Ghost", c.uninstall_app))
        if app.get("homepage"):
            actions.append(("Homepage", "Ghost", c.open_homepage))
        return actions

    def _load_readme(self, app):
        cached = self._readme_cache.get(app["key"])
        if cached is not None:
            self.readme_view.setMarkdown(cached or "*No README found.*")
            return
        self.readme_view.setMarkdown("*Loading README…*")
        if self._readme_loader:
            self._readme_loader.loaded.disconnect()
        loader = ReadmeLoader(app["key"], app.get("publisher"), app.get("repo"),
                              app.get("branch") or "main", app.get("subdir", "."))
        loader.loaded.connect(self._on_readme_loaded)
        self._readme_loader = loader
        loader.start()

    def _on_readme_loaded(self, key, text):
        self._readme_cache[key] = text
        if self.app and self.app.get("key") == key and hasattr(self, "readme_view"):
            self.readme_view.setMarkdown(text or "*No README found.*")
