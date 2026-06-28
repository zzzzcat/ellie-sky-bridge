from __future__ import annotations

import ctypes
import time

import win32api
import win32clipboard
import win32con
import win32gui
import win32process


KEYS = {
    "c": 0x43,
    "esc": win32con.VK_ESCAPE,
    "f": 0x46,
    "enter": win32con.VK_RETURN,
}

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION),
    ]


def focus_window(hwnd: int) -> bool:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    foreground = win32gui.GetForegroundWindow()
    current_thread = win32api.GetCurrentThreadId()
    target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
    foreground_thread = 0
    if foreground:
        foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground)

    attached_target = False
    attached_foreground = False
    try:
        # Releasing Alt allows SetForegroundWindow in cases where Windows'
        # foreground-lock policy would otherwise reject the request.
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(
            win32con.VK_MENU,
            0,
            win32con.KEYEVENTF_KEYUP,
            0,
        )
        if target_thread != current_thread:
            attached_target = bool(
                win32process.AttachThreadInput(current_thread, target_thread, True)
            )
        if foreground_thread and foreground_thread not in {current_thread, target_thread}:
            attached_foreground = bool(
                win32process.AttachThreadInput(current_thread, foreground_thread, True)
            )
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetActiveWindow(hwnd)
        win32gui.SetFocus(hwnd)
    except Exception:
        # Windows may reject focus stealing even when the window is already
        # usable. Callers verify foreground state before sending input.
        pass
    finally:
        if attached_foreground:
            win32process.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target:
            win32process.AttachThreadInput(current_thread, target_thread, False)
    time.sleep(0.3)
    return win32gui.GetForegroundWindow() == hwnd


def press_key(name: str) -> None:
    vk = KEYS[name.lower()]
    scan = win32api.MapVirtualKey(vk, 0)
    inputs = (INPUT * 2)(
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=scan,
                    dwFlags=KEYEVENTF_SCANCODE,
                    time=0,
                    dwExtraInfo=None,
                )
            ),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=scan,
                    dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=None,
                )
            ),
        ),
    )
    sent = ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))
    if sent != 2:
        raise ctypes.WinError()


def pause_hotkey_pressed() -> bool:
    return (
        bool(win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000)
        and bool(win32api.GetAsyncKeyState(win32con.VK_SHIFT) & 0x8000)
        and bool(win32api.GetAsyncKeyState(win32con.VK_F12) & 0x8000)
    )


def _set_clipboard(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def paste_text(text: str) -> None:
    _set_clipboard(text)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord("V"), 0, 0, 0)
    win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def send_chat_message(hwnd: int, text: str) -> bool:
    if not focus_window(hwnd):
        return False
    press_key("enter")
    time.sleep(0.35)
    paste_text(text)
    time.sleep(0.25)
    press_key("enter")
    return True
