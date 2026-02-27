"""Thumbnail generation and caching for image-viewer.

Thumbnails are cached in a .thumbnails folder within each image's directory.
Cache filenames are derived from the image filename + mtime.

Thumbnails are cached at a fixed size (CACHE_THUMBNAIL_SIZE) and scaled in the view.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

try:
    from PIL import Image as PilImage
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# Fixed cache size - thumbnails are generated at this size and scaled in the view
CACHE_THUMBNAIL_SIZE = 128

# Cache folder name within each image directory
CACHE_FOLDER = ".thumbnails"


def _cache_key(filepath: str, mtime: float) -> str:
    """Generate a unique cache filename for an image based on filename and mtime."""
    # Use just the filename and mtime for the key
    filename = os.path.basename(filepath)
    key = f"{filename}:{mtime}"
    return hashlib.sha256(key.encode()).hexdigest() + ".jpg"


def _get_cache_dir(filepath: str) -> Path:
    """Get the cache directory for an image (creates if needed)."""
    img_dir = Path(filepath).parent
    cache_dir = img_dir / CACHE_FOLDER
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_or_create_thumbnail(
    filepath: str,
    mtime: float,
    size: int = 200,
) -> Optional[str]:
    """Return the path to a cached thumbnail, generating it if necessary.

    Thumbnails are cached in a .thumbnails folder next to each image.
    The size parameter is ignored - thumbnails are scaled in the view.

    Args:
        filepath: Absolute path to the source image.
        mtime: File modification time (used to invalidate stale cache).
        size: Ignored (kept for API compatibility).

    Returns:
        Path to the cached thumbnail JPEG, or None if generation failed.
    """
    if not HAS_PILLOW:
        return None

    cache_dir = _get_cache_dir(filepath)
    cache_filename = _cache_key(filepath, mtime)
    cache_path = cache_dir / cache_filename

    if cache_path.exists():
        return str(cache_path)

    try:
        with PilImage.open(filepath) as img:
            # Convert to RGB for JPEG saving (handles RGBA, P mode, etc.)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            # Always use fixed cache size
            img.thumbnail((CACHE_THUMBNAIL_SIZE, CACHE_THUMBNAIL_SIZE), PilImage.LANCZOS)
            img.save(str(cache_path), "JPEG", quality=85, optimize=True)
        return str(cache_path)
    except Exception as e:
        # Silently fail — the viewer will fall back to loading the full image
        return None


def invalidate_cache(filepath: str, mtime: float) -> None:
    """Remove a cached thumbnail if it exists."""
    cache_dir = _get_cache_dir(filepath)
    cache_filename = _cache_key(filepath, mtime)
    cache_path = cache_dir / cache_filename
    if cache_path.exists():
        cache_path.unlink()


def clear_all_cache(base_dirs: Optional[list[str]] = None) -> int:
    """Remove all cached thumbnails under the given base directories.
    
    Args:
        base_dirs: List of base directories to scan for .thumbnails folders.
                   If None, uses current working directory.
    
    Returns:
        Number of cache files deleted.
    """
    count = 0
    search_dirs = base_dirs if base_dirs else ["."]
    
    for base_dir in search_dirs:
        base_path = Path(base_dir)
        if not base_path.exists():
            continue
        
        # Find all .thumbnails folders under this base directory
        for thumb_dir in base_path.rglob(CACHE_FOLDER):
            if thumb_dir.is_dir():
                try:
                    for cache_file in thumb_dir.iterdir():
                        if cache_file.is_file():
                            cache_file.unlink()
                            count += 1
                    # Remove the empty directory
                    thumb_dir.rmdir()
                except Exception:
                    pass  # Ignore errors during cleanup
    
    return count
