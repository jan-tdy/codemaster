# Jadiv Code Master

A universal desktop **app store** for Linux.

Code Master lists apps from four sources side by side:

- every [jan-tdy](https://github.com/jan-tdy) GitHub repository that ships a
  `codemaster-metadata.json` file,
- the project's own **[community catalog](catalog/)** — anyone can submit an
  app pointing at their own repository, no matter who owns it,
- **APT**, **Snap** and **Flatpak** packages, searched and installed through
  the system's own package managers.

A single git repository can publish several apps — for example
[`devcontrolenterpise`](https://github.com/jan-tdy/devcontrolenterpise)
publishes the Telescope Cover, Astrofoto, Atacama (C14) and DSLR apps.

<img width="1186" height="794" alt="image" src="https://github.com/user-attachments/assets/9f629815-124e-4787-98c1-f09f9480cd02" />


---

## Features

- **🛍 Store** — a tile grid grouped by a category **sidebar**, with a
  **Source** and **Publisher** filter alongside the search box, so you can
  narrow down to "only community apps", "only this publisher's apps", or
  "only APT packages", etc. Click a tile (or its single **Install**/
  **Details** button) to open the app's **details page** — its README
  (rendered from the repo, for git-backed apps), full description, and
  every action (Install, Update, Open, Add to menu, Install deps, Remove)
  in one place. Scanning every jan-tdy repo plus the community catalog can
  take up to a minute on the first run, so the catalog is **cached to disk**
  and shown instantly on the next launch while a fresh scan runs in the
  background.
- **📦 APT / Snap / Flatpak** — pick one of those categories in the Store
  sidebar to search the system's own package managers (their full catalogs
  are too large to list up front, so you search instead of browse). Install,
  update, remove and launch packages from any backend that's actually
  installed on your system; a Settings note shows which ones Code Master
  found.
- **🌐 Community catalog** — Code Master's own repository has a
  [`catalog/`](catalog/) folder where *anyone* can submit an app via pull
  request, pointing at their own GitHub repo — it doesn't have to belong to
  jan-tdy. Community apps carry a **community** badge and a one-time
  confirmation before their first install, since installing one clones and
  can run code from a repository Code Master's maintainer doesn't control.
- **📲 Installed** — **launch**, **update** or **remove** anything you've
  installed regardless of source, **add git apps to your application menu**
  (creates a `.desktop` launcher with the app's icon), and install an app's
  dependencies when it declares a requirements file (tries
  `apt install python3-<package>` for each one first, falling back to
  `pip install --user` only for packages apt doesn't have).
- **⟳ Updates** — installed apps with a newer version available are listed
  here; update them individually or all at once. Git apps: `sync` apps via
  `git pull`, `release` apps by fetching the latest release tag. APT
  (`apt list --upgradable`) and Snap (`snap refresh --list`) packages are
  checked the same way, so they show up here too when there's a pending
  update. Flatpak isn't probed — its **Update** button stays always
  available once installed instead (its own package manager decides
  whether there's anything to do).
- **⚙ Manual & Settings** — register apps you installed **by hand** by
  pointing Code Master at a folder containing a `codemaster-metadata.json`
  file, and **remove** a registered location again (which also drops the
  apps that came from it out of Installed — the folder itself is never
  touched). Configure the GitHub user, metadata branch and an optional
  token, and **update Code Master itself** in place (when run from a git
  checkout).
- **🔒 Private repos** — add a GitHub token with `repo` scope in Settings and
  Code Master will scan, list, install and update your **private** jan-tdy
  repositories too, not just public ones. Private apps show a 🔒 badge in the
  Store. The token authenticates the catalog scan, the metadata/icon fetch
  and the `git clone`/`git pull` used to install and update — it's passed
  through each Git process's environment and never exposed in its command
  line or written into a cloned repo's `.git/config`.

---

## Running

```bash
sudo apt install python3-pyqt5 python3-requests   # Debian/Ubuntu
python3 jadiv_code_master.py
```

Prefer your distro's own packages over pip: on Debian/Ubuntu/Arch and other
distros with [PEP 668](https://peps.python.org/pep-0668/) "externally managed"
system Pythons, a direct `pip install` is refused unless you pass
`--break-system-packages`, which can destabilize the system Python — `apt`
sidesteps that entirely. If you'd rather use pip anyway:

```bash
python3 -m pip install --user -r requirements.txt   # PyQt5.QtSvg is needed for SVG icons
python3 jadiv_code_master.py
```

`python3 -m pip` (rather than a bare `pip`) guarantees the packages land in the
same interpreter that actually runs the app.

The APT/Snap/Flatpak Store categories only do anything useful when the
matching command-line tool (`apt`/`apt-cache`, `snap`, `flatpak`) is actually
installed; Code Master detects this automatically and shows which backends
it found under Manual & Settings. APT installs/removals/updates need
`pkexec` (a polkit authentication agent) since they require root; Snap
installs/removals/updates need `pkexec` too; Flatpak installs are per-user
and need no elevated privileges at all.

### Desktop launcher

To add **Jadiv Code Master** to your application menu (Linux, per-user, no root):

```bash
./install-launcher.sh             # install
./install-launcher.sh --uninstall # remove
```

This installs `assets/codemaster.desktop` into `~/.local/share/applications/`
(with the repo path filled in) and the icon into
`~/.local/share/icons/hicolor/scalable/apps/`, then refreshes the desktop and
icon caches. Look for *Jadiv Code Master* in your launcher.

> **Don't double-click `assets/codemaster.desktop` in a file manager** — it
> still has the `__INSTALL_DIR__` placeholder, so it won't run. Some file
> managers (e.g. Thunar on Xfce) also treat a plain `.desktop` file opened
> from an arbitrary folder as untrusted and try to *import it as a panel
> launcher* instead of running it, which fails with an unrelated
> `Failed to add a plugin to the panel: … org.xfce.Panel was not provided by
> any .service files` error. Always use `./install-launcher.sh` to install
> the launcher, then start it from your application menu.

Code Master keeps its state in:

| Path | Purpose |
|------|---------|
| `~/.config/codemaster/config.json`    | GitHub user, branch, token, manual folders |
| `~/.config/codemaster/installed.json` | registry of installed git apps |
| `~/.local/share/codemaster/apps/<publisher>/<repo>/` | cloned repositories of installed git apps |
| `~/.local/share/codemaster/cache/`    | cached catalog + icons (instant startup) |
| `~/.local/share/codemaster/launcher-icons/` | icons for generated `.desktop` launchers |
| `~/.local/share/applications/codemaster-*.desktop` | menu launchers Code Master creates for apps |

APT/Snap/Flatpak packages aren't tracked in any of the above — Code Master
asks each package manager directly whether something is installed, so it
always agrees with reality even if you also use `apt`/`snap`/`flatpak`
directly from a terminal.

---

## Publishing an app: `codemaster-metadata.json`

To make a repository you own appear in the Store, drop a
`codemaster-metadata.json` file in its **root**. One repo, one metadata
file, any number of apps:

```json
{
  "schema_version": 1,
  "publisher": "jan-tdy",
  "repo": "devcontrolenterpise",
  "homepage": "https://github.com/jan-tdy/devcontrolenterpise",
  "apps": [
    {
      "id": "devcontrol-krytka",
      "name": "DevControl – Telescope Cover",
      "tagline": "Motorised dome cover controller",
      "description": "Longer paragraph shown on the app's details page.",
      "category": "Astronomy",
      "version": "2026.6_1.0",
      "author": "JapySoft TDY",
      "icon": "Krytka01/logo.png",
      "subdir": "Krytka01",
      "entrypoint": "devcontrol.py",
      "run": "python3 devcontrol.py",
      "requirements": "requirements.txt",
      "update_method": "sync",
      "maintained": true
    }
  ]
}
```

Don't own the repo, or want to list an app without releasing it through
jan-tdy at all? See **[Publishing without owning jan-tdy: the community
catalog](catalog/)** instead — same file shape, submitted as a PR to this
project rather than merged into your own repo.

### Field reference

| Field | Required | Meaning |
|-------|----------|---------|
| `schema_version` | yes | Metadata format version (currently `1`). |
| `publisher` / `repo` | yes | GitHub owner and repository name. |
| `branch` | no | Branch the app's code lives on. For a repo's own `codemaster-metadata.json` this defaults to whichever branch the metadata itself was found on; only needed to override. Required semantics differ for [community catalog](catalog/) entries — see there. |
| `homepage` | no | Repo-level link used for the **Details** page's homepage link. |
| `apps[]` | yes | One entry per app the repo publishes. |
| `apps[].id` | yes | Stable id, unique within the repo. |
| `apps[].name` | yes | Display name. |
| `apps[].tagline` | no | One-line summary shown on the tile. |
| `apps[].description` | no | Longer description, shown on the details page. |
| `apps[].category` | no | Groups apps in the Store sidebar. |
| `apps[].version` | no | Display metadata shown on the app's tile. `sync` apps detect updates by comparing the installed commit against the latest one on the tracked branch, not this string; `release` apps compare the latest release tag instead. |
| `apps[].icon` | no | Path **inside the repo** to a PNG/SVG icon, or `null`. |
| `apps[].subdir` | no | Folder within the repo the app lives in (default `.`). |
| `apps[].entrypoint` | no | Main script, relative to `subdir`. |
| `apps[].run` | no | Command to launch the app, run from `subdir`. |
| `apps[].requirements` | no | Requirements file, relative to `subdir`. |
| `apps[].update_method` | no | How the app updates — see below. Default `sync`. |
| `apps[].maintained` | no | `false` shows an *unmaintained* badge. |
| `apps[].mime_types` | no | List of MIME types (e.g. `["image/png", "image/jpeg"]`) the app can open. When set, **Add to menu** also writes a `MimeType=` line into the generated `.desktop` file, forwards the opened file(s) to `run` via `%F`, and registers the launcher as the default handler for each type with `xdg-mime`. |
| `apps[].branch` | no | Per-app override of the top-level `branch`. |

### `update_method`

Each app declares how Code Master should keep it up to date:

| Value | Meaning |
|-------|---------|
| `sync` *(default)* | **Replace it with the latest code.** Code Master tracks the metadata branch and `git pull`s the newest commits. The displayed version is the metadata `version`, but an update is offered whenever the branch's latest commit differs from the one you have installed — so it keeps tracking "latest code" even if a publisher pushes without bumping `version`. |
| `release` | **Download the latest release.** Code Master reads the repo's latest GitHub *release* (`releases/latest`), installs the code at that tag, and offers an update when a newer release tag is published. The displayed version is the release tag. |

Use `release` for apps that cut tagged releases (e.g. JadivCalc, which already
checks GitHub release tags from inside the app); use `sync` for apps that are
meant to always run the newest code on the branch.

The icon and metadata are fetched from the **metadata branch** configured in
Settings (default `main`), so merge your `codemaster-metadata.json` into that
branch for the app to appear in the Store.

---

## Submitting an app without owning jan-tdy: the community catalog

You don't have to be jan-tdy, or get your repo merged into this project, to
show up in the Store. Drop a `codemaster-metadata.json`-shaped file into
[`catalog/`](catalog/) here, pointing at **your own** repository, and open a
pull request. See [`catalog/README.md`](catalog/README.md) for the exact
format and what installing a community app means for the person installing
it.

---

Made by JapySoft TDY · contact: j44soft@gmail.com
