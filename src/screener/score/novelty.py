"""Novelty N = pct(distance to crowded cluster centroids) x pct(cluster funding growth over 8 quarters).

Embeddings are L2-normalized; KMeans with k chosen by cosine silhouette (CPU only). Crowded clusters are the
largest ones (size >= the configured quantile). Theme growth is the least-squares slope of funding-round counts
per quarter for the cluster's members over the last 8 quarters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

from screener.score.core import percentile_ranks


@dataclass
class Clustering:
    labels: np.ndarray
    centroids: np.ndarray
    k: int
    silhouette: float | None
    crowded: set[int]


def _normalize(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.where(norms == 0, 1, norms)


def cluster(embeddings: np.ndarray, k_min: int, k_max: int, min_points: int, crowded_quantile: float,
            random_state: int) -> Clustering:
    x = _normalize(np.asarray(embeddings, dtype=float))
    n = len(x)
    upper = min(k_max, n // max(1, min_points), n - 1)
    best: tuple[float, np.ndarray, np.ndarray, int] | None = None
    for k in range(k_min, upper + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=random_state).fit(x)
        if len(set(km.labels_)) < 2:
            continue
        s = float(silhouette_score(x, km.labels_, metric="cosine"))
        if best is None or s > best[0]:
            best = (s, km.labels_, km.cluster_centers_, k)
    if best is None:  # too few points to cluster: one theme
        labels, centroids, k, sil = np.zeros(n, dtype=int), x.mean(axis=0, keepdims=True), 1, None
    else:
        sil, labels, centroids, k = best
    sizes = np.bincount(labels, minlength=k)
    threshold = np.quantile(sizes, crowded_quantile)
    crowded = {int(c) for c in range(k) if sizes[c] >= threshold}
    return Clustering(labels=labels, centroids=_normalize(centroids), k=k, silhouette=sil, crowded=crowded)


def crowd_distance(embeddings: np.ndarray, c: Clustering) -> np.ndarray:
    """Cosine distance from each point to the nearest crowded centroid."""
    x = _normalize(np.asarray(embeddings, dtype=float))
    crowded = c.centroids[sorted(c.crowded)]
    return 1.0 - (x @ crowded.T).max(axis=1)


def quarter_index(d: date, today: date) -> int:
    """0 = current quarter, 1 = previous, ..."""
    return (today.year - d.year) * 4 + ((today.month - 1) // 3 - (d.month - 1) // 3)


def cluster_growth(labels: np.ndarray, round_dates: list[list[date]], today: date, quarters: int) -> dict[int, float]:
    """Slope of rounds-per-quarter (oldest -> newest) per cluster over the window."""
    out: dict[int, float] = {}
    for c in sorted(set(int(x) for x in labels)):
        counts = np.zeros(quarters)
        for i in np.where(labels == c)[0]:
            for d in round_dates[i]:
                q = quarter_index(d, today)
                if 0 <= q < quarters:
                    counts[quarters - 1 - q] += 1
        out[c] = float(np.polyfit(np.arange(quarters), counts, 1)[0]) if counts.any() else 0.0
    return out


def novelty(distance: np.ndarray, labels: np.ndarray, growth: dict[int, float]) -> list[float]:
    d_pct = percentile_ranks(list(distance))
    g_pct = percentile_ranks([growth[int(c)] for c in labels])
    return [float(a) * float(b) for a, b in zip(d_pct, g_pct, strict=True)]
