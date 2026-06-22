from __future__ import annotations

from PIL import Image


def _neutral_gray_count(image: Image.Image, y: int) -> int:
    width, _ = image.size
    x1 = 0
    x2 = round(width * 0.365)
    count = 0
    pixels = image.load()
    for x in range(x1, x2):
        r, g, b = pixels[x, y]
        if max(r, g, b) - min(r, g, b) < 35 and 110 < max(r, g, b) < 240:
            count += 1
    return count


def is_chat_panel_open(image: Image.Image) -> bool:
    """
    Detect the two long neutral-gray borders of the chat input at bottom-left.

    Coordinates are normalized, so this also works when the 3840x2160 game is
    captured at another scale.
    """
    width, height = image.size
    y_start = round(height * 0.88)
    y_end = round(height * 0.95)
    required = round(width * 0.27)
    strong_rows = [
        y for y in range(y_start, y_end)
        if _neutral_gray_count(image, y) >= required
    ]
    if len(strong_rows) < 4:
        return False
    return max(strong_rows) - min(strong_rows) >= round(height * 0.035)


def crop_chat_column(image: Image.Image) -> Image.Image:
    """Crop the entire left-side chat-history column, including sender labels."""
    width, height = image.size
    return image.crop((0, 0, round(width * 0.47), round(height * 0.89)))

