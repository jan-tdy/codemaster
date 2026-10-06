"""Clone / update / install-deps for git-backed apps, off the UI thread."""

import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from .constants import GIT_TIMEOUT_SECONDS


def _requirement_name(requirement):
    """The bare package name at the start of a requirements.txt line."""
    return re.split(r"[<>=!~\[; ]", requirement.strip(), maxsplit=1)[0]


def apt_package_name(requirement):
    """Best-effort mapping of a requirements.txt line to a Debian package
    name, e.g. 'PyQt5>=5.15,<6' -> 'python3-pyqt5'."""
    return "python3-" + _requirement_name(requirement).lower().replace("_", "-")


def apt_requirement_satisfied(requirement):
    """Whether the package apt just installed actually satisfies
    ``requirement``.

    A bare requirement (no version constraint) is always satisfied — apt's
    current version is fine. A constrained one (e.g. "requests>=2.32.4,<3")
    is only accepted when the optional `packaging` library can parse it and
    confirm the installed version matches: apt may ship an older or newer
    release than the pin, and trusting apt-get's exit code alone would
    silently accept whichever version it happened to install.
    """
    if _requirement_name(requirement) == requirement.strip():
        return True
    try:
        from packaging.requirements import Requirement
        from importlib.metadata import version as installed_version
        req = Requirement(requirement)
        return req.specifier.contains(installed_version(req.name),
                                       prereleases=True)
    except Exception:  # noqa: BLE001
        return False


class GitWorker(QThread):
    """Clone / update / install-deps without freezing the UI."""
    done = pyqtSignal(bool, str, str)  # ok, message, resolved commit SHA

    def __init__(self, action, username, repo, repo_root, branch,
                 req_path=None, method="sync", release_tag=None, token=""):
        """``username`` is the app's *publisher* (GitHub owner of ``repo``)
        — for community-catalog apps this can be anyone, not just the
        configured GitHub account."""
        super().__init__()
        self.action = action
        self.username = username
        self.repo = repo
        self.repo_root = Path(repo_root)
        self.branch = branch
        self.req_path = req_path
        self.method = method
        self.release_tag = release_tag
        self.token = token

    def _run(self, cmd, cwd=None, timeout=None):
        """Run a command, raising an error when it exits unsuccessfully.

        stdin is always closed and, for git, terminal credential prompts and
        credential helpers are disabled — otherwise an auth challenge (a
        repo that went private, a stored token that expired or was
        revoked) leaves git blocked waiting for input that will never
        come, hanging this background thread with no feedback to the
        user. A timeout guards the same failure mode for a stalled or
        dead network connection.
        """
        is_git = bool(cmd) and cmd[0] == "git"
        env = self._auth_env() if is_git else None
        if is_git:
            env = env or os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            cmd = [cmd[0], "-c", "credential.helper="] + cmd[1:]
            if timeout is None:
                timeout = GIT_TIMEOUT_SECONDS
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True,
                                  text=True, env=env,
                                  stdin=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Timed out after {timeout}s waiting for: {' '.join(cmd)}"
            ) from exc
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return proc.stdout

    def _auth_env(self):
        """Per-invocation git auth for private repos.

        Pass the authorization header through Git's environment-backed
        configuration so the token is absent from both the command line and
        the clone's ``.git/config`` on disk.
        """
        if not self.token:
            return None
        basic = base64.b64encode(
            f"x-access-token:{self.token}".encode("utf-8")).decode("ascii")
        env = os.environ.copy()
        try:
            config_count = max(0, int(env.get("GIT_CONFIG_COUNT", "0")))
        except ValueError:
            config_count = 0
        env[f"GIT_CONFIG_KEY_{config_count}"] = "http.extraheader"
        env[f"GIT_CONFIG_VALUE_{config_count}"] = \
            f"Authorization: Basic {basic}"
        env["GIT_CONFIG_COUNT"] = str(config_count + 1)
        return env

    def _clone(self, ref):
        """Fresh shallow clone of the repo at a branch or tag."""
        url = f"https://github.com/{self.username}/{self.repo}.git"
        self.repo_root.parent.mkdir(parents=True, exist_ok=True)
        clone_root = Path(tempfile.mkdtemp(
            prefix=f".{self.repo_root.name}-clone-",
            dir=self.repo_root.parent,
        ))
        cmd = ["git", "clone", "--depth", "1"]
        if ref:
            cmd += ["--branch", ref]
        cmd += [url, str(clone_root)]

        backup_root = clone_root.with_name(f"{clone_root.name}-old")
        try:
            self._run(cmd)
            had_existing_repo = self.repo_root.exists() or \
                self.repo_root.is_symlink()
            if had_existing_repo:
                self.repo_root.replace(backup_root)
            try:
                clone_root.replace(self.repo_root)
            except Exception:
                if had_existing_repo and backup_root.exists():
                    backup_root.replace(self.repo_root)
                raise
            if had_existing_repo:
                if backup_root.is_dir() and not backup_root.is_symlink():
                    shutil.rmtree(backup_root, ignore_errors=True)
                else:
                    backup_root.unlink(missing_ok=True)
        finally:
            if clone_root.exists():
                shutil.rmtree(clone_root, ignore_errors=True)

    def _current_branch(self):
        """The branch checked out in repo_root, or "" when detached/unknown."""
        try:
            ref = self._run(
                ["git", "-C", str(self.repo_root),
                 "rev-parse", "--abbrev-ref", "HEAD"]
            ).strip()
            return "" if ref == "HEAD" else ref
        except Exception:  # noqa: BLE001
            return ""

    def _switch_branch(self, branch):
        """Move an existing clone onto ``branch``.

        Clones are shallow (``--depth 1 --branch <branch>``), so the other
        branches aren't in the clone at all and a plain checkout fails —
        the branch has to be fetched first. If the switch fails anyway
        (local state in the way, history too shallow to connect), fall
        back to a fresh clone at the requested branch.
        """
        try:
            self._run(["git", "-C", str(self.repo_root),
                       "fetch", "--depth", "1",
                       "origin",
                       f"+refs/heads/{branch}:refs/remotes/origin/{branch}"])
            self._run(["git", "-C", str(self.repo_root), "checkout",
                       "-B", branch, f"origin/{branch}"])
        except Exception:  # noqa: BLE001
            self._clone(branch)

    def _head_commit(self):
        """The commit actually checked out in repo_root right now."""
        try:
            return self._run(
                ["git", "-C", str(self.repo_root), "rev-parse", "HEAD"]
            ).strip()
        except Exception:  # noqa: BLE001
            return ""

    def _install_via_apt(self, requirements):
        """Best-effort install of the distro's own packages via apt.

        Preferred over pip: apt packages don't fight PEP 668's
        "externally-managed-environment" restriction, so we never need
        --break-system-packages, which can destabilize the system Python.
        Returns True only when apt-get reports success for every mapped
        package name *and* the installed version actually satisfies each
        requirement; the caller falls back to pip otherwise (e.g. a
        requirement with no Debian package, an apt version that doesn't
        meet a pin, or no polkit agent running).
        """
        apt_get = shutil.which("apt-get")
        pkexec = shutil.which("pkexec")
        if not apt_get or not pkexec:
            return False
        packages = sorted({apt_package_name(r) for r in requirements})
        try:
            self._run([pkexec, apt_get, "install", "-y"] + packages)
        except Exception:  # noqa: BLE001
            return False
        return all(apt_requirement_satisfied(r) for r in requirements)

    def _install_deps(self):
        """Install the application's dependencies from its requirements
        file, using system packages when available and otherwise installing
        them with Python's package manager."""
        try:
            requirements = [
                line.strip() for line in
                Path(self.req_path).read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        except OSError:
            requirements = []

        if requirements and self._install_via_apt(requirements):
            return

        # Install into the interpreter that will actually run the app —
        # the same bare `python3` launch_app()/create_launcher() resolve
        # via PATH — not Code Master's own sys.executable. The two can be
        # different interpreters (e.g. Code Master running from a pyenv
        # build while apps launch with the system python3), so a
        # dependency installed into one is invisible to the other: pip
        # reports success, but the app still fails to import it when run.
        python = shutil.which("python3") or sys.executable
        cmd = [python, "-m", "pip", "install", "--user", "-r", str(self.req_path)]
        try:
            self._run(cmd)
        except RuntimeError as exc:
            if "externally-managed-environment" not in str(exc) or not requirements:
                raise
            # PEP 668: pip refuses, and we don't override it with
            # --break-system-packages. Point at the exact apt packages to
            # install by hand instead of leaving a bare pip stderr dump.
            packages = " ".join(sorted({apt_package_name(r)
                                         for r in requirements}))
            raise RuntimeError(
                f"{exc}\n\nThis system's Python blocks direct pip installs "
                f"(PEP 668) and apt doesn't have a matching version. "
                f"Install manually with:\n    sudo apt install {packages}"
            ) from exc

    def _sync_update(self):
        """Update an already-cloned sync app in place.

        Reconciles the clone with ``self.branch`` (the configured metadata
        branch, or the app's own declared branch) before pulling — a plain
        pull would otherwise keep tracking whatever happens to be checked
        out, silently ignoring a branch change made after install.
        """
        if self.branch and self._current_branch() != self.branch:
            self._switch_branch(self.branch)
        else:
            self._run(["git", "-C", str(self.repo_root), "pull", "--ff-only"])

    def run(self):
        """Execute the requested repository operation and emit its result.

        Installation and update actions clone or update the repository,
        while dependency actions install the application's declared
        dependencies. Failures are reported through the completion signal.
        """
        try:
            # "release" apps track a tagged GitHub release; "sync" apps track
            # the latest code on a branch.
            release = (self.method == "release" and self.release_tag)
            if self.action in ("install", "update"):
                if release:
                    # Tags can't be fast-forwarded — re-clone at the new tag.
                    self._clone(self.release_tag)
                elif self.action == "install" and \
                        not (self.repo_root / ".git").exists():
                    self._clone(self.branch)
                else:
                    self._sync_update()
                # Report the commit Git actually checked out, not the SHA
                # the catalog scan saw before this ran — the branch may have
                # advanced (or a release re-clone lands on a different
                # commit than the last sync did) in between.
                self.done.emit(
                    True, "Installed" if self.action == "install"
                    else "Updated", self._head_commit())
            elif self.action == "deps":
                self._install_deps()
                self.done.emit(True, "Dependencies installed", "")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, str(exc), "")
