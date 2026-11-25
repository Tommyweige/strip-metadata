"""Windows integration: Explorer right-click menu and single-instance forwarding.

The right-click menu is written under HKEY_CURRENT_USER only (no admin rights).
Explorer starts one process per selected file, so a running window listens on a
local socket and later processes hand their paths to it and exit.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .core import IMAGE_EXT, VIDEO_EXT

VERB = "StripMetadata"
MENU_TEXT = "清除相片資訊"
FILE_EXTS = sorted(IMAGE_EXT | VIDEO_EXT)
PORT = 47911  # localhost only
MAGIC = b"STRIPMETA1"


# ---------------------------------------------------------------- right-click menu
def _menu_keys() -> list[tuple[str, str]]:
    """(registry key under HKCU, argument placeholder) for every place the verb appears."""
    keys = [(rf"Software\Classes\Directory\shell\{VERB}", "%1"),
            (rf"Software\Classes\Directory\Background\shell\{VERB}", "%V")]
    keys += [(rf"Software\Classes\SystemFileAssociations\{ext}\shell\{VERB}", "%1") for ext in FILE_EXTS]
    return keys


def exe_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else sys.executable}" -m strip_metadata'


def install_menu(command: str | None = None, root=None) -> None:
    import winreg
    root = root if root is not None else winreg.HKEY_CURRENT_USER
    command = command or exe_command()
    icon = command.split('"')[1] if command.startswith('"') else command
    for key, arg in _menu_keys():
        with winreg.CreateKey(root, key) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, MENU_TEXT)
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(root, key + r"\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'{command} "{arg}"')


def uninstall_menu(root=None) -> None:
    import winreg
    root = root if root is not None else winreg.HKEY_CURRENT_USER
    for key, _ in _menu_keys():
        for sub in (key + r"\command", key):
            try:
                winreg.DeleteKey(root, sub)
            except FileNotFoundError:
                pass


def menu_installed(root=None) -> bool:
    import winreg
    root = root if root is not None else winreg.HKEY_CURRENT_USER
    try:
        winreg.OpenKey(root, _menu_keys()[0][0]).Close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- single instance
def forward_to_running(paths: list[str], port: int = PORT) -> bool:
    """Send paths to an already-open window. True if one took them."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as s:
            s.sendall(MAGIC + json.dumps(paths).encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            return s.recv(2) == b"OK"
    except OSError:
        return False


class InstanceServer:
    """Accepts paths from later launches and passes them to `on_paths` (called on a worker thread)."""

    def __init__(self, on_paths: Callable[[list[str]], None], port: int = PORT):
        self.on_paths = on_paths
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.sock.bind(("127.0.0.1", port))  # raises OSError if another window owns it
        self.sock.listen(16)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(3)
                data = b""
                try:
                    while chunk := conn.recv(65536):
                        data += chunk
                        if len(data) > 4_000_000:
                            break
                    if data.startswith(MAGIC):
                        paths = json.loads(data[len(MAGIC):].decode("utf-8"))
                        if isinstance(paths, list) and all(isinstance(p, str) for p in paths):
                            conn.sendall(b"OK")
                            self.on_paths(paths)
                except (OSError, ValueError):
                    pass

    def close(self) -> None:
        self.sock.close()
