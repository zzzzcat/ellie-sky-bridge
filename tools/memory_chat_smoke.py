from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ellie_sky.memory_chat import MemoryChatReader


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--local-player-id", required=True)
    parser.add_argument("--primary-user-id", required=True)
    parser.add_argument("--primary-user-name", default="哥哥")
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    args = parser.parse_args()

    reader = MemoryChatReader(
        args.local_player_id,
        args.primary_user_id,
        args.primary_user_name,
        poll_seconds=0.2,
    )
    reader.ensure_process(args.pid)
    deadline = time.monotonic() + args.wait_seconds
    try:
        while time.monotonic() < deadline:
            for status in reader.drain_statuses():
                print(f"STATUS {status}", flush=True)
            for event in reader.drain_events():
                print(f"MESSAGE {event}", flush=True)
            time.sleep(0.1)
    finally:
        reader.stop()


if __name__ == "__main__":
    main()
