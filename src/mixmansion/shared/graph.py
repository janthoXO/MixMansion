import numpy as np


def cosine_knn(ids: list[str], vectors: np.ndarray, k: int) -> dict[tuple[str, str], float]:
    """Symmetric sparse kNN edges from row vectors.

    Weights are cosine similarity clipped to [0, 1], keys are (a, b) with a < b,
    zero vectors get no edges.
    """
    # ponytail: brute force n×n matrix, fine up to ~10k songs; switch to an ANN index beyond that
    norms = np.linalg.norm(vectors, axis=1)
    live = norms > 0
    unit = np.zeros_like(vectors, dtype=float)
    unit[live] = vectors[live] / norms[live, None]
    sim = np.clip(unit @ unit.T, 0.0, 1.0)
    np.fill_diagonal(sim, 0.0)
    edges: dict[tuple[str, str], float] = {}
    for i in np.flatnonzero(live):
        for j in np.argsort(-sim[i])[:k]:
            if sim[i, j] > 0:
                edges[tuple(sorted((ids[i], ids[j])))] = float(sim[i, j])
    return edges
