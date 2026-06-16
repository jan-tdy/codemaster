# Jadiv Code Master

A desktop **app store** for [jan-tdy](https://github.com/jan-tdy) applications.

Code Master scans every `jan-tdy` GitHub repository, and for each one that
ships a `codemaster-metadata.json` file it lists the apps that repository
publishes. A single repository can publish several apps — for example
[`devcontrolenterpise`](https://github.com/jan-tdy/devcontrolenterpise)
publishes the Telescope Cover, Astrofoto, Atacama (C14) and DSLR apps.

![tabs: Store · Installed · Updates · Manual & Settings · Code Runner](https://img.shields.io/badge/tabs-Store%20%C2%B7%20Installed%20%C2%B7%20Updates%20%C2%B7%20Manual%20%C2%B7%20Code%20Runner-4f8cff?style=for-the-badge)

---

## Features

- **🛍 Store** — browse every published jan-tdy app, grouped by category, with
  icon, version and one-click **Install** (a `git clone` under the hood).
- **📲 Installed** — launch or remove what you installed, and install an app's
  Python dependencies (`pip install -r …`) when it declares a requirements file.
- **⟳ Updates** — installed apps whose published version is newer than the one
  you have are listed here; update them individually or all at once.
- **⚙ Manual & Settings** — register jan-tdy apps you installed **by hand** by
  pointing Code Master at a folder containing a `codemaster-metadata.json`
  file. Also configure the GitHub user, metadata branch and an optional token.
- **▶ Code Runner** — the small Python editor/runner from the classic Code
  Master, kept for quick snippets.

---

## Running

```bash
pip install PyQt5 requests        # PyQt5.QtSvg is needed for SVG icons
python3 jadiv_code_master.py
```

Code Master keeps its state in:

| Path | Purpose |
|------|---------|
| `~/.config/codemaster/config.json`    | GitHub user, branch, token, manual folders |
| `~/.config/codemaster/installed.json` | registry of installed apps |
| `~/.local/share/codemaster/apps/`     | cloned repositories of installed apps |

---

## Publishing an app: `codemaster-metadata.json`

To make a repository appear in the store, drop a `codemaster-metadata.json`
file in its **root**. One repo, one metadata file, any number of apps:

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
      "description": "Longer paragraph shown on the app's card.",
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

### Field reference

| Field | Required | Meaning |
|-------|----------|---------|
| `schema_version` | yes | Metadata format version (currently `1`). |
| `publisher` / `repo` | yes | GitHub owner and repository name. |
| `homepage` | no | Repo-level link used for the **Details** button. |
| `apps[]` | yes | One entry per app the repo publishes. |
| `apps[].id` | yes | Stable id, unique within the repo. |
| `apps[].name` | yes | Display name. |
| `apps[].tagline` | no | One-line summary shown on the card. |
| `apps[].description` | no | Longer description. |
| `apps[].category` | no | Used to group apps in the store. |
| `apps[].version` | no | Drives the **Updates** tab (string compare). |
| `apps[].icon` | no | Path **inside the repo** to a PNG/SVG icon, or `null`. |
| `apps[].subdir` | no | Folder within the repo the app lives in (default `.`). |
| `apps[].entrypoint` | no | Main script, relative to `subdir`. |
| `apps[].run` | no | Command to launch the app, run from `subdir`. |
| `apps[].requirements` | no | Requirements file, relative to `subdir`. |
| `apps[].update_method` | no | How the app updates — see below. Default `sync`. |
| `apps[].maintained` | no | `false` shows an *unmaintained* badge. |

### `update_method`

Each app declares how Code Master should keep it up to date:

| Value | Meaning |
|-------|---------|
| `sync` *(default)* | **Replace it with the latest code.** Code Master tracks the metadata branch and `git pull`s the newest commits. The displayed version is the metadata `version`, and an update is offered when that string changes. |
| `release` | **Download the latest release.** Code Master reads the repo's latest GitHub *release* (`releases/latest`), installs the code at that tag, and offers an update when a newer release tag is published. The displayed version is the release tag. |

Use `release` for apps that cut tagged releases (e.g. JadivCalc, which already
checks GitHub release tags from inside the app); use `sync` for apps that are
meant to always run the newest code on the branch.

The icon and metadata are fetched from the **metadata branch** configured in
Settings (default `main`), so merge your `codemaster-metadata.json` into that
branch for the app to appear in the store.

---

Made by JapySoft TDY · contact: j44soft@gmail.com
