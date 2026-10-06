import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

# Offscreen before any Qt import — several tests below build real widgets
# (CategorySidebar, FilterBar, AppTile) to lock in signal-wiring regressions,
# and this lets that work headless in CI as it already does via
# QT_QPA_PLATFORM in the workflow.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from codemaster import desktop_entry, persistence, system_packages  # noqa: E402
from codemaster.catalog_loader import CatalogLoader  # noqa: E402
from codemaster.git_worker import GitWorker, apt_package_name, apt_requirement_satisfied  # noqa: E402
from codemaster.main_window import CodeMaster, app_source  # noqa: E402
from codemaster.constants import APP_VERSION, xdg_data_home  # noqa: E402
from codemaster.widgets import (  # noqa: E402
    AppTile, CategorySidebar, DetailsPage, FilterBar, format_version,
)


def _bare_store():
    """A CodeMaster instance with no Qt widgets constructed.

    Bypassing __init__ (and therefore QMainWindow.__init__) keeps most of
    these tests free of any real Qt platform/display dependency, since the
    pure logic under test only touches plain attributes. Attributes a real
    __init__ always sets (even when a given test's own scenario doesn't
    care about them) get a harmless default here, so a test that never
    touches e.g. system packages doesn't also have to stub them out.
    """
    store = CodeMaster.__new__(CodeMaster)
    store.config = {"username": "jan-tdy"}
    store.installed = {}
    store.catalog = []
    store.system_installed = {"apt": {}, "snap": {}, "flatpak": {}}
    store.system_upgradable = {"apt": set(), "snap": set(), "flatpak": set()}
    return store


# -- desktop entry helpers -------------------------------------------------- #
def test_desktop_exec_quote_escapes_shell_metacharacters():
    raw = 'py "$HOME" `id` \\ end'
    assert desktop_entry.desktop_exec_quote(raw) == 'py \\"\\$HOME\\" \\`id\\` \\\\ end'


def test_desktop_exec_line_wraps_cd_and_run():
    line = desktop_entry.desktop_exec_line("/home/user/App", "python3 app.py")
    assert line == 'sh -c "cd /home/user/App && python3 app.py"'


def test_desktop_exec_line_forwards_opened_files_when_app_has_mime_types():
    line = desktop_entry.desktop_exec_line("/home/user/App", "python3 app.py",
                                           mime_types=["image/png"])
    assert line == 'sh -c "cd /home/user/App && python3 app.py \\"\\$@\\"" _ %F'


# -- apt dependency mapping --------------------------------------------------- #
def test_apt_package_name_strips_version_constraint():
    assert apt_package_name("PyQt5>=5.15,<6") == "python3-pyqt5"


def test_apt_package_name_normalizes_underscores():
    assert apt_package_name("some_package==1.0") == "python3-some-package"


def test_apt_requirement_satisfied_is_true_for_bare_requirement():
    assert apt_requirement_satisfied("requests") is True


def test_apt_requirement_satisfied_checks_installed_version():
    installed = requests.__version__
    assert apt_requirement_satisfied(f"requests=={installed}") is True
    assert apt_requirement_satisfied("requests==0.0.1") is False


def test_apt_requirement_satisfied_false_for_unknown_package():
    assert apt_requirement_satisfied("not-a-real-package>=1.0") is False


# -- xdg data home ----------------------------------------------------------- #
def test_xdg_data_home_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert xdg_data_home() == Path.home() / ".local" / "share"


def test_xdg_data_home_ignores_empty_env(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "")
    assert xdg_data_home() == Path.home() / ".local" / "share"


def test_xdg_data_home_uses_env_when_set(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/data")
    assert xdg_data_home() == Path("/custom/data")


# -- installed-key migration (pre-v2 "repo/id" -> "publisher/repo/id") ------- #
def test_migrate_installed_keys_prefixes_default_publisher():
    installed = {"repo/app": {"name": "App", "repo": "repo"}}
    changed = persistence.migrate_installed_keys(installed, "jan-tdy")
    assert changed is True
    assert installed == {"jan-tdy/repo/app": {"name": "App", "repo": "repo",
                                               "publisher": "jan-tdy"}}


def test_migrate_installed_keys_leaves_v2_keys_untouched():
    installed = {"jan-tdy/repo/app": {"name": "App", "publisher": "jan-tdy"}}
    changed = persistence.migrate_installed_keys(installed, "jan-tdy")
    assert changed is False
    assert installed == {"jan-tdy/repo/app": {"name": "App", "publisher": "jan-tdy"}}


def test_migrate_installed_keys_respects_stored_publisher():
    installed = {"repo/app": {"name": "App", "publisher": "someone-else"}}
    persistence.migrate_installed_keys(installed, "jan-tdy")
    assert "someone-else/repo/app" in installed


# -- effective_version -------------------------------------------------------- #
def test_effective_version_prefers_release_tag():
    app = {"backend": "git", "update_method": "release", "release_tag": "v2.0",
          "version": "1.0"}
    assert CodeMaster.effective_version(app) == "v2.0"


def test_effective_version_falls_back_to_version_string():
    app = {"backend": "git", "update_method": "sync", "version": "1.2.3"}
    assert CodeMaster.effective_version(app) == "1.2.3"


def test_effective_version_release_without_tag_uses_version():
    app = {"backend": "git", "update_method": "release", "release_tag": None,
          "version": "1.0"}
    assert CodeMaster.effective_version(app) == "1.0"


def test_effective_version_system_package_uses_version_field_directly():
    app = {"backend": "apt", "version": "2.10.34"}
    assert CodeMaster.effective_version(app) == "2.10.34"


# -- app_source ---------------------------------------------------------------- #
def test_app_source_git_defaults_to_jan_tdy():
    assert app_source({"backend": "git"}) == "jan-tdy"


def test_app_source_git_reports_community():
    assert app_source({"backend": "git", "catalog_source": "community"}) == "community"


def test_app_source_system_package_is_its_backend():
    assert app_source({"backend": "flatpak"}) == "flatpak"


# -- has_update ---------------------------------------------------------------- #
def test_has_update_release_compares_version_string():
    store = _bare_store()
    store.installed = {"jan-tdy/repo/app": {"version": "1.0"}}
    store.catalog = [{"key": "jan-tdy/repo/app", "backend": "git",
                      "update_method": "release",
                      "release_tag": "1.1", "version": "1.0"}]
    assert store.has_update({"key": "jan-tdy/repo/app", "backend": "git"}) is True


def test_has_update_release_same_tag_is_up_to_date():
    store = _bare_store()
    store.installed = {"jan-tdy/repo/app": {"version": "1.1"}}
    store.catalog = [{"key": "jan-tdy/repo/app", "backend": "git",
                      "update_method": "release",
                      "release_tag": "1.1", "version": "1.0"}]
    assert store.has_update({"key": "jan-tdy/repo/app", "backend": "git"}) is False


def test_has_update_sync_compares_commit_sha_not_version_string():
    # Regression test for https://github.com/jan-tdy/codemaster/issues/9 —
    # a publisher who forgets to bump `version` must still see the update
    # once new commits land on the tracked branch.
    store = _bare_store()
    store.installed = {"jan-tdy/repo/app": {"version": "1.0", "commit": "aaa111"}}
    store.catalog = [{"key": "jan-tdy/repo/app", "backend": "git",
                      "update_method": "sync",
                      "version": "1.0", "latest_commit": "bbb222"}]
    assert store.has_update({"key": "jan-tdy/repo/app", "backend": "git"}) is True


def test_has_update_sync_same_commit_is_up_to_date():
    store = _bare_store()
    store.installed = {"jan-tdy/repo/app": {"version": "1.0", "commit": "aaa111"}}
    store.catalog = [{"key": "jan-tdy/repo/app", "backend": "git",
                      "update_method": "sync",
                      "version": "1.0", "latest_commit": "aaa111"}]
    assert store.has_update({"key": "jan-tdy/repo/app", "backend": "git"}) is False


def test_has_update_returns_false_when_not_installed():
    store = _bare_store()
    store.installed = {}
    store.catalog = [{"key": "jan-tdy/repo/app", "backend": "git",
                      "update_method": "sync", "latest_commit": "aaa111"}]
    assert store.has_update({"key": "jan-tdy/repo/app", "backend": "git"}) is False


def test_has_update_returns_false_when_not_in_catalog():
    store = _bare_store()
    store.installed = {"jan-tdy/repo/app": {"version": "1.0", "commit": "aaa111"}}
    store.catalog = []
    assert store.has_update({"key": "jan-tdy/repo/app", "backend": "git"}) is False


def test_has_update_apt_and_snap_check_the_upgradable_set():
    store = _bare_store()
    store.installed = {}
    store.catalog = []
    store.system_upgradable = {"apt": {"gimp"}, "snap": set(), "flatpak": set()}
    assert store.has_update({"backend": "apt", "pkg_id": "gimp"}) is True
    assert store.has_update({"backend": "apt", "pkg_id": "vlc"}) is False
    assert store.has_update({"backend": "snap", "pkg_id": "gimp"}) is False


def test_flatpak_backend_upgradable_is_always_empty():
    # Flatpak has no equally simple, version-stable "list pending updates"
    # command to build on with confidence — has_update()'s dispatch for
    # non-git backends is generic (membership in system_upgradable), so
    # this guarantee lives here: an installed flatpak app's Update button
    # stays always-available (see DetailsPage._actions) because this set
    # is always empty, not because has_update() special-cases it.
    assert system_packages.FlatpakBackend.upgradable() == set()


# -- is_installed ---------------------------------------------------------------- #
def test_is_installed_git_checks_installed_dict():
    store = _bare_store()
    store.installed = {"jan-tdy/repo/app": {}}
    assert store.is_installed({"key": "jan-tdy/repo/app", "backend": "git"}) is True
    assert store.is_installed({"key": "other/repo/app", "backend": "git"}) is False


def test_is_installed_system_package_checks_system_installed():
    store = _bare_store()
    store.system_installed = {"apt": {"gimp": "2.10"}}
    assert store.is_installed({"backend": "apt", "pkg_id": "gimp"}) is True
    assert store.is_installed({"backend": "apt", "pkg_id": "inkscape"}) is False


# -- _self_catalog_entry ---------------------------------------------------------- #
def test_self_catalog_entry_ignores_other_apps_from_same_repo():
    # The codemaster repo could in principle publish more than one app;
    # the self-update entry must be picked by id, not just by repo.
    store = _bare_store()
    store.catalog = [{"publisher": "jan-tdy", "repo": "codemaster",
                      "id": "other-tool", "latest_commit": "zzz999"},
                      {"publisher": "jan-tdy", "repo": "codemaster",
                      "id": "codemaster", "latest_commit": "bbb222"}]
    assert store._self_catalog_entry()["latest_commit"] == "bbb222"


def test_self_catalog_entry_ignores_a_same_named_repo_from_another_publisher():
    # The community catalog can list a repo named "codemaster" published by
    # someone else; that must never be mistaken for Code Master itself.
    store = _bare_store()
    store.catalog = [{"publisher": "someone-else", "repo": "codemaster",
                      "id": "codemaster", "latest_commit": "zzz999"}]
    assert store._self_catalog_entry() is None


# -- _self_update_status --------------------------------------------------------- #
def _self_entry(**overrides):
    entry = {"publisher": "jan-tdy", "repo": "codemaster", "id": "codemaster",
             "backend": "git", "update_method": "sync", "version": APP_VERSION}
    entry.update(overrides)
    return entry


def test_self_update_status_sync_compares_commit_not_version():
    # Regression test for https://github.com/jan-tdy/codemaster/issues/16 —
    # codemaster's own catalog entry is a 'sync' app, so a commit-only fix
    # must still be flagged even when `version` in the metadata is unchanged.
    store = _bare_store()
    store._self_commit = "aaa111"
    store.catalog = [_self_entry(latest_commit="bbb222")]
    has_update, detail = store._self_update_status()
    assert has_update is True
    assert "bbb222"[:7] in detail


def test_self_update_status_sync_same_commit_is_up_to_date():
    store = _bare_store()
    store._self_commit = "aaa111"
    store.catalog = [_self_entry(latest_commit="aaa111")]
    assert store._self_update_status() == (False, "")


def test_self_update_status_release_compares_version_string():
    store = _bare_store()
    store._self_commit = "aaa111"
    store.catalog = [_self_entry(update_method="release", release_tag="9.9.9")]
    has_update, detail = store._self_update_status()
    assert has_update is True
    assert "9.9.9" in detail


def test_self_update_status_release_same_version_is_up_to_date():
    store = _bare_store()
    store._self_commit = "aaa111"
    store.catalog = [_self_entry(update_method="release", release_tag=APP_VERSION,
                                 version="0.0.0")]
    assert store._self_update_status() == (False, "")


def test_self_update_status_release_normalizes_v_prefixed_tag():
    # A "v"-prefixed release tag must not double up into "vv0.3.0", nor
    # compare unequal to a bare APP_VERSION that names the same release.
    store = _bare_store()
    store._self_commit = "aaa111"
    store.catalog = [_self_entry(update_method="release",
                                 release_tag=f"v{APP_VERSION}", version="0.0.0")]
    assert store._self_update_status() == (False, "")


def test_self_update_status_returns_false_when_not_in_catalog():
    store = _bare_store()
    store._self_commit = "aaa111"
    store.catalog = []
    assert store._self_update_status() == (False, "")


def test_self_update_status_returns_false_without_own_commit():
    # If Code Master's own HEAD commit can't be resolved, don't claim an
    # update against an empty string.
    store = _bare_store()
    store._self_commit = ""
    store.catalog = [_self_entry(latest_commit="bbb222")]
    assert store._self_update_status() == (False, "")


# -- _installed_apps ------------------------------------------------------------ #
def test_installed_apps_merges_catalog_over_stored_record():
    store = _bare_store()
    store.config = {"username": "jan-tdy"}
    store.system_installed = {"apt": {}, "snap": {}, "flatpak": {}}
    store.catalog = [{"key": "jan-tdy/repo/app", "name": "App", "category": "Tools",
                      "version": "1.1", "repo": "repo", "icon_data": b"png"}]
    store.installed = {"jan-tdy/repo/app": {"name": "App", "repo": "repo",
                                            "category": "Tools", "version": "1.0"}}
    apps = store._installed_apps()
    assert len(apps) == 1
    # The installed record's own version wins (what's actually on disk),
    # not the newer one that might be sitting in the catalog.
    assert apps[0]["version"] == "1.0"
    # Icon data isn't stored per-installed-app, so it's pulled from the
    # catalog entry instead of being lost.
    assert apps[0]["icon_data"] == b"png"
    assert apps[0]["backend"] == "git"


def test_installed_apps_falls_back_when_not_in_catalog():
    store = _bare_store()
    store.config = {"username": "jan-tdy"}
    store.system_installed = {"apt": {}, "snap": {}, "flatpak": {}}
    store.catalog = []
    store.installed = {"jan-tdy/repo/app": {"name": "App", "repo": "repo",
                                            "category": "Tools", "version": "1.0"}}
    apps = store._installed_apps()
    assert apps[0]["key"] == "jan-tdy/repo/app"
    assert apps[0]["name"] == "App"


def test_installed_apps_includes_installed_system_packages():
    store = _bare_store()
    store.config = {"username": "jan-tdy"}
    store.catalog = []
    store.installed = {}
    store.system_installed = {
        "apt": {"gimp": {"version": "2.10.34", "name": "GIMP", "icon_data": b"png"}},
        "snap": {}, "flatpak": {},
    }
    apps = store._installed_apps()
    assert len(apps) == 1
    assert apps[0]["backend"] == "apt"
    assert apps[0]["pkg_id"] == "gimp"
    assert apps[0]["version"] == "2.10.34"
    assert apps[0]["name"] == "GIMP"
    assert apps[0]["icon_data"] == b"png"


# -- manual folder removal -------------------------------------------------- #
def test_remove_manual_folder_drops_matching_installed_apps(monkeypatch, tmp_path):
    store = _bare_store()
    store.config = {"manual_paths": [str(tmp_path)], "username": "jan-tdy"}
    store.installed = {
        "jan-tdy/repo/app": {"source": "manual", "repo_root": str(tmp_path),
                             "name": "App"},
        "jan-tdy/other/app2": {"source": "store", "repo_root": "/elsewhere",
                               "name": "Other"},
    }
    store.system_installed = {"apt": {}, "snap": {}, "flatpak": {}}
    store.catalog = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
    monkeypatch.setattr(persistence, "save_config", lambda cfg: None)
    monkeypatch.setattr(persistence, "save_installed", lambda data: None)
    monkeypatch.setattr(store, "has_launcher", lambda app: False)
    monkeypatch.setattr(store, "_update_desktop_db", lambda: None)
    monkeypatch.setattr(store, "refresh_views", lambda: None)
    monkeypatch.setattr(store, "_toast", lambda msg: None)

    store.remove_manual_folder(str(tmp_path))

    assert str(tmp_path) not in store.config["manual_paths"]
    assert "jan-tdy/repo/app" not in store.installed
    assert "jan-tdy/other/app2" in store.installed


def test_remove_manual_folder_does_nothing_when_declined(monkeypatch, tmp_path):
    store = _bare_store()
    store.config = {"manual_paths": [str(tmp_path)], "username": "jan-tdy"}
    store.installed = {"jan-tdy/repo/app": {"source": "manual",
                                            "repo_root": str(tmp_path)}}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.No)

    store.remove_manual_folder(str(tmp_path))

    assert str(tmp_path) in store.config["manual_paths"]
    assert "jan-tdy/repo/app" in store.installed


# -- third-party (community catalog) confirmation --------------------------- #
def test_confirm_third_party_skips_jan_tdy_apps():
    store = _bare_store()
    store.config = {}
    assert store._confirm_third_party({"catalog_source": "jan-tdy"}) is True


def test_confirm_third_party_asks_once_then_remembers(monkeypatch):
    store = _bare_store()
    store.config = {"confirmed_third_party": []}
    monkeypatch.setattr(persistence, "save_config", lambda cfg: None)
    calls = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: calls.append(1) or QMessageBox.Yes)

    app = {"key": "someone/repo/app", "catalog_source": "community",
          "publisher": "someone", "repo": "repo", "name": "App"}
    assert store._confirm_third_party(app) is True
    assert store._confirm_third_party(app) is True
    assert len(calls) == 1  # second call used the remembered confirmation


def test_confirm_third_party_declined_returns_false(monkeypatch):
    store = _bare_store()
    store.config = {"confirmed_third_party": []}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.No)
    app = {"key": "someone/repo/app", "catalog_source": "community",
          "publisher": "someone", "repo": "repo", "name": "App"}
    assert store._confirm_third_party(app) is False
    assert app["key"] not in store.config["confirmed_third_party"]


# -- GitWorker branch handling on update ---------------------------------------- #
def _bare_worker(branch="main", current_branch="main", repo_root="/tmp/app"):
    """A GitWorker with its git calls recorded instead of executed.

    Bypassing __init__ (and therefore QThread.__init__) keeps these tests
    free of a Qt event loop; only the command building is under test.
    """
    worker = GitWorker.__new__(GitWorker)
    worker.action = "update"
    worker.username = "jan-tdy"
    worker.repo = "app"
    worker.repo_root = Path(repo_root)
    worker.branch = branch
    worker.req_path = None
    worker.method = "sync"
    worker.release_tag = None
    worker.token = ""
    worker.commands = []

    def fake_run(cmd, cwd=None):
        worker.commands.append(cmd)
        if "--abbrev-ref" in cmd:
            return current_branch + "\n"
        return ""

    worker._run = fake_run
    return worker


def test_clone_uses_the_app_s_own_publisher_not_a_fixed_account():
    # The community catalog can point at any GitHub account, not just the
    # one configured in Settings — the clone URL must follow it.
    worker = _bare_worker()
    worker.username = "someone-else"
    worker.repo = "their-app"
    seen = {}

    def fake_run(cmd, cwd=None):
        seen["cmd"] = cmd
        return ""

    worker._run = fake_run
    worker.repo_root = Path("/tmp/does-not-exist-app")
    try:
        worker._clone("main")
    except Exception:
        pass
    assert "https://github.com/someone-else/their-app.git" in seen["cmd"]


def test_update_switches_to_the_configured_branch():
    """Updating to another configured branch fetches and checks it out."""
    # The metadata branch can change after the app was installed; the
    # clone is shallow, so the new branch has to be fetched before it
    # can be checked out.
    worker = _bare_worker(branch="beta", current_branch="main")
    worker._switch_branch("beta")
    fetch, checkout = worker.commands
    assert fetch[-5:] == ["fetch", "--depth", "1", "origin",
                          "+refs/heads/beta:refs/remotes/origin/beta"]
    assert checkout[-4:] == ["checkout", "-B", "beta", "origin/beta"]


def test_git_auth_token_is_passed_via_environment(monkeypatch):
    """Git authentication keeps the token out of command-line arguments."""
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    worker = GitWorker.__new__(GitWorker)
    worker.token = "secret-token"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    import codemaster.git_worker as git_worker_module
    monkeypatch.setattr(git_worker_module.subprocess, "run", fake_run)
    worker._run(["git", "fetch", "origin"])

    cmd, kwargs = calls[0]
    assert all("secret-token" not in arg for arg in cmd)
    assert cmd == ["git", "-c", "credential.helper=", "fetch", "origin"]
    assert kwargs["env"]["GIT_CONFIG_COUNT"] == "1"
    assert kwargs["env"]["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert kwargs["env"]["GIT_CONFIG_VALUE_0"].startswith(
        "Authorization: Basic ")


# -- GitWorker._run credential-prompt / hang safeguards --------------------------- #
def test_run_disables_git_terminal_prompts_and_stdin(monkeypatch):
    # Regression test for https://github.com/jan-tdy/codemaster/issues/17 —
    # a git command must never be able to block on an interactive
    # credential prompt: no inherited stdin, terminal prompts disabled, and
    # any configured credential helper (which might prompt via its own GUI,
    # bypassing GIT_TERMINAL_PROMPT) turned off.
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    worker = GitWorker.__new__(GitWorker)
    worker.token = ""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    import codemaster.git_worker as git_worker_module
    monkeypatch.setattr(git_worker_module.subprocess, "run", fake_run)
    worker._run(["git", "fetch", "origin"])

    cmd, kwargs = calls[0]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert cmd == ["git", "-c", "credential.helper=", "fetch", "origin"]


def test_run_applies_a_default_timeout_to_git_commands(monkeypatch):
    worker = GitWorker.__new__(GitWorker)
    worker.token = ""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    import codemaster.git_worker as git_worker_module
    from codemaster.constants import GIT_TIMEOUT_SECONDS
    monkeypatch.setattr(git_worker_module.subprocess, "run", fake_run)
    worker._run(["git", "fetch", "origin"])
    assert calls[0][1]["timeout"] == GIT_TIMEOUT_SECONDS


def test_run_does_not_apply_a_default_timeout_to_non_git_commands(monkeypatch):
    # pip/apt installs can legitimately run far longer than a git network
    # op is ever allowed to; only git commands get the default timeout.
    worker = GitWorker.__new__(GitWorker)
    worker.token = ""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    import codemaster.git_worker as git_worker_module
    monkeypatch.setattr(git_worker_module.subprocess, "run", fake_run)
    worker._run(["pip", "install", "requests"])
    assert calls[0][1]["timeout"] is None
    assert calls[0][1]["stdin"] == subprocess.DEVNULL


def test_run_converts_a_timeout_into_a_runtime_error(monkeypatch):
    worker = GitWorker.__new__(GitWorker)
    worker.token = ""

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    import codemaster.git_worker as git_worker_module
    monkeypatch.setattr(git_worker_module.subprocess, "run", fake_run)
    try:
        worker._run(["git", "fetch", "origin"])
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "Timed out" in str(exc)


def test_clone_preserves_existing_repo_until_replacement_succeeds(tmp_path):
    """A successful clone replaces the existing repository atomically."""
    worker = _bare_worker(branch="beta", current_branch="main")
    worker.repo_root = tmp_path / "app"
    worker.repo_root.mkdir()
    (worker.repo_root / "old").write_text("old", encoding="utf-8")

    def successful_clone(cmd, cwd=None):
        assert (worker.repo_root / "old").exists()
        assert cmd[:-1] == [
            "git", "clone", "--depth", "1", "--branch", "beta",
            "https://github.com/jan-tdy/app.git",
        ]
        clone_root = Path(cmd[-1])
        (clone_root / "new").write_text("new", encoding="utf-8")
        return ""

    worker._run = successful_clone
    worker._clone("beta")

    assert (worker.repo_root / "new").read_text(encoding="utf-8") == "new"
    assert not (worker.repo_root / "old").exists()
    assert list(tmp_path.iterdir()) == [worker.repo_root]


def test_clone_failure_preserves_existing_repo_and_cleans_temp_dir(tmp_path):
    """A failed clone preserves the repository and removes temporary data."""
    worker = _bare_worker(branch="beta", current_branch="main")
    worker.repo_root = tmp_path / "app"
    worker.repo_root.mkdir()
    (worker.repo_root / "old").write_text("old", encoding="utf-8")

    def failing_clone(cmd, cwd=None):
        raise RuntimeError("clone failed")

    worker._run = failing_clone
    try:
        worker._clone("beta")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "clone failed"

    assert (worker.repo_root / "old").read_text(encoding="utf-8") == "old"
    assert list(tmp_path.iterdir()) == [worker.repo_root]


def test_update_re_clones_when_the_branch_switch_fails():
    """A failed branch switch falls back to cloning the requested branch."""
    worker = _bare_worker(branch="beta", current_branch="main")
    cloned = []

    def failing_run(cmd, cwd=None):
        raise RuntimeError("your local changes would be overwritten")

    worker._run = failing_run
    worker._clone = cloned.append
    worker._switch_branch("beta")
    assert cloned == ["beta"]


def test_current_branch_reads_the_checked_out_branch():
    """The current branch is read from the worker's repository checkout."""
    worker = _bare_worker(branch="beta", current_branch="main")
    assert worker._current_branch() == "main"
    assert worker.commands == [["git", "-C", "/tmp/app",
                                "rev-parse", "--abbrev-ref", "HEAD"]]


def test_current_branch_parses_rev_parse_output(monkeypatch):
    worker = _bare_worker()
    monkeypatch.setattr(worker, "_run", lambda cmd, cwd=None: "feature\n")
    assert worker._current_branch() == "feature"


def test_current_branch_blank_when_detached_head():
    # A shallow clone left in a detached state (or a repo with no commits
    # yet) reports "HEAD" from rev-parse, not a real branch name.
    worker = _bare_worker()
    worker._run = lambda cmd, cwd=None: "HEAD\n"
    assert worker._current_branch() == ""


def test_sync_update_pulls_when_already_on_configured_branch(monkeypatch):
    worker = _bare_worker(branch="main")
    monkeypatch.setattr(worker, "_current_branch", lambda: "main")
    calls = []
    monkeypatch.setattr(worker, "_run", lambda cmd, cwd=None: calls.append(cmd))
    worker._sync_update()
    assert len(calls) == 1
    assert calls[0][-2:] == ["pull", "--ff-only"]


def test_sync_update_switches_branch_when_configured_branch_changed(monkeypatch):
    # Regression test for https://github.com/jan-tdy/codemaster/issues/15 —
    # changing the Metadata branch setting (or an app's declared branch)
    # after install must be picked up on the next update, not silently
    # ignored by a plain pull on whatever is still checked out.
    worker = _bare_worker(branch="dev")
    monkeypatch.setattr(worker, "_current_branch", lambda: "main")
    switched = []
    monkeypatch.setattr(worker, "_switch_branch", lambda branch: switched.append(branch))
    cloned = []
    monkeypatch.setattr(worker, "_clone", lambda ref: cloned.append(ref))
    worker._sync_update()
    assert switched == ["dev"]
    assert cloned == []


# -- CatalogLoader rate limit handling ---------------------------------------- #
class _FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def _bare_loader(token=""):
    loader = CatalogLoader.__new__(CatalogLoader)
    loader.token = token
    return loader


def test_raise_for_status_reports_rate_limit_without_token():
    loader = _bare_loader(token="")
    resp = _FakeResponse(403, {"X-RateLimit-Remaining": "0",
                               "X-RateLimit-Reset": "1893456000"})
    try:
        loader._raise_for_status(resp)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "rate limit" in str(exc).lower()
        assert "Manual & Settings" in str(exc)


def test_raise_for_status_reports_rate_limit_with_token():
    loader = _bare_loader(token="ghp_dummy")
    resp = _FakeResponse(403, {"X-RateLimit-Remaining": "0"})
    try:
        loader._raise_for_status(resp)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "rate limit" in str(exc).lower()
        assert "try again later" in str(exc).lower()


def test_raise_for_status_passes_through_non_rate_limit_403():
    loader = _bare_loader()
    resp = _FakeResponse(403, {})
    try:
        loader._raise_for_status(resp)
        assert False, "expected HTTPError"
    except requests.HTTPError:
        pass


def test_raise_for_status_ignores_healthy_response():
    loader = _bare_loader()
    resp = _FakeResponse(200, {})
    loader._raise_for_status(resp)  # must not raise


# -- CatalogLoader repo-scan skip cache --------------------------------------- #
def test_catalog_loader_init_groups_cached_apps_by_repo():
    cached_apps = [
        {"repo": "repo-a", "repo_pushed_at": "t1", "id": "app1", "publisher": "jan-tdy"},
        {"repo": "repo-a", "repo_pushed_at": "t1", "id": "app2", "publisher": "jan-tdy"},
        # Pre-upgrade cache entries without repo_pushed_at are ignored,
        # not treated as a cache hit for an unscanned repo.
        {"repo": "repo-b", "id": "app3", "publisher": "jan-tdy"},
    ]
    loader = CatalogLoader("jan-tdy", "main", "", cached_apps=cached_apps)
    assert set(loader._cache_by_repo.keys()) == {"repo-a"}
    assert len(loader._cache_by_repo["repo-a"]) == 2


def test_catalog_loader_init_excludes_pre_v2_cache_entries_without_publisher():
    # Regression test: a pre-v2 on-disk catalog.json has no "publisher"
    # field and its apps carry the old two-segment "repo/id" key, which
    # can never match an installed.json record migrated to
    # "publisher/repo/id". Reusing such a stale entry from the cache
    # (because the repo's pushed_at hasn't moved since the user's last v1
    # scan) would keep every previously-installed app looking "not
    # installed" forever — excluding it here forces one real rescan
    # instead, which regenerates a correctly-keyed entry.
    cached_apps = [
        {"repo": "repo-a", "repo_pushed_at": "t1", "id": "app1"},  # no publisher
    ]
    loader = CatalogLoader("jan-tdy", "main", "", cached_apps=cached_apps)
    assert loader._cache_by_repo == {}
    assert loader._cached_apps_for("repo-a", "t1") is None


def test_catalog_loader_init_groups_community_cache_by_file():
    cached_apps = [
        {"catalog_source": "community", "catalog_file": "catalog/x.json",
         "catalog_file_sha": "sha1", "repo_pushed_at": "t1", "id": "app1"},
    ]
    loader = CatalogLoader("jan-tdy", "main", "", cached_apps=cached_apps)
    assert "catalog/x.json" in loader._community_cache
    assert loader._community_cache["catalog/x.json"]["file_sha"] == "sha1"


def test_cached_apps_for_hits_when_pushed_at_matches():
    loader = _bare_loader()
    loader._cache_by_repo = {
        "repo-a": [{"repo": "repo-a", "repo_pushed_at": "t1", "id": "app1"}],
    }
    cached = loader._cached_apps_for("repo-a", "t1")
    assert cached is not None and cached[0]["id"] == "app1"


def test_cached_apps_for_misses_when_repo_changed_since():
    loader = _bare_loader()
    loader._cache_by_repo = {
        "repo-a": [{"repo": "repo-a", "repo_pushed_at": "t1", "id": "app1"}],
    }
    assert loader._cached_apps_for("repo-a", "t2") is None


def test_cached_apps_for_misses_when_repo_never_scanned():
    loader = _bare_loader()
    loader._cache_by_repo = {}
    assert loader._cached_apps_for("repo-a", "t1") is None


def test_cached_community_apps_hits_when_file_sha_and_pushed_at_match():
    loader = _bare_loader()
    loader._community_cache = {
        "catalog/x.json": {"file_sha": "sha1", "repo_pushed_at": "t1",
                          "apps": [{"id": "app1"}]},
    }
    cached = loader._cached_community_apps("catalog/x.json", "sha1", "t1")
    assert cached is not None and cached[0]["id"] == "app1"


def test_cached_community_apps_misses_when_file_changed():
    loader = _bare_loader()
    loader._community_cache = {
        "catalog/x.json": {"file_sha": "sha1", "repo_pushed_at": "t1",
                          "apps": [{"id": "app1"}]},
    }
    assert loader._cached_community_apps("catalog/x.json", "sha2", "t1") is None


class _FakeRepoListResponse:
    def __init__(self, repos):
        self.status_code = 200
        self.headers = {}
        self._repos = repos

    def raise_for_status(self):
        pass

    def json(self):
        return self._repos


class _FakeSession:
    """Serves one page of /repos results, ignoring pagination params."""

    def __init__(self, repos):
        self._repos = repos
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        return _FakeRepoListResponse(self._repos if self.calls == 1 else [])


def test_list_repos_skips_archived_and_reports_pushed_at():
    loader = _bare_loader(token="")
    loader.username = "jan-tdy"
    loader.session = _FakeSession([
        {"name": "active-repo", "default_branch": "main", "private": False,
         "archived": False, "pushed_at": "2026-01-01T00:00:00Z",
         "owner": {"login": "jan-tdy"}},
        {"name": "old-repo", "default_branch": "main", "private": False,
         "archived": True, "pushed_at": "2020-01-01T00:00:00Z",
         "owner": {"login": "jan-tdy"}},
    ])
    assert loader._list_repos() == [
        ("active-repo", "main", False, "2026-01-01T00:00:00Z"),
    ]


def test_apps_from_metadata_keys_by_publisher_repo_and_id(monkeypatch):
    loader = _bare_loader()
    monkeypatch.setattr(loader, "_fetch_release", lambda *a: None)
    monkeypatch.setattr(loader, "_fetch_latest_commit", lambda *a: "sha123")
    monkeypatch.setattr(loader, "_fetch_icon", lambda *a: None)
    meta = {"apps": [{"id": "app1", "name": "App One"}]}
    apps = loader._apps_from_metadata(meta, "someone-else", "their-repo", "main",
                                      False, "2026-01-01", "community")
    assert apps[0]["key"] == "someone-else/their-repo/app1"
    assert apps[0]["publisher"] == "someone-else"
    assert apps[0]["catalog_source"] == "community"
    assert apps[0]["backend"] == "git"


# -- system_packages: table parsing + app-dict unification -------------------- #
def test_parse_table_handles_tabwriter_style_padded_columns():
    table = (
        "Name      Version  Publisher      Notes  Summary\n"
        "gimp      2.10.34  snapcrafters*  -      GNU Image Manipulation Program\n"
        "inkscape  1.2.2    inkscape*      -      Vector graphics editor\n"
    )
    rows = system_packages._parse_table(table)
    assert rows[0] == {"Name": "gimp", "Version": "2.10.34",
                       "Publisher": "snapcrafters*", "Notes": "-",
                       "Summary": "GNU Image Manipulation Program"}
    assert rows[1]["Name"] == "inkscape"


def test_parse_table_returns_nothing_for_empty_output():
    assert system_packages._parse_table("") == []


def test_apt_search_parses_name_dash_summary_lines(monkeypatch):
    monkeypatch.setattr(system_packages, "_run", lambda cmd, timeout=120:
                        "gimp - GNU Image Manipulation Program\n"
                        "gimp-data - Data files for GIMP\n")
    results = system_packages.AptBackend.search("gimp")
    assert results[0] == {"id": "gimp", "name": "gimp",
                          "summary": "GNU Image Manipulation Program",
                          "version": ""}
    assert len(results) == 2


def test_apt_installed_parses_dpkg_query_tab_output(monkeypatch):
    monkeypatch.setattr(system_packages.shutil, "which", lambda name: "/usr/bin/dpkg-query")
    monkeypatch.setattr(system_packages.AptBackend, "_gui_desktop_entries",
                        staticmethod(lambda: {"gimp": {}, "vlc": {}}))
    monkeypatch.setattr(system_packages, "_run", lambda cmd, timeout=120:
                        "gimp\t2.10.34\nvlc\t3.0.20\nlibc6\t2.39\n")
    assert system_packages.AptBackend.installed() == {
        "gimp": {"version": "2.10.34", "name": "gimp", "icon_data": None},
        "vlc": {"version": "3.0.20", "name": "vlc", "icon_data": None},
    }


def test_apt_installed_uses_desktop_entry_name_and_icon(monkeypatch):
    # The actual complaint this fixes: apps that already have a nice name
    # and icon via their own .desktop file used to show up with their bare
    # dpkg package name and a lettered placeholder instead.
    monkeypatch.setattr(system_packages.shutil, "which", lambda name: "/usr/bin/dpkg-query")
    monkeypatch.setattr(system_packages.AptBackend, "_gui_desktop_entries",
                        staticmethod(lambda: {"gimp": {"name": "GIMP", "icon": "gimp"}}))
    monkeypatch.setattr(system_packages, "resolve_icon_bytes", lambda icon: b"icon-bytes")
    monkeypatch.setattr(system_packages, "_run", lambda cmd, timeout=120: "gimp\t2.10.34\n")
    assert system_packages.AptBackend.installed() == {
        "gimp": {"version": "2.10.34", "name": "GIMP", "icon_data": b"icon-bytes"},
    }


def test_apt_gui_desktop_entries_reads_each_file_once(tmp_path, monkeypatch):
    # Regression test: installed() and _installed_app_packages() used to
    # each independently re-scan every dpkg file list and re-parse every
    # matching .desktop file — _gui_desktop_entries() is the single pass
    # both now build on, so one call must account for all the I/O.
    info_dir = tmp_path / "info"
    info_dir.mkdir()
    (info_dir / "gimp.list").write_text(
        "/usr\n/usr/share/applications/gimp.desktop\n", encoding="utf-8")
    monkeypatch.setattr(system_packages.AptBackend, "DPKG_INFO_DIR", info_dir)
    reads = []

    def fake_parse(path):
        reads.append(path)
        return {"name": "GIMP", "icon": "gimp"}

    monkeypatch.setattr(system_packages, "parse_desktop_entry", fake_parse)
    entries = system_packages.AptBackend._gui_desktop_entries()
    assert entries == {"gimp": {"name": "GIMP", "icon": "gimp"}}
    assert len(reads) == 1  # not re-parsed once per caller


def test_apt_upgradable_parses_apt_list_output(monkeypatch):
    monkeypatch.setattr(system_packages, "_run", lambda cmd, timeout=120:
                        "Listing...\n"
                        "gimp/noble-updates 2.10.36-3 amd64 [upgradable from: 2.10.34-1]\n"
                        "vlc/noble-updates 3.0.21-1 amd64 [upgradable from: 3.0.20-1]\n")
    assert system_packages.AptBackend.upgradable() == {"gimp", "vlc"}


def test_apt_upgradable_empty_when_nothing_pending(monkeypatch):
    monkeypatch.setattr(system_packages, "_run", lambda cmd, timeout=120: "Listing...\n")
    assert system_packages.AptBackend.upgradable() == set()


def test_snap_upgradable_parses_refresh_list_table(monkeypatch):
    out = ("Name   Version   Rev   Size   Publisher     Notes\n"
           "gimp   2.10.36   315   169MB  snapcrafters*  -\n")
    monkeypatch.setattr(system_packages, "_run", lambda cmd, timeout=120: out)
    assert system_packages.SnapBackend.upgradable() == {"gimp"}


def test_snap_upgradable_empty_when_all_up_to_date(monkeypatch):
    monkeypatch.setattr(system_packages, "_run",
                        lambda cmd, timeout=120: "All snaps up to date.\n")
    assert system_packages.SnapBackend.upgradable() == set()


def test_apt_installed_returns_nothing_without_desktop_apps(monkeypatch):
    # dpkg tracks thousands of library/dependency packages that are not
    # applications — without the desktop-entry filter, every one of them
    # would show up as an "installed app" tile.
    monkeypatch.setattr(system_packages.shutil, "which", lambda name: "/usr/bin/dpkg-query")
    monkeypatch.setattr(system_packages.AptBackend, "_gui_desktop_entries",
                        staticmethod(lambda: {}))
    calls = []
    monkeypatch.setattr(system_packages, "_run",
                        lambda cmd, timeout=120: calls.append(cmd) or "")
    assert system_packages.AptBackend.installed() == {}
    assert calls == []  # short-circuits before even asking dpkg-query for versions


def test_installed_app_packages_reads_dpkg_file_lists(tmp_path, monkeypatch):
    info_dir = tmp_path / "info"
    info_dir.mkdir()
    (info_dir / "gimp.list").write_text(
        "/usr\n/usr/share/applications/gimp.desktop\n", encoding="utf-8")
    (info_dir / "libc6.list").write_text("/lib/x86_64-linux-gnu/libc.so.6\n",
                                        encoding="utf-8")
    (info_dir / "vlc:amd64.list").write_text(
        "/usr/share/applications/vlc.desktop\n", encoding="utf-8")
    monkeypatch.setattr(system_packages.AptBackend, "DPKG_INFO_DIR", info_dir)
    # The actual .desktop files' content (Terminal=/NoDisplay=/Hidden=) is
    # covered by test_is_gui_desktop_entry_* below; here only the dpkg
    # file-list path matching is under test.
    monkeypatch.setattr(system_packages, "parse_desktop_entry",
                        lambda path: {"name": "x", "icon": None})
    assert system_packages.AptBackend._installed_app_packages() == {"gimp", "vlc"}


def test_is_gui_desktop_entry_true_for_a_plain_gui_app(tmp_path):
    desktop = tmp_path / "gimp.desktop"
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=GIMP\n"
        "Exec=gimp\n", encoding="utf-8")
    assert system_packages.AptBackend._is_gui_desktop_entry(desktop) is True


def test_is_gui_desktop_entry_false_for_terminal_tools(tmp_path):
    # Regression test: python3.12.desktop (and similar interpreter/JRE
    # menu entries dpkg ships) are Terminal=true and/or NoDisplay=true —
    # without filtering on this, every one of those showed up in the Store
    # looking exactly like a GUI app.
    desktop = tmp_path / "python3.12.desktop"
    desktop.write_text(
        "[Desktop Entry]\nName=Python (v3.12)\nExec=/usr/bin/python3.12\n"
        "Terminal=true\nType=Application\nNoDisplay=true\n", encoding="utf-8")
    assert system_packages.AptBackend._is_gui_desktop_entry(desktop) is False


def test_is_gui_desktop_entry_false_for_non_application_type(tmp_path):
    desktop = tmp_path / "link.desktop"
    desktop.write_text("[Desktop Entry]\nType=Link\nURL=https://example.com\n",
                       encoding="utf-8")
    assert system_packages.AptBackend._is_gui_desktop_entry(desktop) is False


def test_is_gui_desktop_entry_false_for_missing_file(tmp_path):
    assert system_packages.AptBackend._is_gui_desktop_entry(
        tmp_path / "missing.desktop") is False


def test_resolve_icon_bytes_reads_an_absolute_path(tmp_path):
    icon_file = tmp_path / "icon.png"
    icon_file.write_bytes(b"fake-png-bytes")
    assert system_packages.resolve_icon_bytes(str(icon_file)) == b"fake-png-bytes"


def test_resolve_icon_bytes_none_for_unresolvable_name():
    assert system_packages.resolve_icon_bytes("a-name-no-theme-ships") is None


def test_resolve_icon_bytes_none_for_empty_icon():
    assert system_packages.resolve_icon_bytes(None) is None
    assert system_packages.resolve_icon_bytes("") is None


def test_snap_desktop_entry_matches_snapname_underscore_appname(tmp_path, monkeypatch):
    monkeypatch.setattr(system_packages.SnapBackend, "SNAP_DESKTOP_DIR", tmp_path)
    (tmp_path / "gimp_gimp.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=GIMP\nIcon=gimp\n",
        encoding="utf-8")
    entry = system_packages.SnapBackend._desktop_entry_for_snap("gimp")
    assert entry == {"name": "GIMP", "icon": "gimp"}


def test_snap_desktop_entry_none_when_no_launcher_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(system_packages.SnapBackend, "SNAP_DESKTOP_DIR", tmp_path)
    assert system_packages.SnapBackend._desktop_entry_for_snap("gimp") is None


def test_flatpak_desktop_entry_for_app_id_reads_export_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(system_packages.FlatpakBackend, "EXPORT_DESKTOP_DIRS", [tmp_path])
    (tmp_path / "org.gimp.GIMP.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=GIMP\nIcon=org.gimp.GIMP\n",
        encoding="utf-8")
    entry = system_packages.FlatpakBackend._desktop_entry_for_app_id("org.gimp.GIMP")
    assert entry == {"name": "GIMP", "icon": "org.gimp.GIMP"}


def test_to_app_dict_unifies_system_package_shape():
    app = system_packages.to_app_dict(
        "snap", {"id": "gimp", "name": "gimp", "summary": "Image editor",
                "version": "2.10.34"})
    assert app["backend"] == "snap"
    assert app["key"] == "snap:gimp"
    assert app["pkg_id"] == "gimp"
    assert app["category"] == "Snap packages"
    assert app["homepage"] == "https://snapcraft.io/gimp"


def test_to_app_dict_prefers_installed_version_over_search_version():
    app = system_packages.to_app_dict(
        "apt", {"id": "gimp", "name": "gimp", "summary": "", "version": "2.10.30"},
        installed_version="2.10.34")
    assert app["version"] == "2.10.34"


def test_category_to_backend_matches_to_app_dict_categories():
    for backend_name in system_packages.BACKENDS:
        app = system_packages.to_app_dict(backend_name, {"id": "x", "name": "x"})
        assert system_packages.CATEGORY_TO_BACKEND[app["category"]] == backend_name


# -- widgets: sidebar/filter rebuild must never re-trigger its own signal ----- #
def test_category_sidebar_set_categories_does_not_recurse():
    sidebar = CategorySidebar()
    calls = []
    sidebar.categoryChanged.connect(calls.append)
    sidebar.set_categories(["Astronomy", "Developer Tools"])
    sidebar.set_categories(["Astronomy", "Developer Tools"])  # same selection again
    # Rebuilding with the same effective selection must not fire the
    # signal — that would have re-triggered the caller's own rebuild,
    # which calls set_categories again, forever.
    assert calls == []


def test_filter_bar_set_sources_and_publishers_do_not_recurse():
    bar = FilterBar()
    calls = []
    bar.changed.connect(lambda: calls.append(1))
    bar.set_sources([("APT", "apt"), ("Snap", "snap")])
    bar.set_publishers(["jan-tdy", "someone-else"])
    bar.set_sources([("APT", "apt"), ("Snap", "snap")])
    assert calls == []
    assert bar.current_source() == ""
    assert bar.current_publisher() == ""


def test_app_tile_button_label_reflects_install_state():
    store = _bare_store()
    store.installed = {}
    store.system_installed = {"apt": {}, "snap": {}, "flatpak": {}}
    store.catalog = []
    app = {"key": "jan-tdy/repo/app", "backend": "git", "name": "App",
          "category": "Tools", "version": "1.0"}
    tile = AppTile(app, store)
    from PyQt5.QtWidgets import QPushButton
    buttons = tile.findChildren(QPushButton)
    assert buttons[-1].text() == "Install"

    store.installed = {"jan-tdy/repo/app": {}}
    tile2 = AppTile(app, store)
    buttons2 = tile2.findChildren(QPushButton)
    assert buttons2[-1].text() == "Details"


def test_details_page_show_app_does_not_leak_widgets_across_apps(monkeypatch):
    # Regression test: _lay.addLayout(header)/addLayout(actions) nest
    # plain QLayouts, not QWidgets — a takeAt()-based clear() never finds
    # a widget() on those items, so the action buttons inside them used to
    # never get deleted and piled up, visibly overlapping, every time
    # show_app() ran again for a different (or the same) app.
    #
    # git apps trigger a real ReadmeLoader thread; keep it offline and
    # instant so this test's outcome doesn't depend on network access.
    def no_network(*_a, **_kw):
        raise requests.RequestException("no network in tests")
    monkeypatch.setattr("codemaster.widgets.requests.get", no_network)

    store = _bare_store()
    store.config = {"username": "jan-tdy"}
    store.installed = {"jan-tdy/repo/app1": {}, "jan-tdy/repo/app2": {}}
    store.catalog = []
    store.system_installed = {"apt": {}, "snap": {}, "flatpak": {}}
    page = DetailsPage(store)

    from PyQt5.QtWidgets import QPushButton
    app1 = {"backend": "git", "key": "jan-tdy/repo/app1", "name": "App One",
          "category": "Tools", "version": "1.0", "requirements": "req.txt"}
    app2 = {"backend": "git", "key": "jan-tdy/repo/app2", "name": "App Two",
          "category": "Tools", "version": "1.0", "requirements": "req.txt"}

    page.show_app(app1)
    count_1 = len(page.findChildren(QPushButton))
    page.show_app(app2)
    count_2 = len(page.findChildren(QPushButton))
    page.show_app(app1)
    count_3 = len(page.findChildren(QPushButton))

    assert count_1 == count_2 == count_3
    assert count_1 > 0


def test_details_page_always_offers_update_for_installed_flatpak():
    # Regression test: has_update() is always False for flatpak (it's
    # never probed — FlatpakBackend.upgradable() is always empty), but
    # the Update button is documented and intended to show anyway, once
    # installed, trusting flatpak itself to no-op if there's nothing to
    # do. _actions() used to gate the button on has_update() alone, so it
    # could never appear for flatpak at all.
    store = _bare_store()
    store.system_installed = {"apt": {}, "snap": {},
                              "flatpak": {"org.gimp.GIMP": {"version": "2.10",
                                                           "name": "GIMP",
                                                           "icon_data": None}}}
    page = DetailsPage(store)
    app = {"backend": "flatpak", "pkg_id": "org.gimp.GIMP", "name": "GIMP",
          "category": "Flatpak packages", "version": "2.10"}
    page.show_app(app)
    from PyQt5.QtWidgets import QPushButton
    labels = [b.text() for b in page.findChildren(QPushButton)]
    assert "Update" in labels


# -- version display -------------------------------------------------------- #
def test_format_version_adds_v_prefix():
    assert format_version("1.5.0") == "v1.5.0"


def test_format_version_does_not_double_an_existing_v_prefix():
    # Regression test: a release tag is conventionally already "v1.5.0",
    # and effective_version() returns it verbatim — the display code used
    # to blindly prepend another "v", showing "vv1.5.0".
    assert format_version("v1.5.0") == "v1.5.0"
    assert format_version("V1.5.0") == "v1.5.0"


def test_format_version_empty_stays_empty():
    assert format_version("") == ""
    assert format_version(None) == ""
