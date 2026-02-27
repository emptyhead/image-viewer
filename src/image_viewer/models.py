"""Data models for image-viewer."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


# Supported image file extensions (lowercase)
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
})


@dataclass
class ImageInfo:
    """Represents a single image with its metadata, rating, and viewed status."""

    # Core identity
    filepath: str           # Absolute path to the image file
    filename: str           # Just the filename (basename)
    directory: str          # Parent directory path

    # File metadata
    file_size: int = 0          # File size in bytes
    file_modified: float = 0.0  # mtime as Unix timestamp

    # User data
    rating: int = 0             # 0=unrated, 1-5 stars
    viewed: bool = False        # Whether the image has been viewed
    view_count: int = 0         # Total number of times viewed
    last_viewed: Optional[float] = None   # Unix timestamp of last view
    first_seen: float = 0.0     # Unix timestamp when first scanned

    # Internal
    db_id: Optional[int] = None  # SQLite row ID (None if not yet persisted)

    @classmethod
    def from_path(cls, filepath: str) -> "ImageInfo":
        """Create an ImageInfo from a file path, reading file metadata."""
        import time
        filepath = os.path.abspath(filepath)
        stat = os.stat(filepath)
        return cls(
            filepath=filepath,
            filename=os.path.basename(filepath),
            directory=os.path.dirname(filepath),
            file_size=stat.st_size,
            file_modified=stat.st_mtime,
            first_seen=time.time(),
        )

    @property
    def extension(self) -> str:
        """Return the lowercase file extension including the dot."""
        return os.path.splitext(self.filename)[1].lower()

    @property
    def is_supported(self) -> bool:
        """Return True if the file extension is supported."""
        return self.extension in SUPPORTED_EXTENSIONS

    @property
    def display_name(self) -> str:
        """Return a short display name (filename without extension)."""
        return os.path.splitext(self.filename)[0]

    def __repr__(self) -> str:
        return (
            f"ImageInfo(filename={self.filename!r}, rating={self.rating}, "
            f"viewed={self.viewed}, view_count={self.view_count})"
        )


@dataclass
class AppConfig:
    """Application configuration, merged from config file and CLI arguments."""

    # Scanning
    recursive: bool = True
    paths: list[str] = field(default_factory=list)

    # Display mode
    start_slideshow: bool = False
    fullscreen: bool = True

    # Thumbnail view
    thumbnail_size: int = 200
    sort: str = "unviewed"  # alpha|directory|unviewed|viewed|rating|rating-desc

    # Slideshow
    slideshow_time: float = 5.0       # Base display time in seconds
    slideshow_order: str = "forward"  # forward|backward|random
    loop: bool = False
    rating_multiplier: float = 0.5    # Extra seconds per rating star

    # Appearance
    highlight_color: str = "#4a90d9"
    unviewed_indicator: str = "border"  # border|dot|none

    def display_time_for(self, rating: int) -> float:
        """Calculate display time for an image with the given rating."""
        return self.slideshow_time + rating * self.rating_multiplier
