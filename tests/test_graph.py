"""Tests for cosine_knn."""

import numpy as np

from mixmansion.shared.graph import cosine_knn


def test_edges_symmetric_keys_no_self_loops():
    ids = ["a", "b", "c"]
    vectors = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
    edges = cosine_knn(ids, vectors, k=2)
    for a, b in edges:
        assert a < b
    assert all(a != b for a, b in edges)


def test_every_edge_in_top_k_of_at_least_one_endpoint():
    ids = ["a", "b", "c", "d"]
    vectors = np.array([[1.0, 0.1], [0.9, 0.3], [0.2, 1.0], [0.1, 0.8]])
    k = 1
    edges = cosine_knn(ids, vectors, k)
    index = {id_: i for i, id_ in enumerate(ids)}
    norms = np.linalg.norm(vectors, axis=1)
    unit = vectors / norms[:, None]
    sim = unit @ unit.T
    np.fill_diagonal(sim, -np.inf)
    for a, b in edges:
        ia, ib = index[a], index[b]
        top_k_of_a = set(np.argsort(-sim[ia])[:k])
        top_k_of_b = set(np.argsort(-sim[ib])[:k])
        assert ib in top_k_of_a or ia in top_k_of_b
    assert edges and len(edges) <= 4


def test_weights_in_range_negative_cosine_gives_no_edge():
    ids = ["a", "b"]
    vectors = np.array([[1.0, 0.0], [-1.0, 0.0]])
    edges = cosine_knn(ids, vectors, k=1)
    assert edges == {}


def test_weights_in_zero_to_one():
    ids = ["a", "b", "c"]
    vectors = np.array([[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]])
    edges = cosine_knn(ids, vectors, k=2)
    assert all(0.0 <= w <= 1.0 for w in edges.values())


def test_zero_vectors_get_no_edges():
    ids = ["a", "b", "c"]
    vectors = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]])
    edges = cosine_knn(ids, vectors, k=2)
    for a, b in edges:
        assert a != "a" and b != "a"
        assert a != "c" and b != "c"


def test_identical_vectors_get_weight_near_one():
    ids = ["a", "b"]
    vectors = np.array([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])
    edges = cosine_knn(ids, vectors, k=1)
    assert edges[("a", "b")] == 1.0
