#!/usr/bin/env bash
# Install (or uninstall) the Jadiv Code Master desktop launcher on Linux.
#
#   ./install-launcher.sh            # install for the current user
#   ./install-launcher.sh --uninstall
#
# Installs into the per-user XDG locations (no root required):
#   ~/.local/share/applications/                  – the .desktop launcher
#   ~/.local/share/icons/hicolor/scalable/apps/   – the icon
set -euo pipefail

# Absolute path to this repository (where jadiv_code_master.py lives).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS_DIR="$REPO_DIR/assets"

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

DESKTOP_FILE=codemaster.desktop
ICON_NAME=codemaster.svg

update_caches() {
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 \
        && gtk-update-icon-cache -f -i -t \
            "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" \
            >/dev/null 2>&1 || true
}

do_uninstall() {
    rm -f "$APP_DIR/$DESKTOP_FILE"
    rm -f "$ICON_DIR/$ICON_NAME"
    update_caches
    echo "Removed Jadiv Code Master launcher."
}

do_install() {
    # Sanity check: make sure the app and its dependencies are reachable.
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Error: python3 is not installed." >&2
        exit 1
    fi
    if ! python3 -c "import PyQt5, requests" >/dev/null 2>&1; then
        # Prefer the distro's own packages: they don't fight PEP 668's
        # "externally-managed-environment" restriction, so there's no need
        # for pip's --break-system-packages override, which can destabilize
        # the system Python. Plain "pip" can also resolve to a different
        # interpreter than the "python3" the .desktop launcher runs
        # (Exec=python3 ...), so a package it installs may be invisible to
        # the app; "python3 -m pip" avoids that.
        echo "Warning: PyQt5 or requests is not installed. Install them with:" >&2
        echo "    sudo apt install python3-pyqt5 python3-requests" >&2
        echo "or, if you prefer pip:" >&2
        printf '    python3 -m pip install --user -r %q\n' \
            "$REPO_DIR/requirements.txt" >&2
    fi

    mkdir -p "$APP_DIR" "$ICON_DIR"
    install -m 0644 "$ASSETS_DIR/$ICON_NAME" "$ICON_DIR/$ICON_NAME"

    # Substitute the placeholder with the real repo path. python3 (verified
    # above) does a literal replacement that is safe for any characters in the
    # path — spaces, backslashes, ampersands, etc.
    python3 -c 'import sys; sys.stdout.write(sys.stdin.read().replace("__INSTALL_DIR__", sys.argv[1]))' \
        "$REPO_DIR" < "$ASSETS_DIR/$DESKTOP_FILE" > "$APP_DIR/$DESKTOP_FILE"
    # Executable, not just readable: some file managers (e.g. Thunar) treat a
    # non-executable .desktop file as untrusted and offer to import it as a
    # panel launcher instead of running it, which fails with a confusing
    # "Failed to add a plugin to the panel" D-Bus error unrelated to this app.
    chmod 0755 "$APP_DIR/$DESKTOP_FILE"

    update_caches
    echo "Installed Jadiv Code Master launcher into $APP_DIR"
    echo "Look for 'Jadiv Code Master' in your application menu."
}

case "${1:-}" in
    "")            do_install ;;
    -u|--uninstall) do_uninstall ;;
    -h|--help)
        echo "Usage: $0 [options]"
        echo "  (no args)        Install the launcher and icon"
        echo "  -u, --uninstall  Uninstall the launcher and icon"
        echo "  -h, --help       Show this help message"
        ;;
    *)
        echo "Unknown option: $1" >&2
        echo "Try '$0 --help'." >&2
        exit 1
        ;;
esac
