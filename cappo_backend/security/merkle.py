"""
RFC 6962-style append-only Merkle tree.

Leaf hash:   SHA-256(0x00 || data)
Parent hash: SHA-256(0x01 || left || right)
"""

from __future__ import annotations
import hashlib

def hash_leaf(data: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(b"\x00")
    h.update(data)
    return h.digest()

def hash_children(left: bytes, right: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(b"\x01")
    h.update(left)
    h.update(right)
    return h.digest()

def _largest_power_of_2_less_than(n: int) -> int:
    assert n >= 2
    k = 1
    while k < n:
        k <<= 1
    return k >> 1


class AppendOnlyMerkleTree:
    def __init__(self, leaves: list[bytes] | None = None) -> None:
        self._leaves: list[bytes] = list(leaves) if leaves else []

    def append(self, data: bytes) -> int:
        self._leaves.append(data)
        return len(self._leaves) - 1

    @property
    def size(self) -> int:
        return len(self._leaves)

    def root(self, n: int | None = None) -> bytes:
        if n is None:
            n = len(self._leaves)
        return self._merkle_hash(0, n)

    def inclusion_proof(self, m: int, n: int | None = None) -> list[bytes]:
        if n is None:
            n = len(self._leaves)
        if not (0 <= m < n):
            raise ValueError(f"Leaf index {m} out of range for tree size {n}")
        return self._path(m, 0, n)

    def consistency_proof(self, first: int, second: int | None = None) -> list[bytes]:
        if second is None:
            second = len(self._leaves)
        if not (0 <= first <= second <= len(self._leaves)):
            raise ValueError(f"Invalid sizes first={first} second={second}")
        if first == 0 or first == second:
            return []
        return self._subproof(first, 0, second, True)

    def _merkle_hash(self, start: int, length: int) -> bytes:
        if length == 0:
            return hashlib.sha256(b"").digest()
        if length == 1:
            return hash_leaf(self._leaves[start])
        k = _largest_power_of_2_less_than(length)
        left = self._merkle_hash(start, k)
        right = self._merkle_hash(start + k, length - k)
        return hash_children(left, right)

    def _path(self, m: int, start: int, length: int) -> list[bytes]:
        if length == 1:
            return []
        k = _largest_power_of_2_less_than(length)
        if m < start + k:
            path = self._path(m, start, k)
            path.append(self._merkle_hash(start + k, length - k))
        else:
            path = self._path(m, start + k, length - k)
            path.append(self._merkle_hash(start, k))
        return path

    def _subproof(self, m: int, start: int, length: int, b: bool) -> list[bytes]:
        if m == length:
            if b: return []
            return [self._merkle_hash(start, length)]
        k = _largest_power_of_2_less_than(length)
        if m <= k:
            path = self._subproof(m, start, k, b)
            path.append(self._merkle_hash(start + k, length - k))
        else:
            path = self._subproof(m - k, start + k, length - k, False)
            path.append(self._merkle_hash(start, k))
        return path


def verify_inclusion_proof(
    leaf_hash: bytes,
    m: int,
    n: int,
    proof: list[bytes],
    root: bytes,
) -> bool:
    if not (0 <= m < n):
        return False
    try:
        proof_copy = list(proof)
        computed = _recompute_root(leaf_hash, m, 0, n, proof_copy)
        return computed == root and not proof_copy
    except (IndexError, ValueError):
        return False


def _recompute_root(
    h: bytes,
    m: int,
    start: int,
    length: int,
    proof: list[bytes],
) -> bytes:
    if length == 1:
        return h
    k = _largest_power_of_2_less_than(length)
    if m < start + k:
        left = _recompute_root(h, m, start, k, proof)
        sibling = proof.pop(0)
        return hash_children(left, sibling)
    else:
        right = _recompute_root(h, m, start + k, length - k, proof)
        sibling = proof.pop(0)
        return hash_children(sibling, right)


def verify_consistency_proof(
    first: int,
    second: int,
    first_root: bytes,
    second_root: bytes,
    proof: list[bytes],
) -> bool:
    if first == second:
        return first_root == second_root and not proof
    if first == 0 or first > second:
        return False
    
    proof_copy = list(proof)
    try:
        fr, sr = _verify_subproof(first, 0, second, True, proof_copy, first_root)
        return fr == first_root and sr == second_root and not proof_copy
    except (IndexError, ValueError):
        return False


def _verify_subproof(
    m: int,
    start: int,
    length: int,
    b: bool,
    proof: list[bytes],
    first_root: bytes,
) -> tuple[bytes, bytes]:
    if m == length:
        if b:
            return first_root, first_root
        node = proof.pop(0)
        return node, node
    
    k = _largest_power_of_2_less_than(length)
    if m <= k:
        fl, sl = _verify_subproof(m, start, k, b, proof, first_root)
        right = proof.pop(0)
        return fl, hash_children(sl, right)
    else:
        fl, sl = _verify_subproof(m - k, start + k, length - k, False, proof, first_root)
        left = proof.pop(0)
        return hash_children(left, fl), hash_children(left, sl)
