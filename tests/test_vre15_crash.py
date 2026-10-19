"""A checkpoint on revocation is crash-atomic.

Found in due diligence on 2026-10-18: `revoke.revoke` wrote modified pages
into the image one run at a time, so a process killed between writes left
the image torn (2 or 5 of 8 new pages), with nothing recording it. The fix
writes a redo log first (synced, then renamed into place), then the image,
then removes the log; `revoke.recover`, run by `map_image` and by the host
before mapping, replays a complete log.

Each case kills a real process with SIGKILL at a chosen step of a real
revocation (deterministic: the step's os call is wrapped), then recovers
the way the next mapping would, and checks that the image holds the whole
old state or the whole new state.
"""

import os
import pathlib
import subprocess
import sys

import pytest

from vre15 import revoke

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux mappings")

CHILD = r"""
import ctypes, os, signal, sys
sys.path.insert(0, sys.argv[2])
from vre15 import revoke
img, call, nth = sys.argv[1], sys.argv[3], int(sys.argv[4])
length = os.path.getsize(img)
fd = os.open(img, os.O_RDONLY)
base = revoke._libc.mmap(None, length, revoke.PROT_RW, revoke.MAP_PRIVATE, fd, 0)
os.close(fd)
for i in range(8):                                   # 8 separate runs, 8 page writes
    ctypes.memset(base + 2 * i * revoke.PAGE, 0x4E, revoke.PAGE)
if call != "none":
    real, seen = getattr(os, call), [0]
    def crash(*a, **k):
        seen[0] += 1
        if seen[0] == nth:
            os.kill(os.getpid(), signal.SIGKILL)
        return real(*a, **k)
    setattr(os, call, crash)
revoke.revoke(base, length, img)
"""

# (os call to die in, at which call, the state recovery must produce)
CASES = {
    "while writing the redo log": ("fsync", 1, "old"),
    "before the log is renamed into place": ("replace", 1, "old"),
    "after 2 of 8 image writes": ("pwrite", 3, "new"),
    "after 5 of 8 image writes": ("pwrite", 6, "new"),
    "after the image is synced, before the log is removed": ("unlink", 1, "new"),
    "no crash": ("none", 0, "new"),
}


def new_pages(img):
    data = img.read_bytes()
    return sum(data[i * 4096] == ord("N") for i in range(0, 16, 2))


@pytest.mark.parametrize("case", CASES)
def test_after_a_crash_and_recovery_the_image_is_old_or_new_never_torn(tmp_path, case):
    call, nth, expect = CASES[case]
    img = tmp_path / "t0.img"
    img.write_bytes(b"O" * (16 * 4096))
    p = subprocess.run([sys.executable, "-c", CHILD, str(img), str(ROOT), call, str(nth)])
    assert p.returncode == (0 if call == "none" else -9), case
    if call == "pwrite":                                 # the crash really landed mid-apply
        assert 0 < new_pages(img) < 8
    r = revoke.recover(img)
    assert new_pages(img) == (8 if expect == "new" else 0), case
    assert not os.path.exists(f"{img}.redo") and not os.path.exists(f"{img}.redo.tmp")
    if call in ("pwrite", "unlink"):                     # what was replayed is receipted
        assert r is not None and r.pages == 8 and len(r.runs) == 8
    else:
        assert r is None


def test_map_image_recovers_before_mapping(tmp_path):
    img = tmp_path / "t0.img"
    img.write_bytes(b"O" * (16 * 4096))
    subprocess.run([sys.executable, "-c", CHILD, str(img), str(ROOT), "pwrite", "3"])
    assert 0 < new_pages(img) < 8                        # torn on disk until recovered
    import ctypes
    base = revoke._libc.mmap(None, 16 * 4096, revoke.PROT_RW, revoke.MAP_PRIVATE | 0x20, -1, 0)
    revoke.map_image(base, 16 * 4096, img)
    assert ctypes.string_at(base, 1) == b"N" and new_pages(img) == 8


def test_a_damaged_log_is_refused_and_leaves_the_image_alone(tmp_path):
    img = tmp_path / "t0.img"
    img.write_bytes(b"O" * (16 * 4096))
    pathlib.Path(f"{img}.redo").write_bytes(revoke.REDO_MAGIC + b"\0" * 64)
    with pytest.raises(revoke.RegionError):
        revoke.recover(img)
    assert new_pages(img) == 0
