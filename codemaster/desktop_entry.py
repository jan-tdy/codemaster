"""Desktop entry (.desktop launcher) construction helpers."""

import shlex


def desktop_exec_quote(s):
    """Escape a string for embedding inside a double-quoted Exec= value,
    per the Desktop Entry Specification's quoting rules."""
    return (s.replace("\\", "\\\\").replace("`", "\\`")
             .replace("$", "\\$").replace('"', '\\"'))


def desktop_exec_line(cwd, run, mime_types=None):
    """Build an Exec= value that changes into ``cwd`` itself instead of
    relying on the .desktop file's Path= key. Not every application menu /
    launcher (e.g. several dmenu-style launchers used on tiling window
    managers) honours Path=, which makes a launcher with a relative run
    command silently fail to open on those setups while working fine on a
    desktop environment that does honour it.

    When the app declares ``mime_types``, the file(s) the desktop
    environment opens it with are forwarded as arguments (via %F) so the
    app can actually open what was double-clicked, not just start empty."""
    shell_cmd = f"cd {shlex.quote(cwd)} && {run}"
    if mime_types:
        shell_cmd += ' "$@"'
        return f'sh -c "{desktop_exec_quote(shell_cmd)}" _ %F'
    return f'sh -c "{desktop_exec_quote(shell_cmd)}"'
