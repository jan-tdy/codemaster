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
    if ! python3 -c "import PyQt5" >/dev/null 2>&1; then
        echo "Warning: PyQt5 is not installed. Install it with:" >&2
        echo "    pip install PyQt5 requests" >&2
    fi

    mkdir -p "$APP_DIR" "$ICON_DIR"
    install -m 0644 "$ASSETS_DIR/$ICON_NAME" "$ICON_DIR/$ICON_NAME"

    # Substitute the placeholder with the real repo path.
    local escaped_repo_dir
    escaped_repo_dir=$(printf '%s\n' "$REPO_DIR" | sed 's/[&|\]/\\&/g')
    sed "s|__INSTALL_DIR__|$escaped_repo_dir|g" \
        "$ASSETS_DIR/$DESKTOP_FILE" > "$APP_DIR/$DESKTOP_FILE"
    chmod 0644 "$APP_DIR/$DESKTOP_FILE"

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
