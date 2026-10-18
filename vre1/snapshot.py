"""Wasm instance snapshot and restore: linear memory plus mutable globals.

This is a snapshot of a Wasm guest's state inside a runtime, not a VM or
process snapshot. It is the materialization mechanism available on the
reference host (no KVM, no CRIU).

File layout: 4-byte big-endian header length, header JSON, payload.
The header commits to the module digest, page count, globals, the SHA-256 of
the full linear memory and the codec. Restore refuses on any mismatch.

Codecs:
  raw           the full linear memory, every byte
  sparse        a bitmap of non-zero 4 KiB blocks plus those blocks only
  sparse-zlib1  sparse, compressed with zlib level 1
Restore writes only non-zero blocks for the sparse codecs. A fresh linear
memory is already zero, so zero blocks are never touched.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import struct
import zlib

PROTOCOL = "veklom.vre1.snapshot/v1"
BLOCK = 4096
PAGE = 65536
CODECS = ("raw", "sparse", "sparse-zlib1")
GLOBALS = ("seed", "steps")
ZERO = bytes(BLOCK)


class SnapshotRejected(Exception):
    pass


def _bitmap(mem: bytes) -> tuple[bytes, bytes]:
    nblk = len(mem) // BLOCK
    bits = bytearray((nblk + 7) // 8)
    blocks = []
    for i in range(nblk):
        chunk = mem[i * BLOCK:(i + 1) * BLOCK]
        if chunk != ZERO:
            bits[i // 8] |= 1 << (i % 8)
            blocks.append(chunk)
    return bytes(bits), b"".join(blocks)


def encode(mem: bytes, globals_: dict, module_sha256: str, codec: str) -> bytes:
    if codec not in CODECS:
        raise ValueError(codec)
    if codec == "raw":
        payload = mem
    else:
        bits, blocks = _bitmap(mem)
        payload = struct.pack(">I", len(bits)) + bits + blocks
        if codec == "sparse-zlib1":
            payload = zlib.compress(payload, 1)
    header = json.dumps({
        "protocol": PROTOCOL, "codec": codec, "module_sha256": module_sha256,
        "pages": len(mem) // PAGE, "globals": globals_,
        "memory_sha256": hashlib.sha256(mem).hexdigest(),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }, sort_keys=True).encode()
    return struct.pack(">I", len(header)) + header + payload


def split(blob) -> tuple[dict, memoryview]:
    view = memoryview(blob)
    try:
        (hlen,) = struct.unpack(">I", view[:4])
        return json.loads(bytes(view[4:4 + hlen])), view[4 + hlen:]
    except Exception as e:
        raise SnapshotRejected(f"unreadable header: {e}") from e


def verify_payload(blob) -> None:
    """The integrity check, separate so its cost can be measured on its own."""
    header, payload = split(blob)
    if hashlib.sha256(payload).hexdigest() != header.get("payload_sha256"):
        raise SnapshotRejected("payload digest mismatch")


def decode(blob, verify: bool = True) -> tuple[dict, list[tuple[int, memoryview]]]:
    """Header and (offset, view) runs to copy into fresh memory. Views slice
    one writable buffer, so decoding copies nothing it does not have to."""
    if not isinstance(blob, bytearray):
        blob = bytearray(blob)
    header, payload = split(blob)
    if header.get("protocol") != PROTOCOL or header.get("codec") not in CODECS:
        raise SnapshotRejected("unknown protocol or codec")
    if verify and hashlib.sha256(payload).hexdigest() != header["payload_sha256"]:
        raise SnapshotRejected("payload digest mismatch")
    size = header["pages"] * PAGE
    if header["codec"] == "raw":
        if len(payload) != size:
            raise SnapshotRejected("raw payload size mismatch")
        return header, [(0, payload)]
    if header["codec"] == "sparse-zlib1":
        payload = memoryview(bytearray(zlib.decompress(payload)))
    (blen,) = struct.unpack(">I", payload[:4])
    bits, blocks = payload[4:4 + blen], payload[4 + blen:]
    if len(bits) * 8 < size // BLOCK:
        raise SnapshotRejected("bitmap too short")
    runs, pos, start, run_pos = [], 0, None, 0
    for i in range(size // BLOCK):
        if bits[i >> 3] >> (i & 7) & 1:
            if start is None:
                start, run_pos = i * BLOCK, pos
            pos += BLOCK
        elif start is not None:
            runs.append((start, blocks[run_pos:pos]))
            start = None
    if start is not None:
        runs.append((start, blocks[run_pos:pos]))
    if pos != len(blocks):
        raise SnapshotRejected("sparse block count mismatch")
    return header, runs


def write_file(path: str, blob: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, blob)
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(store, exports, module_sha256: str, codec: str) -> bytes:
    mem = exports["memory"]
    raw = bytes(mem.read(store, 0, mem.data_len(store)))
    return encode(raw, {g: exports[g].value(store) for g in GLOBALS}, module_sha256, codec)


def restore_into(store, exports, header: dict, runs: list[tuple[int, bytes]],
                 module_sha256: str) -> None:
    if header["module_sha256"] != module_sha256:
        raise SnapshotRejected("snapshot is for a different module")
    mem = exports["memory"]
    mem.grow(store, header["pages"] - mem.size(store))
    base = ctypes.addressof(mem.data_ptr(store).contents)
    size = mem.data_len(store)
    for offset, data in runs:
        n = len(data)
        if offset < 0 or offset + n > size:
            raise SnapshotRejected("run outside linear memory")
        # one copy, straight from the decoded buffer into guest memory
        ctypes.memmove(base + offset, ctypes.addressof(ctypes.c_char.from_buffer(data)), n)
    for g in GLOBALS:
        exports[g].set_value(store, header["globals"][g])


def memory_sha256(store, exports) -> str:
    mem = exports["memory"]
    return hashlib.sha256(bytes(mem.read(store, 0, mem.data_len(store)))).hexdigest()
