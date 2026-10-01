"""Experimental, read-only SQLCipher codec lookup in a selected KakaoTalk PID.

Only the explicitly supplied database salt is considered. No memory dump,
injection, debugging, key file, automatic profile enumeration, or key logging.
Layout observed with KakaoTalk 26.8.1.5315 and SQLCipher 4.6-style 64-bit ctx.
"""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import struct
import sys
import time

from .crypto import decrypt_page


def process_identity(pid: int) -> tuple[str, int]:
    """Check a running selected process without reading its memory.

    Pin the actual image path and Windows creation FILETIME so a recycled PID
    cannot reuse a previous process's cached database key. This does not detect
    an account logout while the same KakaoTalk process remains running.
    """
    if sys.platform != "win32" or c.sizeof(c.c_void_p) != 8:
        raise RuntimeError("Live reader requires 64-bit Windows Python")
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD,
                                                w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    kernel.GetProcessTimes.argtypes = [w.HANDLE, c.POINTER(w.FILETIME),
                                      c.POINTER(w.FILETIME), c.POINTER(w.FILETIME),
                                      c.POINTER(w.FILETIME)]
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.GetExitCodeProcess.restype = w.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError("Selected process is unavailable")
    try:
        image, length = c.create_unicode_buffer(32768), w.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, c.byref(length)):
            raise OSError("Cannot verify process image")
        if Path(image.value).name.casefold() != "kakaotalk.exe":
            raise ValueError("Selected PID is not KakaoTalk.exe")
        created, exited, cpu_kernel, cpu_user = (w.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, c.byref(created), c.byref(exited),
                                      c.byref(cpu_kernel), c.byref(cpu_user)):
            raise OSError("Cannot verify process creation time")
        exit_code = w.DWORD()
        if not kernel.GetExitCodeProcess(handle, c.byref(exit_code)):
            raise OSError("Cannot verify process state")
        if exit_code.value != 259:  # STILL_ACTIVE
            raise RuntimeError("Selected process has exited")
        stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return str(Path(image.value)).casefold(), stamp
    finally:
        kernel.CloseHandle(handle)


def discover_pids(executable: Path) -> list[int]:
    """Find running PIDs for an explicitly supplied absolute executable path.

    Process-name matching only selects candidates. The actual image path must
    match exactly, which excludes a separate sandbox installation. No account,
    profile or process memory is inspected.
    """
    if sys.platform != "win32" or c.sizeof(c.c_void_p) != 8:
        raise RuntimeError("Live reader requires 64-bit Windows Python")
    if not executable.is_absolute() or executable.name.casefold() != "kakaotalk.exe":
        raise ValueError("Set an absolute KakaoTalk.exe path")
    expected = str(executable.resolve()).casefold()
    kernel = c.WinDLL("kernel32", use_last_error=True)

    class ProcessEntry(c.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                    ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", c.c_size_t),
                    ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                    ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG),
                    ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]

    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = w.BOOL
    kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    kernel.Process32NextW.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == c.c_void_p(-1).value:
        raise OSError("Cannot enumerate process candidates")
    found = []
    try:
        entry = ProcessEntry()
        entry.dwSize = c.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, c.byref(entry))
        while available:
            if entry.szExeFile.casefold() == "kakaotalk.exe":
                try:
                    image, _ = process_identity(entry.th32ProcessID)
                    if image == expected:
                        found.append(entry.th32ProcessID)
                except (OSError, RuntimeError, ValueError):
                    pass
            available = kernel.Process32NextW(snapshot, c.byref(entry))
        return sorted(set(found))
    finally:
        kernel.CloseHandle(snapshot)


def recover_keys(pid: int, page_one: bytes, timeout=25.0) -> tuple[bytes, bytes]:
    if sys.platform != "win32" or c.sizeof(c.c_void_p) != 8:
        raise RuntimeError("Live reader requires 64-bit Windows Python")
    if len(page_one) != 4096:
        raise ValueError("Expected a complete first database page")
    kernel = c.WinDLL("kernel32", use_last_error=True)

    class MBI(c.Structure):
        _fields_ = [("base", c.c_void_p), ("allocation", c.c_void_p),
                    ("allocation_protect", w.DWORD), ("partition", w.WORD),
                    ("size", c.c_size_t), ("state", w.DWORD),
                    ("protect", w.DWORD), ("kind", w.DWORD)]

    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.VirtualQueryEx.argtypes = [w.HANDLE, c.c_void_p, c.POINTER(MBI), c.c_size_t]
    kernel.VirtualQueryEx.restype = c.c_size_t
    kernel.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p,
                                       c.c_size_t, c.POINTER(c.c_size_t)]
    kernel.ReadProcessMemory.restype = w.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD,
                                                w.LPWSTR, c.POINTER(w.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise OSError("Cannot read selected process")

    def read(address, size):
        if not address:
            return None
        buffer, done = c.create_string_buffer(size), c.c_size_t()
        ok = kernel.ReadProcessMemory(handle, c.c_void_p(address), buffer,
                                      size, c.byref(done))
        return buffer.raw if ok and done.value == size else None

    try:
        image, length = c.create_unicode_buffer(32768), w.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, c.byref(length)):
            raise OSError("Cannot verify process image")
        if Path(image.value).name.casefold() != "kakaotalk.exe":
            raise ValueError("Selected PID is not KakaoTalk.exe")
        signature = struct.pack("<5I", 2, 16, 32, 16, 16)
        address, scanned, started = 0, 0, time.monotonic()
        while time.monotonic() - started < timeout and scanned < 300 * 1024 * 1024:
            info = MBI()
            if not kernel.VirtualQueryEx(handle, address, c.byref(info), c.sizeof(info)):
                break
            base, size = info.base or 0, info.size
            if size == 0 or base + size <= address:
                break
            address = base + size
            if (info.state != 0x1000 or info.kind != 0x20000 or info.protect & 0x100
                    or info.protect & 0xFF not in (2, 4, 8, 32, 64, 128)):
                continue
            for offset in range(0, size, 1024 * 1024):
                if time.monotonic() - started >= timeout or scanned >= 300 * 1024 * 1024:
                    break
                take = min(1024 * 1024 + 256, size - offset)
                data = read(base + offset, take)
                scanned += take
                if not data:
                    continue
                hit = data.find(signature)
                while hit >= 0:
                    raw = read(base + offset + hit - 8, 128)
                    if raw:
                        fields = struct.unpack_from("<15I", raw)
                        if (fields[7], fields[9], fields[10]) == (4096, 80, 64):
                            pointers = struct.unpack_from("<8Q", raw, 64)
                            if read(pointers[0], 16) == page_one[:16]:
                                for context in pointers[4:6]:
                                    cipher = read(context, 40)
                                    if not cipher:
                                        continue
                                    _, _, kp, mp, _, _ = struct.unpack("<ii4Q", cipher)
                                    key, mac = read(kp, 32), read(mp, 32)
                                    if key and mac:
                                        try:
                                            decrypt_page(page_one, 1, key, mac)
                                        except ValueError:
                                            continue
                                        return key, mac
                    hit = data.find(signature, hit + 1)
        raise RuntimeError("Matching DB key unavailable; open its chat and verify PID")
    finally:
        kernel.CloseHandle(handle)
