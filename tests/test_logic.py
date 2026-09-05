import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jadiv_code_master as cm


def _bare_store():
    """A CodeMaster instance with no Qt widgets constructed.

    Bypassing __init__ (and therefore QMainWindow.__init__) keeps these
    tests free of any real Qt platform/display dependency, since the pure
    logic under test only touches plain attributes.
    """
    return cm.CodeMaster.__new__(cm.CodeMaster)


# -- desktop entry helpers -------------------------------------------------- #
def test_desktop_exec_quote_escapes_shell_metacharacters():
    raw = 'py "$HOME" `id` \\ end'
    assert cm._desktop_exec_quote(raw) == 'py \\"\\$HOME\\" \\`id\\` \\\\ end'


def test_desktop_exec_line_wraps_cd_and_run():
    line = cm._desktop_exec_line("/home/user/App", "python3 app.py")
    assert line == 'sh -c "cd /home/user/App && python3 app.py"'


# -- xdg data home ----------------------------------------------------------- #
def test_xdg_data_home_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert cm._xdg_data_home() == Path.home() / ".local" / "share"


def test_xdg_data_home_ignores_empty_env(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "")
    assert cm._xdg_data_home() == Path.home() / ".local" / "share"


def test_xdg_data_home_uses_env_when_set(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/data")
    assert cm._xdg_data_home() == Path("/custom/data")


# -- effective_version -------------------------------------------------------- #
def test_effective_version_prefers_release_tag():
    app = {"update_method": "release", "release_tag": "v2.0", "version": "1.0"}
    assert cm.CodeMaster.effective_version(app) == "v2.0"


def test_effective_version_falls_back_to_version_string():
    app = {"update_method": "sync", "version": "1.2.3"}
    assert cm.CodeMaster.effective_version(app) == "1.2.3"


def test_effective_version_release_without_tag_uses_version():
    app = {"update_method": "release", "release_tag": None, "version": "1.0"}
    assert cm.CodeMaster.effective_version(app) == "1.0"


# -- has_update ---------------------------------------------------------------- #
def test_has_update_release_compares_version_string():
    store = _bare_store()
    store.installed = {"repo/app": {"version": "1.0"}}
    store.catalog = [{"key": "repo/app", "update_method": "release",
                      "release_tag": "1.1", "version": "1.0"}]
    assert store.has_update({"key": "repo/app"}) is True


def test_has_update_release_same_tag_is_up_to_date():
    store = _bare_store()
    store.installed = {"repo/app": {"version": "1.1"}}
    store.catalog = [{"key": "repo/app", "update_method": "release",
                      "release_tag": "1.1", "version": "1.0"}]
    assert store.has_update({"key": "repo/app"}) is False


def test_has_update_sync_compares_commit_sha_not_version_string():
    # Regression test for https://github.com/jan-tdy/codemaster/issues/9 —
    # a publisher who forgets to bump `version` must still see the update
    # once new commits land on the tracked branch.
    store = _bare_store()
    store.installed = {"repo/app": {"version": "1.0", "commit": "aaa111"}}
    store.catalog = [{"key": "repo/app", "update_method": "sync",
                      "version": "1.0", "latest_commit": "bbb222"}]
    assert store.has_update({"key": "repo/app"}) is True


def test_has_update_sync_same_commit_is_up_to_date():
    store = _bare_store()
    store.installed = {"repo/app": {"version": "1.0", "commit": "aaa111"}}
    store.catalog = [{"key": "repo/app", "update_method": "sync",
                      "version": "1.0", "latest_commit": "aaa111"}]
    assert store.has_update({"key": "repo/app"}) is False


def test_has_update_returns_false_when_not_installed():
    store = _bare_store()
    store.installed = {}
    store.catalog = [{"key": "repo/app", "update_method": "sync",
                      "latest_commit": "aaa111"}]
    assert store.has_update({"key": "repo/app"}) is False


def test_has_update_returns_false_when_not_in_catalog():
    store = _bare_store()
    store.installed = {"repo/app": {"version": "1.0", "commit": "aaa111"}}
    store.catalog = []
    assert store.has_update({"key": "repo/app"}) is False


# -- _installed_apps ------------------------------------------------------------ #
def test_installed_apps_merges_catalog_over_stored_record():
    store = _bare_store()
    store.catalog = [{"key": "repo/app", "name": "App", "category": "Tools",
                      "version": "1.1", "repo": "repo", "icon_data": b"png"}]
    store.installed = {"repo/app": {"name": "App", "repo": "repo",
                                    "category": "Tools", "version": "1.0"}}
    apps = store._installed_apps()
    assert len(apps) == 1
    # The installed record's own version wins (what's actually on disk),
    # not the newer one that might be sitting in the catalog.
    assert apps[0]["version"] == "1.0"
    # Icon data isn't stored per-installed-app, so it's pulled from the
    # catalog entry instead of being lost.
    assert apps[0]["icon_data"] == b"png"


def test_installed_apps_falls_back_when_not_in_catalog():
    store = _bare_store()
    store.catalog = []
    store.installed = {"repo/app": {"name": "App", "repo": "repo",
                                    "category": "Tools", "version": "1.0"}}
    apps = store._installed_apps()
    assert apps[0]["key"] == "repo/app"
    assert apps[0]["name"] == "App"
