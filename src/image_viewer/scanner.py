"""Image scanner: discovers image files in directories and upserts them into the database.

Performance notes:
- Directory walking is fast (just stat calls).
- All discovered images are collected first, then batch-upserted in a single
  SQLite transaction — orders of magnitude faster than per-image commits.
- Progress callbacks are called during the walk phase so the UI can update.
"""

from __future__ import annotations

import os
from typing import Callable, Iterator, Optional

from .models import ImageInfo, SUPPORTED_EXTENSIONS
from .database import MultiDatabase


def iter_images(
    paths: list[str],
    recursive: bool = True,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> Iterator[ImageInfo]:
    """Yield ImageInfo objects for all supported images found in the given paths.

    Args:
        paths: List of file or directory paths to scan.
        recursive: If True, scan directories recursively.
        progress_callback: Optional callable(filepath, count_so_far) called for
            each image found during the walk.
    """
    count = 0
    for path in paths:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                count += 1
                if progress_callback:
                    progress_callback(path, count)
                yield ImageInfo.from_path(path)
        elif os.path.isdir(path):
            if recursive:
                for root, dirs, files in os.walk(path, followlinks=False):
                    # Skip hidden directories (like .thumbnails, .git, etc.)
                    dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
                    for filename in sorted(files):
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in SUPPORTED_EXTENSIONS:
                            filepath = os.path.join(root, filename)
                            count += 1
                            if progress_callback:
                                progress_callback(filepath, count)
                            yield ImageInfo.from_path(filepath)
            else:
                for filename in sorted(os.listdir(path)):
                    filepath = os.path.join(path, filename)
                    if os.path.isfile(filepath):
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in SUPPORTED_EXTENSIONS:
                            count += 1
                            if progress_callback:
                                progress_callback(filepath, count)
                            yield ImageInfo.from_path(filepath)


def scan_and_store(
    paths: list[str],
    db: MultiDatabase,
    recursive: bool = True,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> list[ImageInfo]:
    """Scan paths for images, batch-upsert them into the database, and return the list.

    All images are collected first (fast directory walk), then stored in a single
    SQLite transaction. Existing images retain their rating and viewed status.

    Args:
        paths: List of file or directory paths to scan.
        db: The MultiDatabase instance to upsert into.
        recursive: If True, scan directories recursively.
        progress_callback: Optional callable(filepath, count) called during the walk.

    Returns:
        List of ImageInfo objects with db_id set and existing metadata preserved.
    """
    # Phase 1: Collect all images (fast — just os.walk + stat)
    images = list(iter_images(paths, recursive=recursive, progress_callback=progress_callback))

    # Phase 2: Batch upsert in a single transaction (fast — one commit)
    if images:
        images = db.batch_upsert_images(images)

    return images


def get_base_dirs(paths: list[str]) -> list[str]:
    """Determine the unique base directories for a list of paths.

    For file paths, the base dir is the parent directory.
    For directory paths, the base dir is the directory itself.
    Returns a deduplicated list of absolute paths.
    """
    base_dirs: list[str] = []
    seen: set[str] = set()
    for path in paths:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            base = os.path.dirname(path)
        else:
            base = path
        if base not in seen:
            seen.add(base)
            base_dirs.append(base)
    return base_dirs
