"""Discovers every git-backed app Code Master knows how to install:
  1. every repo the configured GitHub account owns that ships a
     ``codemaster-metadata.json`` (the original jan-tdy scan), and
  2. the community catalog — one ``catalog/<name>.json`` file per
     submission, living in this project's own repository, each a complete
     ``codemaster-metadata.json``-shaped description of an app hosted
     anywhere on GitHub (not necessarily by the configured account).

Both produce the same "git" backend app dicts; apt/snap/flatpak apps are
discovered separately by ``system_packages`` and merged in by the caller.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from .constants import (
    COMMUNITY_CATALOG_BRANCH, COMMUNITY_CATALOG_DIR, COMMUNITY_CATALOG_OWNER,
    COMMUNITY_CATALOG_REPO, METADATA_FILE,
)


class CatalogLoader(QThread):
    """Discover every app published through codemaster-metadata.json."""
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)
    status = pyqtSignal(str)

    # How many repos to scan over the network at once. GitHub's REST API
    # has no documented hard cap on concurrent requests, but a very high
    # number risks tripping its secondary (abuse) rate limit — 8 keeps
    # scans fast without doing that.
    MAX_PARALLEL_SCANS = 8

    def __init__(self, username, branch, token="", cached_apps=None):
        super().__init__()
        self.username = username
        self.branch = branch
        self.token = token
        # A shared Session reuses TCP/TLS connections across the parallel
        # scan requests below instead of opening a fresh one per call.
        self.session = requests.Session()
        # Apps from the last successful scan, grouped by repo, so a repo
        # whose `pushed_at` hasn't moved since then can be skipped
        # entirely instead of re-fetching metadata/release/commit/icon.
        self._cache_by_repo = {}
        # Community catalog entries are cached by their catalog/*.json file
        # path, keyed on that file's own content hash plus the *target*
        # repo's pushed_at, since either one changing means a rescan.
        self._community_cache = {}
        for app in cached_apps or []:
            if app.get("catalog_source") == "community":
                path = app.get("catalog_file")
                if not path:
                    continue
                entry = self._community_cache.setdefault(path, {
                    "file_sha": app.get("catalog_file_sha"),
                    "repo_pushed_at": app.get("repo_pushed_at"),
                    "apps": [],
                })
                entry["apps"].append(app)
            elif app.get("repo_pushed_at") and app.get("repo"):
                self._cache_by_repo.setdefault(app["repo"], []).append(app)

    # -- shared GitHub plumbing -------------------------------------- #
    def _headers(self):
        """Build the optional GitHub API authorization headers."""
        return {"Authorization": f"token {self.token}"} if self.token else {}

    def _raise_for_status(self, resp):
        """Raise an actionable error when GitHub API rate limits are
        exhausted."""
        if resp.status_code == 403 and \
                resp.headers.get("X-RateLimit-Remaining") == "0":
            when = ""
            reset = resp.headers.get("X-RateLimit-Reset")
            if reset:
                try:
                    when = (" It resets at "
                            f"{time.strftime('%H:%M', time.localtime(int(reset)))}.")
                except (ValueError, OSError, OverflowError):
                    pass
            hint = ("Add a GitHub token in Manual & Settings → GitHub "
                    "token to raise the limit." if not self.token else
                    "Your configured GitHub token has also hit its rate "
                    "limit — try again later.")
            raise RuntimeError(
                f"GitHub API rate limit reached.{when} {hint}")
        resp.raise_for_status()

    def _authenticated_login(self):
        """Determine the GitHub account associated with the configured
        token, or None if it cannot be determined."""
        try:
            resp = self.session.get("https://api.github.com/user",
                                headers=self._headers(), timeout=20)
            resp.raise_for_status()
            return resp.json().get("login", "")
        except requests.RequestException:
            return None

    def _list_repos(self):
        """List non-archived repositories owned by the configured GitHub
        user.

        Returns:
            list[tuple[str, str, bool, str]]: Repository name, default
            branch, private status, and last-push timestamp (used to skip
            re-scanning repos that haven't changed) for each owned,
            non-archived repository.
        """
        repos, page = [], 1
        use_user_repos = False
        if self.token:
            auth_login = self._authenticated_login()
            use_user_repos = bool(auth_login) and \
                auth_login.lower() == self.username.lower()
        if use_user_repos:
            url_base = "https://api.github.com/user/repos"
            extra = "&affiliation=owner&visibility=all"
        else:
            url_base = f"https://api.github.com/users/{self.username}/repos"
            extra = ""
        while True:
            url = f"{url_base}?per_page=100&page={page}{extra}"
            resp = self.session.get(url, headers=self._headers(), timeout=20)
            self._raise_for_status(resp)
            chunk = resp.json()
            if not chunk:
                break
            for r in chunk:
                # Archived repos are frozen (read-only) on GitHub, so their
                # published apps can't change either — skip scanning them.
                if r.get("archived"):
                    continue
                owner = (r.get("owner") or {}).get("login", "")
                if use_user_repos and owner.lower() != self.username.lower():
                    continue
                repos.append((r["name"], r.get("default_branch", "main"),
                             bool(r.get("private")), r.get("pushed_at", "")))
            if len(chunk) < 100:
                break
            page += 1
        return repos

    def _fetch_repo_info(self, publisher, repo):
        """(default_branch, is_private, pushed_at) for an arbitrary
        publisher/repo, or None if it can't be read (deleted, renamed, or
        private without access) — used for community catalog entries,
        which can point at any GitHub repository."""
        url = f"https://api.github.com/repos/{publisher}/{repo}"
        try:
            resp = self.session.get(url, headers=self._headers(), timeout=20)
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        return (data.get("default_branch", "main"), bool(data.get("private")),
                data.get("pushed_at", ""))

    def _fetch_metadata(self, publisher, repo, default_branch):
        branches = []
        for b in (self.branch, default_branch):
            if b and b not in branches:
                branches.append(b)
        for branch in branches:
            raw = (f"https://raw.githubusercontent.com/{publisher}/"
                   f"{repo}/{branch}/{METADATA_FILE}")
            try:
                resp = self.session.get(raw, headers=self._headers(), timeout=20)
            except requests.RequestException:
                continue
            if resp.status_code == 200:
                try:
                    return resp.json(), branch
                except ValueError:
                    return None, None
        return None, None

    def _fetch_release(self, publisher, repo):
        """Latest published release tag for a repo, or None if it has none."""
        url = f"https://api.github.com/repos/{publisher}/{repo}/releases/latest"
        try:
            resp = self.session.get(url, headers=self._headers(), timeout=20)
            if resp.status_code == 200:
                return resp.json().get("tag_name")
        except requests.RequestException:
            return None
        return None

    def _fetch_latest_commit(self, publisher, repo, branch):
        """Latest commit SHA on ``branch``, used to detect updates for
        'sync' apps whose publisher may forget to bump the metadata
        version."""
        url = f"https://api.github.com/repos/{publisher}/{repo}/commits/{branch}"
        try:
            resp = self.session.get(url, headers=self._headers(), timeout=20)
            if resp.status_code == 200:
                return resp.json().get("sha")
        except requests.RequestException:
            return None
        return None

    def _fetch_icon(self, publisher, repo, branch, icon_path):
        if not icon_path:
            return None
        raw = f"https://raw.githubusercontent.com/{publisher}/{repo}/{branch}/{icon_path}"
        try:
            resp = self.session.get(raw, headers=self._headers(), timeout=20)
            if resp.status_code == 200:
                return resp.content
        except requests.RequestException:
            return None
        return None

    # -- building app dicts from a metadata document ------------------ #
    def _apps_from_metadata(self, meta, publisher, repo, branch, is_private,
                             pushed_at, catalog_source,
                             catalog_file=None, catalog_file_sha=None):
        """Every app dict a ``codemaster-metadata.json``-shaped document
        describes, enriched with icon/release/commit data. Shared by the
        primary scan and the community catalog, which differ only in
        *where* ``meta`` came from."""
        release_tag = None
        if any(a.get("update_method") == "release"
               for a in meta.get("apps", [])):
            release_tag = self._fetch_release(publisher, repo)
        sync_commit = None
        if any(a.get("update_method", "sync") != "release"
               for a in meta.get("apps", [])):
            sync_commit = self._fetch_latest_commit(publisher, repo, branch)
        apps = []
        for app in meta.get("apps", []):
            app_branch = app.get("branch") or branch
            icon_data = self._fetch_icon(publisher, repo, app_branch, app.get("icon"))
            method = app.get("update_method", "sync")
            apps.append({
                "backend": "git",
                "key": f"{publisher}/{repo}/{app.get('id')}",
                "publisher": publisher,
                "repo": repo,
                "repo_pushed_at": pushed_at,
                "branch": app_branch,
                "id": app.get("id"),
                "name": app.get("name", app.get("id", "?")),
                "tagline": app.get("tagline", ""),
                "description": app.get("description", ""),
                "category": app.get("category", "Other"),
                "version": str(app.get("version", "")),
                "author": app.get("author", publisher),
                "icon_data": icon_data,
                "subdir": app.get("subdir", "."),
                "entrypoint": app.get("entrypoint", ""),
                "run": app.get("run", ""),
                "requirements": app.get("requirements"),
                "mime_types": app.get("mime_types") or [],
                "maintained": app.get("maintained", True),
                "update_method": method,
                "release_tag": release_tag if method == "release" else None,
                "latest_commit": sync_commit if method != "release" else None,
                "homepage": app.get("homepage") or meta.get("homepage"),
                "private": is_private,
                "catalog_source": catalog_source,
                "catalog_file": catalog_file,
                "catalog_file_sha": catalog_file_sha,
            })
        return apps

    # -- primary scan (configured GitHub account's own repos) --------- #
    def _cached_apps_for(self, repo, pushed_at):
        """Previously-scanned apps for ``repo``, or None if there's no
        usable cache entry (never scanned, or the repo changed since)."""
        apps = self._cache_by_repo.get(repo)
        if not apps or not pushed_at:
            return None
        if apps[0].get("repo_pushed_at") != pushed_at:
            return None
        return apps

    def _scan_repo(self, repo, default_branch, is_private, pushed_at):
        """Fetch metadata/release/commit/icon for one repo. Called from a
        worker thread (see ``run``); returns the app dicts it publishes,
        or an empty list if it has no ``codemaster-metadata.json``."""
        meta, branch = self._fetch_metadata(self.username, repo, default_branch)
        if not meta:
            return []
        return self._apps_from_metadata(meta, self.username, repo, branch,
                                         is_private, pushed_at, "jan-tdy")

    # -- community catalog (catalog/*.json in this project's own repo) - #
    def _list_community_files(self):
        url = (f"https://api.github.com/repos/{COMMUNITY_CATALOG_OWNER}/"
               f"{COMMUNITY_CATALOG_REPO}/contents/{COMMUNITY_CATALOG_DIR}"
               f"?ref={COMMUNITY_CATALOG_BRANCH}")
        try:
            resp = self.session.get(url, headers=self._headers(), timeout=20)
        except requests.RequestException:
            return []
        if resp.status_code == 404:
            return []  # no catalog/ directory (or it's empty) yet
        try:
            self._raise_for_status(resp)
        except Exception:  # noqa: BLE001
            return []  # best-effort: never fail the whole load over this
        try:
            entries = resp.json()
        except ValueError:
            return []
        if not isinstance(entries, list):
            return []
        return [e for e in entries if e.get("type") == "file"
                and e.get("name", "").endswith(".json")]

    def _cached_community_apps(self, path, file_sha, repo_pushed_at):
        entry = self._community_cache.get(path)
        if not entry or not file_sha or not repo_pushed_at:
            return None
        if entry["file_sha"] != file_sha or entry["repo_pushed_at"] != repo_pushed_at:
            return None
        return entry["apps"]

    def _scan_community_entry(self, file_entry):
        """One ``catalog/<name>.json`` submission -> the app dicts it
        describes, or an empty list if it's malformed or its target repo
        can no longer be read."""
        path = file_entry.get("path") or file_entry.get("name", "")
        sha = file_entry.get("sha", "")
        download_url = file_entry.get("download_url")
        if not download_url:
            return []
        try:
            resp = self.session.get(download_url, headers=self._headers(), timeout=20)
        except requests.RequestException:
            return []
        if resp.status_code != 200:
            return []
        try:
            meta = resp.json()
        except ValueError:
            return []
        publisher = meta.get("publisher")
        repo = meta.get("repo")
        if not publisher or not repo or not meta.get("apps"):
            return []
        info = self._fetch_repo_info(publisher, repo)
        if not info:
            return []
        default_branch, is_private, pushed_at = info
        branch = meta.get("branch") or default_branch
        cached = self._cached_community_apps(path, sha, pushed_at)
        if cached is not None:
            return cached
        return self._apps_from_metadata(
            meta, publisher, repo, branch, is_private, pushed_at,
            catalog_source="community", catalog_file=path, catalog_file_sha=sha)

    def _scan_community_catalog(self):
        files = self._list_community_files()
        if not files:
            return []
        out = []
        workers = min(self.MAX_PARALLEL_SCANS, len(files))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._scan_community_entry, f) for f in files]
            for future in as_completed(futures):
                try:
                    out.extend(future.result())
                except Exception:  # noqa: BLE001
                    continue
        return out

    # -- entry point --------------------------------------------------- #
    def run(self):
        try:
            self.status.emit("Contacting GitHub…")
            apps = []
            repos = self._list_repos()

            # Repos whose last push hasn't moved since the previous
            # successful scan are reused from cache without any network
            # call — this is what keeps a scan fast as more repos pile up:
            # only genuinely new or changed repos need fetching.
            to_scan = []
            for repo, default_branch, is_private, pushed_at in repos:
                cached = self._cached_apps_for(repo, pushed_at)
                if cached is not None:
                    apps.extend(cached)
                else:
                    to_scan.append((repo, default_branch, is_private, pushed_at))

            if to_scan:
                skipped = len(repos) - len(to_scan)
                note = f" ({skipped} unchanged, cached)" if skipped else ""
                self.status.emit(f"Scanning {len(to_scan)} repo(s){note}…")
                done = 0
                workers = min(self.MAX_PARALLEL_SCANS, len(to_scan))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(self._scan_repo, repo, default_branch,
                                    is_private, pushed_at): repo
                        for repo, default_branch, is_private, pushed_at
                        in to_scan
                    }
                    for future in as_completed(futures):
                        repo = futures[future]
                        done += 1
                        self.status.emit(
                            f"Scanning {repo}… ({done}/{len(to_scan)})")
                        apps.extend(future.result())

            self.status.emit("Scanning community catalog…")
            try:
                apps.extend(self._scan_community_catalog())
            except Exception:  # noqa: BLE001
                pass  # best-effort: never fail the whole load over this

            apps.sort(key=lambda a: (a["category"].lower(), a["name"].lower()))
            self.loaded.emit(apps)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
