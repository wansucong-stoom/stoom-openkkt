"""Read-only SQLite VFS over an authenticated, encrypted DB/WAL snapshot.

Only pages requested by SQLite are decrypted; no complete plaintext database or
plaintext temporary file is created. A page may contain adjacent message rows.
"""
from contextlib import contextmanager
import uuid

import apsw

from .crypto import PAGE, committed_frames, decrypt_page


class EncryptedSnapshot:
    def __init__(self, data: bytes, wal: bytes):
        if not data or len(data) % PAGE:
            raise ValueError("Database must contain complete SQLCipher pages")
        frames, size, self.report = committed_frames(wal)
        original = len(data) // PAGE
        maximum = max((n for n, _ in frames), default=0)
        self.size = size or original
        if self.size > max(original, maximum) or self.size * PAGE > 512 * 1024 * 1024:
            raise ValueError("Invalid or oversized WAL commit")
        self.data = data
        self.overlay = {n: page for n, page in frames if n <= self.size}
        self.encrypted_bytes = len(data) + len(wal)
        self.keys = None
        self.verified = set()
        self.report.update(wal_applied=bool(frames), decryption="on_demand_pages")

    def encrypted_page(self, number):
        if not 1 <= number <= self.size:
            raise ValueError("Page outside snapshot")
        page = self.overlay.get(number)
        if page is None:
            page = self.data[(number - 1) * PAGE:number * PAGE]
        if len(page) != PAGE:
            raise ValueError("Missing committed snapshot page")
        return page

    def unlock(self, keys):
        decrypt_page(self.encrypted_page(1), 1, *keys)
        self.keys = keys

    def read(self, amount, offset):
        if offset < 0 or amount < 0 or offset + amount > self.size * PAGE:
            raise ValueError("Read outside snapshot")
        result = bytearray()
        while amount:
            number, within = divmod(offset, PAGE)
            number += 1
            plain = decrypt_page(self.encrypted_page(number), number, *self.keys)
            self.verified.add(number)
            if number == 1:
                # Committed WAL is already overlaid; this private view is immutable.
                plain = plain[:18] + b"\x01\x01" + plain[20:]
            take = min(amount, PAGE - within)
            result.extend(plain[within:within + take])
            amount -= take
            offset += take
        return bytes(result)

    def close(self):
        self.keys = None
        self.data = b""
        self.overlay.clear()


class SnapshotFile:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def xRead(self, amount, offset):
        return self.snapshot.read(amount, offset)

    def xFileSize(self):
        return self.snapshot.size * PAGE

    def xClose(self):
        pass

    def xLock(self, level):
        pass

    def xUnlock(self, level):
        pass

    def xCheckReservedLock(self):
        return False

    def xFileControl(self, op, pointer):
        return False

    def xSectorSize(self):
        return PAGE

    def xDeviceCharacteristics(self):
        return apsw.SQLITE_IOCAP_IMMUTABLE

    def xWrite(self, data, offset):
        raise apsw.ReadOnlyError("Snapshot is read-only")

    def xTruncate(self, size):
        raise apsw.ReadOnlyError("Snapshot is read-only")

    def xSync(self, flags):
        pass


class SnapshotVFS(apsw.VFS):
    def __init__(self, snapshot):
        self.name = "openkkt_snapshot_" + uuid.uuid4().hex
        self.snapshot = snapshot
        super().__init__(self.name, "")

    def xOpen(self, name, flags):
        if not flags[0] & apsw.SQLITE_OPEN_MAIN_DB or flags[0] & apsw.SQLITE_OPEN_READWRITE:
            raise apsw.ReadOnlyError("Only the immutable main snapshot is available")
        flags[1] = apsw.SQLITE_OPEN_READONLY
        return SnapshotFile(self.snapshot)

    def xAccess(self, pathname, flags):
        return False


@contextmanager
def encrypted_connection(snapshot):
    vfs = SnapshotVFS(snapshot)
    con = None
    try:
        con = apsw.Connection("openkkt-encrypted-snapshot", vfs=vfs.name,
                              flags=apsw.SQLITE_OPEN_READONLY)
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("PRAGMA cache_size=-1024")
        yield con
    except apsw.Error:
        raise ValueError("Encrypted snapshot query failed") from None
    finally:
        if con is not None:
            con.close()
        vfs.unregister()
