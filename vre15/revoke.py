"""Residency follows authority: checkpoint on revocation.

The one VRE-1.5 mechanism that passed its pre-registered test (H15: 13/0,
p = 0.0002). It is a governance mechanism, not a performance one; H13 and
H14 (fewer missed deadlines) were not confirmed. See vre15/README.md.

A governed region is a span of process memory, page-aligned, mapped
copy-on-write (MAP_PRIVATE) over an image file that holds the tenant's
state. While authority holds, the tenant writes into private pages. When
authority ends, `revoke`:

1. finds every page the tenant modified under that authority: pagemap
   reports it as an anonymous (non-file) page that is present, or as
   swapped;
2. writes exactly those pages back into the image and fdatasyncs it;
3. maps the image over the region again, which drops every private page
   (resident or in swap) from the process;
4. asks the kernel to page out the now-clean file pages (MADV_PAGEOUT).

Crash safety: the write-back in step 2 goes through a redo log. The
modified pages are first written to `<image>.redo`, which is synced and only
then renamed into place, so a log either exists complete or not at all.
Then the image is updated and synced, and the log removed. `recover(image)`
(called by `map_image`, and by anything that maps an image) replays a
complete log left by a crash. Whatever point a process dies at, the image
after recovery holds either the whole old state or the whole new state,
never a mix (tests/test_vre15_crash.py kills a real process at each step).
A crash before the receipt is returned means no receipt was issued:
`recover` returns the receipt of what it replayed, so a supervisor can
record it (reconciliation, not silence).

Invariant, checked by `held_privately` and the tests: after `revoke`,
- no page of the region is present in or swapped from this process's page
  tables;
- the image holds the state exactly, synced to disk;
- reading the region afterwards faults that state back in unchanged.
Page-cache residency after step 4 is best-effort. H15 measured 89% of
revoked tenants with nothing resident 5 s after authority ended, against
9% under native reclaim.

Each revocation returns a receipt whose digest covers the page indices and
bytes written, and the runs of pages it wrote (vre15/evidence.py turns it
into a signed, witnessed record). Identical writes under identical authority give identical
digests, so the receipt is evidence of exactly what changed.

Linux only, Linux 5.4 or later (MADV_PAGEOUT). Reading pagemap flags needs
no privilege.
"""

import ctypes
import hashlib
import os
import struct
import time
from dataclasses import dataclass

PAGE = 4096
PROT_RW, MAP_PRIVATE, MAP_FIXED = 0x3, 0x02, 0x10
MADV_PAGEOUT = 21

_libc = ctypes.CDLL(None, use_errno=True)
_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
_libc.madvise.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]


class RegionError(OSError):
    pass


@dataclass(frozen=True)
class Receipt:
    """What one revocation wrote back: page count, digest, time taken."""
    pages: int
    digest: str
    ms: float
    pageout_errno: int = 0
    runs: tuple = ()


def _pagemap(base, length):
    n = length // PAGE
    with open("/proc/self/pagemap", "rb") as f:
        f.seek((base // PAGE) * 8)
        raw = f.read(n * 8)
    return [struct.unpack_from("<Q", raw, 8 * i)[0] for i in range(n)]


def modified_pages(base, length):
    """Page indices holding private state: anonymous and present, or swapped.
    Clean pages still backed by the image are file pages and are skipped."""
    out = []
    for i, e in enumerate(_pagemap(base, length)):
        present, swapped, file_page = e >> 63 & 1, e >> 62 & 1, e >> 61 & 1
        if swapped or (present and not file_page):
            out.append(i)
    return out


def held_privately(base, length):
    """Page indices present in or swapped from this process's page tables.
    Empty right after `revoke`, which is the invariant."""
    return [i for i, e in enumerate(_pagemap(base, length)) if e >> 63 & 1 or e >> 62 & 1]


def runs_of(pages):
    """Contiguous runs [(first, count)] of sorted page indices."""
    out = []
    for i in pages:
        if out and out[-1][0] + out[-1][1] == i:
            out[-1][1] += 1
        else:
            out.append([i, 1])
    return out


def _check(base, length):
    if base % PAGE or length % PAGE or length <= 0:
        raise RegionError(0, "governed region must be page-aligned and non-empty")


REDO_MAGIC = b"VRE15REDO1"


def _redo_path(image) -> str:
    return os.fspath(image) + ".redo"


def _fsync_dir(path) -> None:
    fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _encode_redo(length: int, runs) -> bytes:
    """MAGIC, region length, run count, then (first, count, bytes) per run,
    then SHA-256 of everything before it."""
    body = bytearray(REDO_MAGIC + struct.pack("<QI", length, len(runs)))
    for first, count, data in runs:
        body += struct.pack("<II", first, count) + data
    return bytes(body) + hashlib.sha256(body).digest()


def _decode_redo(raw: bytes, length: int):
    """The runs of a complete, intact log; None for anything else."""
    head = len(REDO_MAGIC) + 12
    if len(raw) < head + 32 or not raw.startswith(REDO_MAGIC):
        return None
    body, tail = raw[:-32], raw[-32:]
    if hashlib.sha256(body).digest() != tail:
        return None
    n_len, n = struct.unpack_from("<QI", body, len(REDO_MAGIC))
    if n_len != length:
        return None
    runs, off = [], head
    for _ in range(n):
        first, count = struct.unpack_from("<II", body, off)
        off += 8
        data = body[off:off + count * PAGE]
        if len(data) != count * PAGE or (first + count) * PAGE > length:
            return None
        runs.append((first, count, data))
        off += count * PAGE
    return runs if off == len(body) else None


def _apply(fd, runs) -> str:
    h = hashlib.sha256()
    for first, count, data in runs:
        os.pwrite(fd, data, first * PAGE)
        h.update(struct.pack("<II", first, count))
        h.update(data)
    return h.hexdigest()


def recover(image):
    """Finish a checkpoint a crash interrupted. A complete redo log next to
    `image` is applied and synced, then removed; a partial one (never
    renamed into place) is discarded, since the image was not touched.
    Returns the Receipt of what was replayed, or None if nothing was."""
    redo = _redo_path(image)
    tmp = redo + ".tmp"
    if os.path.exists(tmp):
        os.unlink(tmp)                                  # crash while writing the log: image untouched
    if not os.path.exists(redo):
        return None
    t0 = time.perf_counter()
    with open(redo, "rb") as f:
        raw = f.read()
    fd = os.open(image, os.O_RDWR)
    try:
        runs = _decode_redo(raw, os.fstat(fd).st_size)
        if runs is None:
            raise RegionError(0, f"redo log {redo} is damaged; the image was not changed by it")
        digest = _apply(fd, runs)
        os.fdatasync(fd)
    finally:
        os.close(fd)
    os.unlink(redo)
    _fsync_dir(redo)
    return Receipt(sum(c for _, c, _ in runs), digest, (time.perf_counter() - t0) * 1000, 0,
                   tuple((first, count) for first, count, _ in runs))


def map_image(base, length, image):
    """Map `image` copy-on-write over [base, base+length). The image's size
    must equal the region's, so no part of the region falls outside it.
    A checkpoint interrupted by a crash is finished first (`recover`)."""
    _check(base, length)
    recover(image)
    fd = os.open(image, os.O_RDONLY)
    try:
        if os.fstat(fd).st_size != length:
            raise RegionError(0, "image size does not match the region")
        if _libc.mmap(base, length, PROT_RW, MAP_PRIVATE | MAP_FIXED, fd, 0) != base:
            raise RegionError(ctypes.get_errno(), "mmap MAP_FIXED failed")
    finally:
        os.close(fd)


def revoke(base, length, image):
    """Authority over the region has ended: write its modified pages into
    `image`, sync, drop every private page, page the region out."""
    _check(base, length)
    t0 = time.perf_counter()
    dirty = modified_pages(base, length)
    runs = [(first, count, ctypes.string_at(base + first * PAGE, count * PAGE))
            for first, count in runs_of(dirty)]
    fd = os.open(image, os.O_RDWR)
    try:
        if os.fstat(fd).st_size != length:
            raise RegionError(0, "image size does not match the region")
        if runs:
            # Redo log first: synced, then renamed into place, so it exists
            # complete or not at all. Only then is the image touched.
            redo = _redo_path(image)
            tmp = redo + ".tmp"
            with open(tmp, "wb") as f:
                f.write(_encode_redo(length, runs))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, redo)
            _fsync_dir(redo)
            digest = _apply(fd, runs)
            os.fdatasync(fd)
            os.unlink(redo)
            _fsync_dir(redo)
        else:
            digest = hashlib.sha256().hexdigest()
        if _libc.mmap(base, length, PROT_RW, MAP_PRIVATE | MAP_FIXED, fd, 0) != base:
            raise RegionError(ctypes.get_errno(), "remap after checkpoint failed")
    finally:
        os.close(fd)
    err = 0 if _libc.madvise(base, length, MADV_PAGEOUT) == 0 else ctypes.get_errno()
    return Receipt(len(dirty), digest, (time.perf_counter() - t0) * 1000, err,
                   tuple((first, count) for first, count in runs_of(dirty)))
