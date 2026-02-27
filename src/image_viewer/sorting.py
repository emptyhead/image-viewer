"""Sorting strategies for image lists."""

from __future__ import annotations

from typing import Callable

import random

from .models import ImageInfo


# Sort key: (sort_name, description)
SORT_OPTIONS: list[tuple[str, str]] = [
    ("random",     "Random order"),
    ("unviewed",    "Unviewed first, then alphabetical"),
    ("viewed",      "Viewed first, then alphabetical"),
    ("alpha",       "Alphabetical by filename"),
    ("directory",   "Grouped by directory, then alphabetical"),
    ("rating",      "Lowest rating first (unrated at top)"),
    ("rating-desc", "Highest rating first"),
]

SORT_NAMES: list[str] = [s[0] for s in SORT_OPTIONS]


def _key_alpha(img: ImageInfo) -> tuple:
    return (img.filename.lower(), img.filepath.lower())


def _key_directory(img: ImageInfo) -> tuple:
    return (img.directory.lower(), img.filename.lower())


def _key_unviewed(img: ImageInfo) -> tuple:
    # Unviewed (viewed=False → 0) sorts before viewed (viewed=True → 1)
    return (int(img.viewed), img.filename.lower())


def _key_viewed(img: ImageInfo) -> tuple:
    # Viewed (viewed=True → 0 after negation) sorts before unviewed
    return (int(not img.viewed), img.filename.lower())


def _key_rating(img: ImageInfo) -> tuple:
    # Unrated (0) first, then ascending rating
    return (img.rating, img.filename.lower())


def _key_rating_desc(img: ImageInfo) -> tuple:
    # Highest rating first (negate rating for descending)
    return (-img.rating, img.filename.lower())


def _key_random(img: ImageInfo) -> float:
    # Random order - use a hash of filepath for consistency within session
    return random.random()


_SORT_KEY_MAP: dict[str, Callable[[ImageInfo], tuple]] = {
    "alpha":       _key_alpha,
    "directory":   _key_directory,
    "unviewed":    _key_unviewed,
    "viewed":      _key_viewed,
    "rating":      _key_rating,
    "rating-desc": _key_rating_desc,
    "random":      _key_random,
}


def sort_images(images: list[ImageInfo], sort: str) -> list[ImageInfo]:
    """Return a new sorted list of images using the given sort strategy.

    Args:
        images: List of ImageInfo objects to sort.
        sort: Sort strategy name. One of: alpha, directory, unviewed, viewed,
              rating, rating-desc.

    Returns:
        New sorted list (original list is not modified).

    Raises:
        ValueError: If sort is not a recognised strategy.
    """
    key_fn = _SORT_KEY_MAP.get(sort)
    if key_fn is None:
        raise ValueError(
            f"Unknown sort strategy {sort!r}. "
            f"Valid options: {', '.join(SORT_NAMES)}"
        )
    return sorted(images, key=key_fn)
