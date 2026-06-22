from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image


class Diagnostics:
    def __init__(self, root: Path):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = root / timestamp
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.images_dir = self.run_dir / "images"
        self.images_dir.mkdir(exist_ok=True)
        self._counter = 0

        latest_path = root / "latest.txt"
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        latest_path.write_text(str(self.run_dir), encoding="utf-8")
        self.event("run_start", run_dir=str(self.run_dir))

    def event(self, event_type: str, **fields: Any) -> None:
        record = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "monotonic": round(time.monotonic(), 3),
            "event": event_type,
            **fields,
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str))
            handle.write("\n")

    def save_image(
        self,
        label: str,
        image: Image.Image,
        *,
        jpeg: bool = False,
    ) -> str:
        self._counter += 1
        safe_label = "".join(
            char if char.isalnum() or char in ("-", "_") else "_"
            for char in label
        ).strip("_")
        suffix = "jpg" if jpeg else "png"
        path = self.images_dir / f"{self._counter:04d}-{safe_label}.{suffix}"
        if jpeg:
            image.convert("RGB").save(path, format="JPEG", quality=82)
        else:
            image.save(path)
        return str(path)
