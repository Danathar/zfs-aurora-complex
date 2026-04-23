"""
Module: shared/kernel_release.py
What: Shared helpers for comparing Fedora kernel release strings.
Doing: Builds one natural-sort key used by both CI input resolution and the
image-build helper.
Why: The repo chooses a single supported primary kernel in more than one place,
so the sort logic should live in one module instead of drifting.
Goal: Keep primary-kernel selection consistent across workflow and build paths.
"""

from __future__ import annotations

import re


KERNEL_RELEASE_PART_RE = re.compile(r"\d+|[^\d]+")


def kernel_release_sort_key(value: str) -> list[tuple[int, object]]:
    """
    Return a natural-sort key for kernel release strings.

    The tuple form keeps numeric and text fragments comparable without relying
    on mixed `int`/`str` comparisons.
    """

    return [
        (0, int(part)) if part.isdigit() else (1, part)
        for part in KERNEL_RELEASE_PART_RE.findall(value)
    ]
