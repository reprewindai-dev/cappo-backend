"""Known defect, recorded so it cannot be forgotten: a checkpoint on
revocation is not crash-atomic.

`revoke.revoke` writes the modified pages into the image one run at a time
and syncs at the end. A process killed between those writes leaves the image
holding some pages of the new state and some of the old: a torn state, with
no receipt and nothing that says so. The next mapping of that image starts
the tenant from a state that never existed.

Found in due diligence on 2026-10-18 (vre15/README.md, "Due diligence").
The test kills a real process at its 3rd page write (deterministic, by
wrapping os.pwrite) and checks the image. It is marked xfail(strict=True):
it fails today, and once checkpoints are made atomic it will pass and the
marker must come off.
"""

import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux mappings")

CHILD = r"""
import ctypes, os, signal, sys
sys.path.insert(0, sys.argv[2])
from vre15 import revoke
img, kill_at = sys.argv[1], int(sys.argv[3])
length = os.path.getsize(img)
fd = os.open(img, os.O_RDONLY)
base = revoke._libc.mmap(None, length, revoke.PROT_RW, revoke.MAP_PRIVATE, fd, 0)
os.close(fd)
for i in range(8):                                   # 8 separate runs, 8 page writes
    ctypes.memset(base + 2 * i * revoke.PAGE, 0x4E, revoke.PAGE)
calls = [0]
real = os.pwrite
def pwrite(fd, data, off):
    calls[0] += 1
    if calls[0] == kill_at:
        os.kill(os.getpid(), signal.SIGKILL)
    return real(fd, data, off)
os.pwrite = pwrite
revoke.revoke(base, length, img)
"""


def new_pages_after_crash(tmp_path, kill_at):
    img = tmp_path / f"t-{kill_at}.img"
    img.write_bytes(b"O" * (16 * 4096))
    p = subprocess.run([sys.executable, "-c", CHILD, str(img), str(ROOT), str(kill_at)])
    data = img.read_bytes()
    return p.returncode, sum(data[i * 4096] == ord("N") for i in range(0, 16, 2))


def test_without_a_crash_the_checkpoint_is_complete(tmp_path):
    assert new_pages_after_crash(tmp_path, kill_at=99) == (0, 8)


@pytest.mark.xfail(strict=True, reason="known defect: checkpoint on revocation is not crash-atomic")
def test_a_crash_mid_checkpoint_leaves_the_image_old_or_new_never_torn(tmp_path):
    rc, new = new_pages_after_crash(tmp_path, kill_at=3)
    assert rc == -9
    assert new in (0, 8), f"torn image: {new} of 8 pages hold the new state"
