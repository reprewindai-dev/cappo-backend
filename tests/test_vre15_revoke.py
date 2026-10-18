"""Residency follows authority (vre15/revoke.py), checked on real mappings.

After `revoke`, a governed region holds no private page in the process,
present or swapped; its image holds exactly the state, and the receipt's
digest is a deterministic record of what was written back.
"""

import ctypes
import hashlib
import os
import sys

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Linux only", allow_module_level=True)

from vre15 import revoke  # noqa: E402

PAGE = revoke.PAGE
N = 16
MAP_ANON = 0x20
_libc = ctypes.CDLL(None, use_errno=True)
_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
_libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
_libc.madvise.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]


def initial():
    """Every other page non-zero; the zero pages become holes in the image."""
    return b"".join(bytes([i + 1]) * PAGE if i % 2 == 0 else bytes(PAGE) for i in range(N))


@pytest.fixture
def region(tmp_path):
    made = []

    def make(name="t.img", content=None):
        content = initial() if content is None else content
        img = tmp_path / name
        fd = os.open(img, os.O_CREAT | os.O_WRONLY, 0o600)
        os.ftruncate(fd, len(content))
        for off in range(0, len(content), PAGE):
            if any(content[off:off + PAGE]):
                os.pwrite(fd, content[off:off + PAGE], off)
        os.close(fd)
        base = _libc.mmap(None, len(content), revoke.PROT_RW, revoke.MAP_PRIVATE | MAP_ANON, -1, 0)
        assert base not in (None, ctypes.c_void_p(-1).value)
        made.append((base, len(content)))
        revoke.map_image(base, len(content), img)
        return base, len(content), img

    yield make
    for base, length in made:
        _libc.munmap(base, length)


def poke(base, page, byte, off=0):
    ctypes.memset(base + page * PAGE + off, byte, 64)


def test_revoke_writes_back_exactly_the_modified_pages_and_releases_them(region):
    base, length, img = region()
    before = bytes(ctypes.string_at(base, length))          # faults clean file pages in
    assert before == initial()
    assert revoke.modified_pages(base, length) == []       # reads alone modify nothing
    for p in (3, 4, 9):
        poke(base, p, 0xAB)
    assert revoke.modified_pages(base, length) == [3, 4, 9]
    want = bytes(ctypes.string_at(base, length))

    r = revoke.revoke(base, length, img)

    assert r.pages == 3 and r.pageout_errno == 0
    assert revoke.held_privately(base, length) == []       # the invariant
    assert img.read_bytes() == want                        # the image holds the state
    assert bytes(ctypes.string_at(base, length)) == want   # and it faults back unchanged
    assert revoke.modified_pages(base, length) == []       # as clean file pages


def test_receipt_digest_is_deterministic_and_covers_position_and_content(region):
    def receipt(name, page, byte):
        base, length, img = region(name)
        poke(base, page, byte)
        return revoke.revoke(base, length, img).digest

    a, b = receipt("a.img", 5, 0x11), receipt("b.img", 5, 0x11)
    assert a == b
    assert receipt("c.img", 5, 0x12) != a                  # different bytes
    assert receipt("d.img", 6, 0x11) != a                  # same bytes, different page


def test_revoke_with_nothing_modified_writes_nothing(region):
    base, length, img = region()
    ctypes.string_at(base, length)
    r = revoke.revoke(base, length, img)
    assert r.pages == 0 and r.digest == hashlib.sha256().hexdigest()
    assert img.read_bytes() == initial()
    assert revoke.held_privately(base, length) == []


def test_each_authority_epoch_is_receipted_separately(region):
    base, length, img = region()
    poke(base, 2, 0x21)
    first = revoke.revoke(base, length, img)
    poke(base, 7, 0x22)                                    # next window: only page 7 changes
    second = revoke.revoke(base, length, img)
    assert (first.pages, second.pages) == (1, 1) and first.digest != second.digest
    data = img.read_bytes()
    assert data[2 * PAGE] == 0x21 and data[7 * PAGE] == 0x22   # the first epoch's write survived the remap


def test_state_already_in_swap_is_written_back_and_leaves_swap(region):
    with open("/proc/swaps") as f:
        if len(f.read().splitlines()) < 2:
            pytest.skip("no swap device")
    base, length, img = region()
    poke(base, 1, 0x5A)
    if _libc.madvise(base + PAGE, PAGE, revoke.MADV_PAGEOUT) != 0:
        pytest.skip("MADV_PAGEOUT unsupported")
    e = revoke._pagemap(base, length)[1]
    if not e >> 62 & 1:
        pytest.skip("page was not swapped out")
    assert revoke.modified_pages(base, length) == [1]
    r = revoke.revoke(base, length, img)
    assert r.pages == 1 and revoke.held_privately(base, length) == []
    assert img.read_bytes()[PAGE:2 * PAGE] == bytes([0x5A]) * 64 + bytes(PAGE - 64)


def test_mismatched_or_unaligned_regions_are_refused(region, tmp_path):
    base, length, img = region()
    short = tmp_path / "short.img"
    short.write_bytes(bytes(PAGE))
    with pytest.raises(revoke.RegionError):
        revoke.map_image(base, length, short)
    with pytest.raises(revoke.RegionError):
        revoke.revoke(base, length, short)
    with pytest.raises(revoke.RegionError):
        revoke.revoke(base + 1, length - PAGE, img)
