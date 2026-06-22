from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

import win32api
import win32con

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ellie_sky.capture import capture_window, enable_dpi_awareness, find_window
from ellie_sky.config import load_config
from ellie_sky.input_win import focus_window, send_chat_message


INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_MOVE = 0x0001

VK_BY_NAME = {
    "w": ord("W"),
    "a": ord("A"),
    "s": ord("S"),
    "d": ord("D"),
    "f": ord("F"),
    "g": ord("G"),
    "c": ord("C"),
    "space": win32con.VK_SPACE,
    "esc": win32con.VK_ESCAPE,
    "enter": win32con.VK_RETURN,
    "up": win32con.VK_UP,
    "down": win32con.VK_DOWN,
    "left": win32con.VK_LEFT,
    "right": win32con.VK_RIGHT,
}

EXTENDED_KEYS = {
    win32con.VK_UP,
    win32con.VK_DOWN,
    win32con.VK_LEFT,
    win32con.VK_RIGHT,
}


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


def send_scan(vk: int, keyup: bool = False) -> None:
    scan = win32api.MapVirtualKey(vk, 0)
    flags = KEYEVENTF_SCANCODE
    if keyup:
        flags |= KEYEVENTF_KEYUP
    if vk in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    item = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(
            ki=KEYBDINPUT(
                wVk=0,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=None,
            )
        ),
    )
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError()


def hold_key(name: str, seconds: float) -> None:
    vk = VK_BY_NAME[name.lower()]
    send_scan(vk, keyup=False)
    time.sleep(seconds)
    send_scan(vk, keyup=True)


def move_mouse(dx: int, dy: int) -> None:
    item = INPUT(
        type=INPUT_MOUSE,
        union=INPUT_UNION(
            mi=MOUSEINPUT(
                dx=dx,
                dy=dy,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE,
                time=0,
                dwExtraInfo=None,
            )
        ),
    )
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError()


def save_screenshot(window, label: str) -> Path:
    out = Path("state") / f"codex-play-{label}.png"
    out.parent.mkdir(exist_ok=True)
    capture_window(window).save(out)
    return out.resolve()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "actions",
        nargs="*",
        help="Actions like screenshot:label, hold:w:1.0, tap:f, wait:0.5.",
    )
    args = parser.parse_args()

    enable_dpi_awareness()
    config = load_config(Path("config.json"))
    window = find_window(config.game.window_title, config.game.process_name)
    focused = focus_window(window.hwnd)
    print(f"focused={focused} window={window.title!r} rect={window.rect}")

    for action in args.actions:
        parts = action.split(":")
        kind = parts[0].lower()
        if kind == "screenshot":
            label = parts[1] if len(parts) > 1 else str(int(time.time()))
            print(f"screenshot={save_screenshot(window, label)}")
        elif kind == "hold":
            hold_key(parts[1], float(parts[2]))
        elif kind == "tap":
            hold_key(parts[1], 0.08)
        elif kind == "mouse":
            move_mouse(int(parts[1]), int(parts[2]))
        elif kind == "chat":
            message = action[len("chat:"):]
            if not send_chat_message(window.hwnd, message):
                raise RuntimeError("Could not focus the game window for chat.")
        elif kind == "wait":
            time.sleep(float(parts[1]))
        else:
            raise ValueError(f"Unknown action: {action}")


if __name__ == "__main__":
    main()
