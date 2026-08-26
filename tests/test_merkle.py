import os
import pytest
from cappo_backend.security.merkle import AppendOnlyMerkleTree, verify_inclusion_proof, verify_consistency_proof, hash_leaf

def test_merkle_tree():
    tree = AppendOnlyMerkleTree()
    leaves = [os.urandom(32) for _ in range(10)]
    roots = []
    
    for i, leaf in enumerate(leaves):
        idx = tree.append(leaf)
        assert idx == i
        roots.append(tree.root())
        
    for i in range(10):
        for j in range(i + 1, 11):
            sub_tree = AppendOnlyMerkleTree(leaves[:j])
            proof = sub_tree.inclusion_proof(i)
            assert verify_inclusion_proof(hash_leaf(sub_tree._leaves[i]), i, j, proof, sub_tree.root())

    for i in range(1, 11):
        for j in range(i, 11):
            tree_j = AppendOnlyMerkleTree(leaves[:j])
            proof = tree_j.consistency_proof(i, j)
            assert verify_consistency_proof(i, j, roots[i-1], roots[j-1], proof)

