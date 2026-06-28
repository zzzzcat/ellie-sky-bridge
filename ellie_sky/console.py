from __future__ import annotations

import ctypes
import logging
import os
import sys


RESET = "\033[0m"
COLORS = {
    "pink": "\033[95m",
    "cyan": "\033[96m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "dim": "\033[2m",
}
_color_enabled = False


def _enable_windows_ansi() -> bool:
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def configure_console() -> None:
    global _color_enabled
    _color_enabled = bool(sys.stdout.isatty() and _enable_windows_ansi())
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleTitleW("Ellie Sky Bridge")
        except (AttributeError, OSError):
            pass


def colored(text: str, color: str) -> str:
    if not _color_enabled:
        return text
    return f"{COLORS[color]}{text}{RESET}"


class BridgeLogFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: "dim",
        logging.INFO: "cyan",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "red",
    }

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        color = self.LEVEL_COLORS.get(record.levelno)
        return colored(rendered, color) if color else rendered


def configure_logging() -> None:
    configure_console()
    handler = logging.StreamHandler()
    handler.setFormatter(BridgeLogFormatter(
        "%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def banner_lines(
    mode: str,
    model: str,
    memory_enabled: bool,
    user_name: str,
) -> list[str]:
    memory = "READ-ONLY MEMORY" if memory_enabled else "VISUAL FALLBACK"
    return [
        "",
        "+----------------------------------------------------------+",
        "|                         (\\_/)                            |",
        "|                         (o.o)                            |",
        "|                         /|_|\\                            |",
        "|                                                          |",
        "|                 E L L I E   S K Y   B R I D G E          |",
        "|                 pink bunny link // online                |",
        "+----------------------------------------------------------+",
        f"  MODE    {mode}",
        f"  CHAT    {memory}",
        f"  USER    {user_name}",
        f"  VISION  {model}",
        "  PAUSE   Ctrl+Shift+F12",
        "",
    ]


def print_banner(
    mode: str,
    model: str,
    memory_enabled: bool,
    user_name: str,
) -> None:
    lines = banner_lines(mode, model, memory_enabled, user_name)
    for index, line in enumerate(lines):
        if 1 <= index <= 8:
            print(colored(line, "pink"))
        elif line.startswith("  MODE"):
            print(colored(line, "yellow" if "DRY" in mode else "green"))
        elif line.startswith(("  CHAT", "  USER", "  VISION")):
            print(colored(line, "cyan"))
        else:
            print(colored(line, "dim"))


def status(label: str, message: str, color: str = "cyan") -> None:
    tag = colored(f"[{label:<7}]", color)
    print(f"{tag} {message}", flush=True)
