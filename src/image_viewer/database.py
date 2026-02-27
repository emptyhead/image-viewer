"""SQLite database layer for image-viewer.

The database is stored as `.image-viewer.db` in the base image directory.
If the directory is not writable, it falls back to
~/.config/image-viewer/<path-hash>.db.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .models import ImageInfo


# Database filename stored alongside images
DB_FILENAME = ".image-viewer.db"

# Fallback config directory
CONFIG_DIR = Path.home() / ".config" / "image-viewer"


def _get_db_path(base_dir: str) -> Path:
    """Determine the database path for a given base directory.

    Prefers placing the DB in the base_dir itself. Falls back to the
    config directory if base_dir is not writable.
    """
    local_db = Path(base_dir) / DB_FILENAME
    # Check if we can write to the directory
    if os.access(base_dir, os.W_OK):
        return local_db
    # Fallback: use config dir with a hash of the path
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path_hash = hashlib.sha256(base_dir.encode()).hexdigest()[:16]
    return CONFIG_DIR / f"{path_hash}.db"


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    directory TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    file_modified REAL DEFAULT 0,
    rating INTEGER DEFAULT 0,
    viewed INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    last_viewed REAL,
    first_seen REAL NOT NULL,
    thumbnail_cache TEXT
);

CREATE INDEX IF NOT EXISTS idx_filepath ON images(filepath);
CREATE INDEX IF NOT EXISTS idx_directory ON images(directory);
CREATE INDEX IF NOT EXISTS idx_rating ON images(rating);
CREATE INDEX IF NOT EXISTS idx_viewed ON images(viewed);
"""


class Database:
    """Manages the SQLite database for a single base directory."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = os.path.abspath(base_dir)
        self.db_path = _get_db_path(self.base_dir)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Open the database connection and create tables if needed."""
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrent access
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_CREATE_TABLE_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    # -------------------------------------------------------------------------
    # Image CRUD
    # -------------------------------------------------------------------------

    def upsert_image(self, image: ImageInfo) -> ImageInfo:
        """Insert or update a single image record. Returns the image with db_id set.

        For bulk operations, prefer batch_upsert_images() which is much faster.
        """
        return self.batch_upsert_images([image])[0]

    def batch_upsert_images(self, images: list[ImageInfo]) -> list[ImageInfo]:
        """Insert or update many image records in a single transaction.

        Existing images retain their rating, viewed status, view_count, and
        last_viewed. Only file metadata (size, mtime) is updated for existing rows.
        New images are inserted with default rating=0, viewed=False.

        Returns the list with db_id and persisted metadata filled in.
        """
        if not images:
            return images

        sql = """
        INSERT INTO images
            (filepath, filename, directory, file_size, file_modified,
             rating, viewed, view_count, last_viewed, first_seen)
        VALUES
            (:filepath, :filename, :directory, :file_size, :file_modified,
             :rating, :viewed, :view_count, :last_viewed, :first_seen)
        ON CONFLICT(filepath) DO UPDATE SET
            filename = excluded.filename,
            directory = excluded.directory,
            file_size = excluded.file_size,
            file_modified = excluded.file_modified
        """
        params = [
            {
                "filepath": img.filepath,
                "filename": img.filename,
                "directory": img.directory,
                "file_size": img.file_size,
                "file_modified": img.file_modified,
                "rating": img.rating,
                "viewed": int(img.viewed),
                "view_count": img.view_count,
                "last_viewed": img.last_viewed,
                "first_seen": img.first_seen,
            }
            for img in images
        ]

        with self.conn:  # Single transaction for all inserts
            self.conn.executemany(sql, params)

        # Fetch all rows back in one query to get ids and preserved metadata
        filepaths = [img.filepath for img in images]
        placeholders = ",".join("?" * len(filepaths))
        rows = self.conn.execute(
            f"SELECT * FROM images WHERE filepath IN ({placeholders})",
            filepaths,
        ).fetchall()

        # Build a lookup map
        row_map = {row["filepath"]: row for row in rows}

        for img in images:
            row = row_map.get(img.filepath)
            if row:
                img.db_id = row["id"]
                img.rating = row["rating"]
                img.viewed = bool(row["viewed"])
                img.view_count = row["view_count"]
                img.last_viewed = row["last_viewed"]

        return images

    def get_image(self, filepath: str) -> Optional[ImageInfo]:
        """Fetch a single image by filepath."""
        row = self.conn.execute(
            "SELECT * FROM images WHERE filepath = ?", (filepath,)
        ).fetchone()
        return self._row_to_image(row) if row else None

    def get_all_images(self) -> list[ImageInfo]:
        """Fetch all images in the database."""
        rows = self.conn.execute("SELECT * FROM images").fetchall()
        return [self._row_to_image(r) for r in rows]

    def update_rating(self, filepath: str, rating: int) -> None:
        """Update the rating for an image. Rating is clamped to 0-5."""
        rating = max(0, min(5, rating))
        self.conn.execute(
            "UPDATE images SET rating = ? WHERE filepath = ?",
            (rating, filepath),
        )
        self.conn.commit()

    def mark_viewed(self, filepath: str) -> None:
        """Mark an image as viewed, incrementing view_count and updating timestamps."""
        now = time.time()
        self.conn.execute(
            """
            UPDATE images
            SET viewed = 1,
                view_count = view_count + 1,
                last_viewed = ?
            WHERE filepath = ?
            """,
            (now, filepath),
        )
        self.conn.commit()

    def delete_image(self, filepath: str) -> None:
        """Delete an image record from the database."""
        self.conn.execute(
            "DELETE FROM images WHERE filepath = ?",
            (filepath,),
        )
        self.conn.commit()

    def set_thumbnail_cache(self, filepath: str, cache_path: str) -> None:
        """Store the path to a cached thumbnail."""
        self.conn.execute(
            "UPDATE images SET thumbnail_cache = ? WHERE filepath = ?",
            (cache_path, filepath),
        )
        self.conn.commit()

    def get_thumbnail_cache(self, filepath: str) -> Optional[str]:
        """Get the cached thumbnail path for an image, or None."""
        row = self.conn.execute(
            "SELECT thumbnail_cache FROM images WHERE filepath = ?", (filepath,)
        ).fetchone()
        if row and row["thumbnail_cache"]:
            return row["thumbnail_cache"]
        return None

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _row_to_image(row: sqlite3.Row) -> ImageInfo:
        """Convert a database row to an ImageInfo object."""
        return ImageInfo(
            db_id=row["id"],
            filepath=row["filepath"],
            filename=row["filename"],
            directory=row["directory"],
            file_size=row["file_size"] or 0,
            file_modified=row["file_modified"] or 0.0,
            rating=row["rating"] or 0,
            viewed=bool(row["viewed"]),
            view_count=row["view_count"] or 0,
            last_viewed=row["last_viewed"],
            first_seen=row["first_seen"] or 0.0,
        )


class MultiDatabase:
    """Manages multiple Database instances for multiple root paths.

    Merges results in memory when querying across all databases.
    """

    def __init__(self, base_dirs: list[str]) -> None:
        self._dbs: dict[str, Database] = {}
        for d in base_dirs:
            d = os.path.abspath(d)
            self._dbs[d] = Database(d)

    def connect(self) -> None:
        for db in self._dbs.values():
            db.connect()

    def close(self) -> None:
        for db in self._dbs.values():
            db.close()

    def __enter__(self) -> "MultiDatabase":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _db_for(self, filepath: str) -> Database:
        """Find the appropriate database for a given filepath."""
        filepath = os.path.abspath(filepath)
        # Find the db whose base_dir is the longest prefix of filepath
        best: Optional[Database] = None
        best_len = -1
        for base_dir, db in self._dbs.items():
            if filepath.startswith(base_dir + os.sep) or filepath == base_dir:
                if len(base_dir) > best_len:
                    best = db
                    best_len = len(base_dir)
        if best is None:
            # Fall back to first db
            best = next(iter(self._dbs.values()))
        return best

    def upsert_image(self, image: ImageInfo) -> ImageInfo:
        return self._db_for(image.filepath).upsert_image(image)

    def batch_upsert_images(self, images: list[ImageInfo]) -> list[ImageInfo]:
        """Batch upsert images, routing each to the correct database."""
        if not images:
            return images
        # Group images by their target database
        groups: dict[str, list[ImageInfo]] = {}
        for img in images:
            db = self._db_for(img.filepath)
            key = str(db.db_path)
            if key not in groups:
                groups[key] = []
            groups[key].append(img)
        # Batch upsert each group
        result = []
        for db in self._dbs.values():
            key = str(db.db_path)
            if key in groups:
                result.extend(db.batch_upsert_images(groups[key]))
        return result

    def get_image(self, filepath: str) -> Optional[ImageInfo]:
        return self._db_for(filepath).get_image(filepath)

    def get_all_images(self) -> list[ImageInfo]:
        results = []
        for db in self._dbs.values():
            results.extend(db.get_all_images())
        return results

    def update_rating(self, filepath: str, rating: int) -> None:
        self._db_for(filepath).update_rating(filepath, rating)

    def mark_viewed(self, filepath: str) -> None:
        self._db_for(filepath).mark_viewed(filepath)

    def delete_image(self, filepath: str) -> None:
        self._db_for(filepath).delete_image(filepath)

    def set_thumbnail_cache(self, filepath: str, cache_path: str) -> None:
        self._db_for(filepath).set_thumbnail_cache(filepath, cache_path)

    def get_thumbnail_cache(self, filepath: str) -> Optional[str]:
        return self._db_for(filepath).get_thumbnail_cache(filepath)
