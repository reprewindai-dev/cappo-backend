"""VRE-1 snapshot format: lossless per codec, refuses tampering, bounded writes.

Uses only the stdlib, so it runs without wasmtime. Guest memory is stood in
for by a ctypes buffer behind the same data_ptr/data_len/grow interface
restore_into uses.
"""

import ctypes
import json
import pathlib
import random

import pytest

from vre1 import snapshot as snap

MODULE = "a" * 64


class FakeMemory:
    def __init__(self, max_pages=64):
        self.buf = (ctypes.c_ubyte * (max_pages * snap.PAGE))()
        self.pages = 0

    def size(self, store):
        return self.pages

    def grow(self, store, delta):
        self.pages += delta

    def data_len(self, store):
        return self.pages * snap.PAGE

    def data_ptr(self, store):
        return ctypes.cast(self.buf, ctypes.POINTER(ctypes.c_ubyte))

    def read(self, store, start, stop):
        return bytes(self.buf)[start:stop]


class FakeGlobal:
    def __init__(self, v=0):
        self.v = v

    def value(self, store):
        return self.v

    def set_value(self, store, v):
        self.v = v


def state(pages=8, density=0.25, seed=1):
    rng = random.Random(seed)
    mem = bytearray(pages * snap.PAGE)
    for b in range(len(mem) // snap.BLOCK):
        if rng.random() < density:
            mem[b * snap.BLOCK:(b + 1) * snap.BLOCK] = rng.randbytes(snap.BLOCK)
    return bytes(mem)


def restore(blob, module=MODULE):
    header, runs = snap.decode(blob)
    x = {"memory": FakeMemory(), "seed": FakeGlobal(), "steps": FakeGlobal()}
    snap.restore_into(None, x, header, runs, module)
    return x["memory"].read(None, 0, x["memory"].data_len(None)), x


@pytest.mark.parametrize("codec", snap.CODECS)
@pytest.mark.parametrize("density", [0.0, 0.05, 0.25, 1.0])
def test_roundtrip_is_exact(codec, density):
    mem = state(density=density)
    blob = snap.encode(mem, {"seed": 99, "steps": 7}, MODULE, codec)
    out, x = restore(blob)
    assert out == mem
    assert (x["seed"].v, x["steps"].v) == (99, 7)


def test_sparse_writes_only_nonzero_blocks():
    mem = state(density=0.25)
    header, runs = snap.decode(snap.encode(mem, {"seed": 1, "steps": 0}, MODULE, "sparse"))
    nonzero = sum(1 for b in range(0, len(mem), snap.BLOCK) if any(mem[b:b + snap.BLOCK]))
    assert sum(len(d) for _, d in runs) == nonzero * snap.BLOCK < len(mem)


@pytest.mark.parametrize("codec", snap.CODECS)
def test_flipped_payload_byte_is_refused(codec):
    blob = bytearray(snap.encode(state(), {"seed": 1, "steps": 0}, MODULE, codec))
    blob[-10] ^= 0xFF
    with pytest.raises(snap.SnapshotRejected, match="payload digest"):
        snap.decode(blob)


def test_header_edit_is_refused_by_size_or_digest():
    blob = snap.encode(state(), {"seed": 1, "steps": 0}, MODULE, "raw")
    hlen = int.from_bytes(blob[:4], "big")
    header = json.loads(blob[4:4 + hlen])
    header["pages"] += 1
    h = json.dumps(header, sort_keys=True).encode()
    with pytest.raises(snap.SnapshotRejected):
        snap.decode(len(h).to_bytes(4, "big") + h + blob[4 + hlen:])


def test_snapshot_for_another_module_is_refused():
    blob = snap.encode(state(), {"seed": 1, "steps": 0}, MODULE, "sparse")
    with pytest.raises(snap.SnapshotRejected, match="different module"):
        restore(blob, module="b" * 64)


def test_runs_cannot_write_outside_linear_memory():
    header, runs = snap.decode(snap.encode(state(pages=4, density=1.0), {"seed": 1, "steps": 0},
                                           MODULE, "raw"))
    header = dict(header, pages=2)                  # pretend memory is smaller than the run
    x = {"memory": FakeMemory(), "seed": FakeGlobal(), "steps": FakeGlobal()}
    with pytest.raises(snap.SnapshotRejected, match="outside linear memory"):
        snap.restore_into(None, x, header, runs, MODULE)


def test_unreadable_header_is_refused():
    with pytest.raises(snap.SnapshotRejected):
        snap.decode(b"\x00\x00\x00\x05nope!")


def test_committed_runs_have_no_integrity_failures():
    for manifest in sorted((pathlib.Path(__file__).resolve().parents[1] / "vre1" / "runs").glob("*/manifest.json")):
        m = json.loads(manifest.read_text())
        for row in m["summary"]["vre1a"] + m["summary"]["vre1b"]:
            assert row["integrity_failures"] == 0, (manifest, row)
