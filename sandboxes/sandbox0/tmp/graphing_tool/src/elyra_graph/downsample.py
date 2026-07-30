"""Downsample strategies for large series."""

from __future__ import annotations

import numpy as np

from .errors import GraphingError

DEFAULT_MAX_POINTS = 250_000


def lttb(x: np.ndarray, y: np.ndarray, threshold: int) -> tuple[np.ndarray, np.ndarray]:
    """Largest-Triangle-Three-Buckets downsample."""
    n = len(x)
    if threshold >= n or threshold < 3:
        return x, y
    x = x.astype(float)
    y = y.astype(float)
    sampled_x = np.zeros(threshold)
    sampled_y = np.zeros(threshold)
    sampled_x[0], sampled_y[0] = x[0], y[0]
    sampled_x[-1], sampled_y[-1] = x[-1], y[-1]

    bucket_size = (n - 2) / (threshold - 2)
    a = 0
    for i in range(1, threshold - 1):
        avg_range_start = int(np.floor((i) * bucket_size)) + 1
        avg_range_end = int(np.floor((i + 1) * bucket_size)) + 1
        avg_range_end = min(avg_range_end, n)
        avg_x = np.mean(x[avg_range_start:avg_range_end])
        avg_y = np.mean(y[avg_range_start:avg_range_end])

        range_start = int(np.floor((i - 1) * bucket_size)) + 1
        range_end = int(np.floor(i * bucket_size)) + 1
        range_end = min(range_end, n)

        point_ax, point_ay = x[a], y[a]
        max_area = -1.0
        next_a = range_start
        for idx in range(range_start, range_end):
            area = abs(
                (point_ax - avg_x) * (y[idx] - point_ay)
                - (point_ax - x[idx]) * (avg_y - point_ay)
            ) * 0.5
            if area > max_area:
                max_area = area
                next_a = idx
        sampled_x[i] = x[next_a]
        sampled_y[i] = y[next_a]
        a = next_a
    return sampled_x, sampled_y


def apply_limit(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int = DEFAULT_MAX_POINTS,
    strategy: str | None = "lttb",
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    warnings: list[str] = []
    n = len(y)
    if n <= max_points:
        return x, y, warnings
    if not strategy:
        raise GraphingError(
            "E_LIMIT",
            f"series length {n} exceeds max_points={max_points}",
            hint="pass downsample='lttb' or raise max_points",
        )
    if strategy == "stride":
        step = int(np.ceil(n / max_points))
        warnings.append(f"downsampled with stride={step} from {n}")
        return x[::step], y[::step], warnings
    if strategy == "lttb":
        xs, ys = lttb(x, y, max_points)
        warnings.append(f"downsampled with lttb to {len(ys)} from {n}")
        return xs, ys, warnings
    raise GraphingError("E_LIMIT", f"unknown downsample strategy: {strategy}")
