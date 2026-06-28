from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes


PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE_PROTECTIONS = {
    0x02,  # PAGE_READONLY
    0x04,  # PAGE_READWRITE
    0x08,  # PAGE_WRITECOPY
    0x20,  # PAGE_EXECUTE_READ
    0x40,  # PAGE_EXECUTE_READWRITE
    0x80,  # PAGE_EXECUTE_WRITECOPY
}


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


def readable_region(info: MemoryBasicInformation) -> bool:
    protection = info.Protect & 0xFF
    return (
        info.State == MEM_COMMIT
        and protection in READABLE_PROTECTIONS
        and not info.Protect & (PAGE_GUARD | PAGE_NOACCESS)
    )


def printable_context(
    data: bytes,
    found: int,
    pattern_length: int,
    encoding: str,
    context_bytes: int,
    escape_context: bool = False,
) -> str:
    start = max(0, found - context_bytes)
    end = min(len(data), found + pattern_length + context_bytes)
    if encoding == "utf-16-le" and (found - start) % 2:
        start += 1
    decoded = data[start:end].decode(encoding, errors="replace")
    printable = "".join(
        character if character.isprintable() else "·"
        for character in decoded
    )
    if escape_context:
        return printable.encode("unicode_escape").decode("ascii")
    return printable


def scan_process(
    pid: int,
    text: str,
    max_matches: int,
    context_bytes: int = 0,
    near_addresses: list[int] | None = None,
    near_distance: int = 0,
    escape_context: bool = False,
) -> int:
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
        False,
        pid,
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    patterns = {
        "utf-8": text.encode("utf-8"),
        "utf-16-le": text.encode("utf-16-le"),
    }
    longest = max(len(pattern) for pattern in patterns.values())
    chunk_size = 4 * 1024 * 1024
    address = 0
    matches = 0
    scanned_bytes = 0

    try:
        info = MemoryBasicInformation()
        while kernel32.VirtualQueryEx(
            handle,
            ctypes.c_void_p(address),
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            base = int(info.BaseAddress or 0)
            region_size = int(info.RegionSize)
            next_address = base + region_size
            if next_address <= address:
                break

            if readable_region(info):
                offset = 0
                overlap = b""
                while offset < region_size:
                    requested = min(chunk_size, region_size - offset)
                    buffer = ctypes.create_string_buffer(requested)
                    bytes_read = ctypes.c_size_t()
                    ok = kernel32.ReadProcessMemory(
                        handle,
                        ctypes.c_void_p(base + offset),
                        buffer,
                        requested,
                        ctypes.byref(bytes_read),
                    )
                    if ok and bytes_read.value:
                        data = overlap + buffer.raw[:bytes_read.value]
                        data_base = base + offset - len(overlap)
                        scanned_bytes += bytes_read.value
                        for encoding, pattern in patterns.items():
                            start = 0
                            while True:
                                found = data.find(pattern, start)
                                if found < 0:
                                    break
                                absolute_address = data_base + found
                                if near_addresses and not any(
                                    abs(absolute_address - target) <= near_distance
                                    for target in near_addresses
                                ):
                                    start = found + max(1, len(pattern))
                                    continue
                                print(
                                    f"MATCH encoding={encoding} "
                                    f"address=0x{absolute_address:016X}"
                                )
                                if context_bytes:
                                    print(
                                        "  CONTEXT "
                                        + printable_context(
                                            data,
                                            found,
                                            len(pattern),
                                            encoding,
                                            context_bytes,
                                            escape_context,
                                        )
                                    )
                                matches += 1
                                if matches >= max_matches:
                                    print(
                                        f"Stopped after {matches} matches; "
                                        f"scanned {scanned_bytes / 1024 / 1024:.1f} MiB."
                                    )
                                    return matches
                                start = found + max(1, len(pattern))
                        overlap = data[-max(0, longest - 1):]
                    else:
                        overlap = b""
                    offset += requested

            address = next_address
    finally:
        kernel32.CloseHandle(handle)

    print(
        f"Finished: {matches} match(es), "
        f"scanned {scanned_bytes / 1024 / 1024:.1f} MiB."
    )
    return matches


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(
        description="Read-only UTF-8/UTF-16 text search in a Windows process.",
    )
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--max-matches", type=int, default=50)
    parser.add_argument("--context-bytes", type=int, default=0)
    parser.add_argument(
        "--near-address",
        action="append",
        default=[],
        type=lambda value: int(value, 0),
    )
    parser.add_argument("--near-distance", type=lambda value: int(value, 0), default=0)
    parser.add_argument("--escape-context", action="store_true")
    args = parser.parse_args()
    scan_process(
        args.pid,
        args.text,
        args.max_matches,
        context_bytes=max(0, args.context_bytes),
        near_addresses=args.near_address,
        near_distance=max(0, args.near_distance),
        escape_context=args.escape_context,
    )


if __name__ == "__main__":
    main()
