from __future__ import annotations

import ctypes
from dataclasses import dataclass

import win32process
import win32gui
import win32api
import win32con
from PIL import ImageGrab


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    process_id: int
    title: str
    rect: tuple[int, int, int, int]

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def _process_name(hwnd: int) -> str:
    try:
        _, process_id = win32process.GetWindowThreadProcessId(hwnd)
        access = win32con.PROCESS_QUERY_LIMITED_INFORMATION
        handle = win32api.OpenProcess(access, False, process_id)
        try:
            return win32process.GetModuleFileNameEx(handle, 0).rsplit("\\", 1)[-1]
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""


def find_window(title_fragment: str, process_name: str = "") -> GameWindow:
    matches: list[GameWindow] = []

    def visit(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        title_match = title_fragment.lower() in title.lower()
        process_match = bool(process_name) and _process_name(hwnd).lower() == process_name.lower()
        if not title_match and not process_match:
            return
        _, process_id = win32process.GetWindowThreadProcessId(hwnd)
        matches.append(GameWindow(
            hwnd=hwnd,
            process_id=process_id,
            title=title,
            rect=win32gui.GetWindowRect(hwnd),
        ))

    win32gui.EnumWindows(visit, None)
    if not matches:
        raise RuntimeError(
            f'No visible game window matched title "{title_fragment}" '
            f'or process "{process_name}".'
        )
    return max(matches, key=lambda item: item.width * item.height)


def is_foreground_window(window: GameWindow) -> bool:
    return win32gui.GetForegroundWindow() == window.hwnd


def capture_window(window: GameWindow):
    client_left, client_top = win32gui.ClientToScreen(window.hwnd, (0, 0))
    client_rect = win32gui.GetClientRect(window.hwnd)
    client_width = client_rect[2] - client_rect[0]
    client_height = client_rect[3] - client_rect[1]
    bbox = (
        client_left,
        client_top,
        client_left + client_width,
        client_top + client_height,
    )
    return ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
