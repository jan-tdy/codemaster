import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

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


# -- apt dependency mapping --------------------------------------------------- #
def test_apt_package_name_strips_version_constraint():
    assert cm._apt_package_name("PyQt5>=5.15,<6") == "python3-pyqt5"


def test_apt_package_name_normalizes_underscores():
    assert cm._apt_package_name("some_package==1.0") == "python3-some-package"


def test_apt_requirement_satisfied_is_true_for_bare_requirement():
    assert cm._apt_requirement_satisfied("requests") is True


def test_apt_requirement_satisfied_checks_installed_version():
    installed = requests.__version__
    assert cm._apt_requirement_satisfied(f"requests=={installed}") is True
    assert cm._apt_requirement_satisfied("requests==0.0.1") is False


def test_apt_requirement_satisfied_false_for_unknown_package():
    assert cm._apt_requirement_satisfied("not-a-real-package>=1.0") is False


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


# -- GitWorker branch handling on update ---------------------------------------- #
def _bare_worker(branch, current_branch):
    """A GitWorker with its git calls recorded instead of executed.

    Bypassing __init__ (and therefore QThread.__init__) keeps these tests
    free of a Qt event loop; only the command building is under test.
    """
    worker = cm.GitWorker.__new__(cm.GitWorker)
    worker.action = "update"
    worker.username = "jan-tdy"
    worker.repo = "app"
    worker.repo_root = Path("/tmp/app")
    worker.branch = branch
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


def test_update_switches_to_the_configured_branch():
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
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    worker = cm.GitWorker.__new__(cm.GitWorker)
    worker.token = "secret-token"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cm.subprocess, "run", fake_run)
    worker._run(["git", "fetch", "origin"])

    cmd, kwargs = calls[0]
    assert all("secret-token" not in arg for arg in cmd)
    assert cmd == ["git", "fetch", "origin"]
    assert kwargs["env"]["GIT_CONFIG_COUNT"] == "1"
    assert kwargs["env"]["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert kwargs["env"]["GIT_CONFIG_VALUE_0"].startswith(
        "Authorization: Basic ")


def test_clone_preserves_existing_repo_until_replacement_succeeds(tmp_path):
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
    worker = _bare_worker(branch="beta", current_branch="main")
    cloned = []

    def failing_run(cmd, cwd=None):
        raise RuntimeError("your local changes would be overwritten")

    worker._run = failing_run
    worker._clone = cloned.append
    worker._switch_branch("beta")
    assert cloned == ["beta"]


def test_current_branch_reads_the_checked_out_branch():
    worker = _bare_worker(branch="beta", current_branch="main")
    assert worker._current_branch() == "main"
    assert worker.commands == [["git", "-C", "/tmp/app",
                                "rev-parse", "--abbrev-ref", "HEAD"]]


# -- CatalogLoader rate limit handling ---------------------------------------- #
class _FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        """
        Raise an HTTP error when the response indicates a failed request.
        
        Raises:
            requests.HTTPError: If the response status code is 400 or higher.
        """
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def _bare_loader(token=""):
    """Create an uninitialized catalog loader with the specified authentication token.
    
    Parameters:
    	token (str): Authentication token assigned to the loader.
    
    Returns:
    	CatalogLoader: A loader instance configured with the token.
    """
    loader = cm.CatalogLoader.__new__(cm.CatalogLoader)
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
