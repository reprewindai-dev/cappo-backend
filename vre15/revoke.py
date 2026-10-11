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

Invariant, checked by `held_privately` and the tests: after `revoke`,
- no page of the region is present in or swapped from this process's page
  tables;
- the image holds the state exactly, synced to disk;
- reading the region afterwards faults that state back in unchanged.
Page-cache residency after step 4 is best-effort. H15 measured 89% of
revoked tenants with nothing resident 5 s after authority ended, against
9% under native reclaim.

Each revocation returns a receipt whose digest covers the page indices and
bytes written. Identical writes under identical authority give identical
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


def map_image(base, length, image):
    """Map `image` copy-on-write over [base, base+length). The image's size
    must equal the region's, so no part of the region falls outside it."""
    _check(base, length)
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
    h = hashlib.sha256()
    fd = os.open(image, os.O_RDWR)
    try:
        if os.fstat(fd).st_size != length:
            raise RegionError(0, "image size does not match the region")
        for first, count in runs_of(dirty):
            data = ctypes.string_at(base + first * PAGE, count * PAGE)
            os.pwrite(fd, data, first * PAGE)
            h.update(struct.pack("<II", first, count))
            h.update(data)
        if dirty:
            os.fdatasync(fd)
        if _libc.mmap(base, length, PROT_RW, MAP_PRIVATE | MAP_FIXED, fd, 0) != base:
            raise RegionError(ctypes.get_errno(), "remap after checkpoint failed")
    finally:
        os.close(fd)
    err = 0 if _libc.madvise(base, length, MADV_PAGEOUT) == 0 else ctypes.get_errno()
    return Receipt(len(dirty), h.hexdigest(), (time.perf_counter() - t0) * 1000, err)
