from __future__ import annotations

import ctypes
import json
import logging
import queue
import re
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass


PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE_PROTECTIONS = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
CHAT_MARKER = b'{"type":"chat"'
WEBSOCKET_MARKER = b"live-ws-sky-merge.game.163.com"
FRIEND_PATTERN = re.compile(
    rb'"friend_id"\s*:\s*"([0-9a-f-]{36})".{0,512}?'
    rb'"nickname"\s*:\s*"((?:\\.|[^"\\])*)"',
    flags=re.DOTALL | re.IGNORECASE,
)
MAX_JSON_BYTES = 8192
SCAN_CHUNK_BYTES = 4 * 1024 * 1024
RANGE_RADIUS_BYTES = 2 * 1024 * 1024


class MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MemoryBasicInformation),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL


@dataclass(frozen=True)
class MemoryRange:
    start: int
    size: int


@dataclass(frozen=True)
class MemoryChatPacket:
    sender_id: str
    text: str
    msg_id: str = ""
    result: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.sender_id, self.msg_id, self.text)


@dataclass(frozen=True)
class MemoryChatEvent:
    sender_id: str
    sender: str
    text: str
    msg_id: str = ""


def _decode_json_string(raw: bytes) -> str:
    try:
        return json.loads(b'"' + raw + b'"')
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")


def extract_friend_names(data: bytes) -> dict[str, str]:
    names: dict[str, str] = {}
    for match in FRIEND_PATTERN.finditer(data):
        sender_id = match.group(1).decode("ascii").lower()
        nickname = _decode_json_string(match.group(2)).strip()
        if nickname:
            names[sender_id] = nickname
    return names


def _chat_objects(data: bytes):
    cursor = 0
    decoder = json.JSONDecoder()
    while True:
        offset = data.find(CHAT_MARKER, cursor)
        if offset < 0:
            return
        candidate = data[offset:offset + MAX_JSON_BYTES]
        try:
            decoded = candidate.decode("utf-8", errors="ignore")
            value, _ = decoder.raw_decode(decoded)
        except json.JSONDecodeError:
            cursor = offset + len(CHAT_MARKER)
            continue
        if isinstance(value, dict) and value.get("type") == "chat":
            yield offset, value
        cursor = offset + len(CHAT_MARKER)


def extract_chat_packets(data: bytes) -> list[MemoryChatPacket]:
    packets: list[MemoryChatPacket] = []
    for _, value in _chat_objects(data):
        sender_id = str(value.get("sender_id", "")).strip().lower()
        text = str(value.get("msg", "")).strip()
        if not sender_id or not text:
            continue
        packets.append(MemoryChatPacket(
            sender_id=sender_id,
            text=text,
            msg_id=str(value.get("msg_id", "")).strip(),
            result=str(value.get("result", "")).strip().lower(),
        ))
    return packets


def resolve_packet(
    packet: MemoryChatPacket,
    local_player_id: str,
    primary_user_id: str,
    primary_user_name: str,
    friend_names: dict[str, str],
) -> MemoryChatEvent | None:
    local_id = local_player_id.strip().lower()
    primary_id = primary_user_id.strip().lower()
    if packet.result == "success" or (local_id and packet.sender_id == local_id):
        return None
    if primary_id and packet.sender_id == primary_id:
        sender = primary_user_name
    else:
        sender = friend_names.get(packet.sender_id)
        if not sender:
            return None
    return MemoryChatEvent(packet.sender_id, sender, packet.text, packet.msg_id)


def _readable_region(info: MemoryBasicInformation) -> bool:
    protection = info.Protect & 0xFF
    return (
        info.State == MEM_COMMIT
        and protection in READABLE_PROTECTIONS
        and not info.Protect & (PAGE_GUARD | PAGE_NOACCESS)
    )


class ProcessMemory:
    def __init__(self, pid: int):
        self.pid = pid
        self.handle = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
            False,
            pid,
        )
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def regions(self):
        address = 0
        info = MemoryBasicInformation()
        while kernel32.VirtualQueryEx(
            self.handle,
            ctypes.c_void_p(address),
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            base = int(info.BaseAddress or 0)
            size = int(info.RegionSize)
            next_address = base + size
            if next_address <= address:
                return
            if _readable_region(info):
                yield MemoryRange(base, size)
            address = next_address

    def read(self, start: int, size: int) -> bytes:
        if size <= 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(start),
            buffer,
            size,
            ctypes.byref(bytes_read),
        )
        if not ok or not bytes_read.value:
            return b""
        return buffer.raw[:bytes_read.value]


def _merge_ranges(ranges: list[MemoryRange]) -> list[MemoryRange]:
    merged: list[MemoryRange] = []
    for current in sorted(ranges, key=lambda item: item.start):
        if not merged or current.start > merged[-1].start + merged[-1].size:
            merged.append(current)
            continue
        previous = merged[-1]
        end = max(previous.start + previous.size, current.start + current.size)
        merged[-1] = MemoryRange(previous.start, end - previous.start)
    return merged


class MemoryChatReader:
    def __init__(
        self,
        local_player_id: str,
        primary_user_id: str,
        primary_user_name: str,
        poll_seconds: float = 0.2,
        friend_names: dict[str, str] | None = None,
    ):
        self.local_player_id = local_player_id.strip().lower()
        self.primary_user_id = primary_user_id.strip().lower()
        self.primary_user_name = primary_user_name
        self.poll_seconds = poll_seconds
        self.friend_names = {
            sender_id.strip().lower(): nickname
            for sender_id, nickname in (friend_names or {}).items()
            if sender_id.strip() and nickname.strip()
        }
        self.pid: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._events: queue.Queue[MemoryChatEvent] = queue.Queue()
        self._statuses: queue.Queue[dict] = queue.Queue()

    def ensure_process(self, pid: int) -> None:
        if self.pid == pid and self._thread and self._thread.is_alive():
            return
        self.stop()
        self.pid = pid
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(pid,),
            name="sky-memory-chat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self.pid = None

    def drain_events(self) -> list[MemoryChatEvent]:
        events: list[MemoryChatEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def drain_statuses(self) -> list[dict]:
        statuses: list[dict] = []
        while True:
            try:
                statuses.append(self._statuses.get_nowait())
            except queue.Empty:
                return statuses

    def _run(self, pid: int) -> None:
        memory: ProcessMemory | None = None
        try:
            self._statuses.put({"event": "memory_chat_discovery_started", "pid": pid})
            memory = ProcessMemory(pid)
            ranges, packets, discovered_names, scanned_bytes = self._discover(memory)
            while not ranges and not self._stop.is_set():
                self._statuses.put({
                    "event": "memory_chat_waiting_for_buffer",
                    "pid": pid,
                    "scanned_bytes": scanned_bytes,
                })
                if self._stop.wait(1.0):
                    return
                ranges, packets, discovered_names, scanned_bytes = self._discover(memory)
            friend_names = {**discovered_names, **self.friend_names}
            active_keys = {packet.key() for packet in packets}
            for packet in packets:
                if packet.result == "success" and not self.local_player_id:
                    self.local_player_id = packet.sender_id
            self._statuses.put({
                "event": "memory_chat_ready",
                "pid": pid,
                "ranges": len(ranges),
                "range_bytes": sum(item.size for item in ranges),
                "scanned_bytes": scanned_bytes,
                "baseline_packets": len(active_keys),
                "friend_names": len(friend_names),
                "local_player_id_known": bool(self.local_player_id),
            })
            while not self._stop.wait(self.poll_seconds):
                current_packets: dict[tuple[str, str, str], MemoryChatPacket] = {}
                successful_reads = 0
                updated_names: dict[str, str] = {}
                for item in ranges:
                    data = memory.read(item.start, item.size)
                    if not data:
                        continue
                    successful_reads += 1
                    for sender_id, nickname in extract_friend_names(data).items():
                        if friend_names.get(sender_id) != nickname:
                            friend_names[sender_id] = nickname
                            updated_names[sender_id] = nickname
                    for packet in extract_chat_packets(data):
                        current_packets[packet.key()] = packet
                if ranges and not successful_reads:
                    raise RuntimeError("All discovered memory chat ranges became unreadable.")
                if updated_names:
                    self._statuses.put({
                        "event": "memory_chat_friend_names_updated",
                        "names": updated_names,
                    })
                current_keys = set(current_packets)
                for key in current_keys - active_keys:
                    packet = current_packets[key]
                    if packet.result == "success" and not self.local_player_id:
                        self.local_player_id = packet.sender_id
                    event = resolve_packet(
                        packet,
                        self.local_player_id,
                        self.primary_user_id,
                        self.primary_user_name,
                        friend_names,
                    )
                    if event is not None:
                        self._events.put(event)
                    elif (
                        packet.result != "success"
                        and packet.sender_id != self.local_player_id
                        and packet.sender_id != self.primary_user_id
                        and packet.sender_id not in friend_names
                    ):
                        self._statuses.put({
                            "event": "memory_chat_unknown_sender_ignored",
                            "sender_id": packet.sender_id,
                            "text": packet.text,
                        })
                active_keys = current_keys
        except Exception as error:
            logging.exception("Memory chat reader failed.")
            self._statuses.put({
                "event": "memory_chat_error",
                "pid": pid,
                "error": repr(error),
            })
        finally:
            if memory is not None:
                memory.close()

    def _discover(
        self,
        memory: ProcessMemory,
    ) -> tuple[list[MemoryRange], list[MemoryChatPacket], dict[str, str], int]:
        ranges: list[MemoryRange] = []
        packets: dict[tuple[str, str, str], MemoryChatPacket] = {}
        friend_names: dict[str, str] = {}
        scanned_bytes = 0
        overlap_size = MAX_JSON_BYTES
        for region in memory.regions():
            offset = 0
            overlap = b""
            while offset < region.size and not self._stop.is_set():
                requested = min(SCAN_CHUNK_BYTES, region.size - offset)
                chunk = memory.read(region.start + offset, requested)
                if not chunk:
                    overlap = b""
                    offset += requested
                    continue
                scanned_bytes += len(chunk)
                data = overlap + chunk
                data_start = region.start + offset - len(overlap)
                friend_names.update(extract_friend_names(data))
                marker_cursor = 0
                while True:
                    marker_offset = data.find(WEBSOCKET_MARKER, marker_cursor)
                    if marker_offset < 0:
                        break
                    absolute = data_start + marker_offset
                    start = max(region.start, absolute - RANGE_RADIUS_BYTES)
                    end = min(
                        region.start + region.size,
                        absolute + RANGE_RADIUS_BYTES,
                    )
                    ranges.append(MemoryRange(start, end - start))
                    marker_cursor = marker_offset + len(WEBSOCKET_MARKER)
                for packet_offset, value in _chat_objects(data):
                    sender_id = str(value.get("sender_id", "")).strip().lower()
                    text = str(value.get("msg", "")).strip()
                    if not sender_id or not text:
                        continue
                    packet = MemoryChatPacket(
                        sender_id,
                        text,
                        str(value.get("msg_id", "")).strip(),
                        str(value.get("result", "")).strip().lower(),
                    )
                    packets[packet.key()] = packet
                    absolute = data_start + packet_offset
                    start = max(region.start, absolute - RANGE_RADIUS_BYTES)
                    end = min(
                        region.start + region.size,
                        absolute + RANGE_RADIUS_BYTES,
                    )
                    ranges.append(MemoryRange(start, end - start))
                overlap = data[-overlap_size:]
                offset += requested
        return _merge_ranges(ranges), list(packets.values()), friend_names, scanned_bytes
