"""
LRU/Versioned cache for PDF renders (PNG).
Uses SQLite for metadata storage and file system for actual render files.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry metadata."""
    
    id: int
    cache_key: str
    source_version: str
    file_path: str
    size_bytes: int
    created_at: datetime
    last_access_at: datetime


class RenderCacheManager:
    """
    LRU/Versioned cache manager for PDF renders.
    
    Features:
    - Version-based invalidation (etag/last_modified/hash)
    - Size-limited cache with LRU eviction
    - TTL-based expiration
    - Thread-safe SQLite metadata storage
    """
    
    _instance: Optional[RenderCacheManager] = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs) -> RenderCacheManager:
        """Singleton pattern for cache manager."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_size_mb: Optional[int] = None,
        ttl_days: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """
        Initialize cache manager.
        
        Args:
            cache_dir: Directory for cache files (default: temp dir)
            max_size_mb: Maximum cache size in MB (default: from settings)
            ttl_days: TTL in days for cache entries (default: from settings)
            enabled: Whether cache is enabled (default: from settings)
        """
        if getattr(self, "_initialized", False):
            return
        
        self.enabled = enabled if enabled is not None else settings.evidence_cache_enabled
        
        if cache_dir is not None:
            self.cache_dir = cache_dir
        elif settings.evidence_cache_dir:
            self.cache_dir = Path(settings.evidence_cache_dir)
        else:
            self.cache_dir = Path(tempfile.gettempdir()) / "aizoomdoc_evidence_cache"
        
        self.max_size_bytes = (max_size_mb or settings.evidence_cache_max_mb) * 1024 * 1024
        self.ttl_days = ttl_days if ttl_days is not None else settings.evidence_cache_ttl_days
        
        # Create directories
        self.renders_dir = self.cache_dir / "renders"
        self.renders_dir.mkdir(parents=True, exist_ok=True)
        
        # SQLite database path
        self.db_path = self.cache_dir / "cache_metadata.db"
        
        # Initialize database
        self._init_db()
        self._initialized = True
        
        logger.info(
            f"RenderCacheManager initialized: dir={self.cache_dir}, "
            f"max_size={self.max_size_bytes // (1024*1024)}MB, "
            f"ttl={self.ttl_days}days, enabled={self.enabled}"
        )
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get SQLite connection with row factory."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self) -> None:
        """Initialize SQLite database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT NOT NULL UNIQUE,
                    source_version TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    last_access_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_key ON cache_entries(cache_key)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_last_access ON cache_entries(last_access_at)
            """)
            conn.commit()
    
    def _hash_key(self, key: str) -> str:
        """Generate MD5 hash of key."""
        return hashlib.md5(key.encode("utf-8")).hexdigest()
    
    def _make_cache_key(
        self,
        source_id: str,
        source_version: str,
        page: int,
        dpi: int,
        bbox_norm: Optional[Tuple[float, float, float, float]] = None,
    ) -> str:
        """
        Build a unique cache key.
        
        Args:
            source_id: Source identifier (r2_key or URL)
            source_version: Version of source (etag/last_modified/hash)
            page: Page number
            dpi: DPI for rendering
            bbox_norm: Optional normalized bounding box for ROI
            
        Returns:
            Unique cache key string
        """
        parts = [source_id, source_version, str(page), str(dpi)]
        if bbox_norm:
            # Round bbox to 4 decimal places for consistent keys
            rounded = tuple(round(v, 4) for v in bbox_norm)
            parts.append(str(rounded))
        return ":".join(parts)
    
    def _get_file_path(self, cache_key: str) -> Path:
        """Get file path for cache key."""
        hashed = self._hash_key(cache_key)
        return self.renders_dir / f"{hashed}.png"
    
    def get(
        self,
        source_id: str,
        source_version: str,
        page: int,
        dpi: int,
        bbox_norm: Optional[Tuple[float, float, float, float]] = None,
    ) -> Optional[bytes]:
        """
        Get cached render if exists and valid.
        
        Args:
            source_id: Source identifier
            source_version: Version of source
            page: Page number
            dpi: DPI
            bbox_norm: Optional ROI bounding box
            
        Returns:
            PNG bytes if cache hit, None otherwise
        """
        if not self.enabled:
            return None
        
        cache_key = self._make_cache_key(source_id, source_version, page, dpi, bbox_norm)
        
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM cache_entries WHERE cache_key = ?",
                (cache_key,)
            ).fetchone()
            
            if row is None:
                return None
            
            # Check if entry is expired by TTL
            created_at = datetime.fromisoformat(row["created_at"])
            if datetime.utcnow() - created_at > timedelta(days=self.ttl_days):
                # Entry expired, remove it
                self._remove_entry(conn, row["id"], row["file_path"])
                return None
            
            # Check if file exists
            file_path = Path(row["file_path"])
            if not file_path.exists():
                # File missing, remove metadata
                self._remove_entry(conn, row["id"], row["file_path"])
                return None
            
            # Update last access time
            conn.execute(
                "UPDATE cache_entries SET last_access_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), row["id"])
            )
            conn.commit()
            
            # Read and return file
            try:
                return file_path.read_bytes()
            except Exception as e:
                logger.error(f"Error reading cache file {file_path}: {e}")
                return None
    
    def put(
        self,
        source_id: str,
        source_version: str,
        page: int,
        dpi: int,
        png_bytes: bytes,
        bbox_norm: Optional[Tuple[float, float, float, float]] = None,
    ) -> bool:
        """
        Store render in cache.
        
        Args:
            source_id: Source identifier
            source_version: Version of source
            page: Page number
            dpi: DPI
            png_bytes: PNG image bytes
            bbox_norm: Optional ROI bounding box
            
        Returns:
            True if successfully cached
        """
        if not self.enabled:
            return False
        
        cache_key = self._make_cache_key(source_id, source_version, page, dpi, bbox_norm)
        file_path = self._get_file_path(cache_key)
        size_bytes = len(png_bytes)
        now = datetime.utcnow().isoformat()
        
        try:
            # Ensure we have space
            self._ensure_space(size_bytes)
            
            # Write file
            file_path.write_bytes(png_bytes)
            
            # Upsert metadata
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO cache_entries 
                        (cache_key, source_version, file_path, size_bytes, created_at, last_access_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        source_version = excluded.source_version,
                        file_path = excluded.file_path,
                        size_bytes = excluded.size_bytes,
                        created_at = excluded.created_at,
                        last_access_at = excluded.last_access_at
                """, (cache_key, source_version, str(file_path), size_bytes, now, now))
                conn.commit()
            
            return True
        except Exception as e:
            logger.error(f"Error storing cache entry: {e}")
            # Clean up file if it was written
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass
            return False
    
    def _remove_entry(self, conn: sqlite3.Connection, entry_id: int, file_path: str) -> None:
        """Remove cache entry and its file."""
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.warning(f"Error deleting cache file {file_path}: {e}")
        
        conn.execute("DELETE FROM cache_entries WHERE id = ?", (entry_id,))
        conn.commit()
    
    def _get_total_size(self, conn: sqlite3.Connection) -> int:
        """Get total size of all cached files."""
        result = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM cache_entries").fetchone()
        return result[0]
    
    def _ensure_space(self, needed_bytes: int) -> None:
        """Ensure there's enough space for new entry, evicting LRU entries if needed."""
        with self._get_connection() as conn:
            total_size = self._get_total_size(conn)
            
            # Also check TTL expired entries and remove them first
            cutoff = (datetime.utcnow() - timedelta(days=self.ttl_days)).isoformat()
            expired = conn.execute(
                "SELECT id, file_path FROM cache_entries WHERE created_at < ?",
                (cutoff,)
            ).fetchall()
            
            for row in expired:
                self._remove_entry(conn, row["id"], row["file_path"])
                total_size = self._get_total_size(conn)
            
            # Evict LRU entries until we have enough space
            while total_size + needed_bytes > self.max_size_bytes:
                # Get oldest accessed entry
                oldest = conn.execute(
                    "SELECT id, file_path, size_bytes FROM cache_entries ORDER BY last_access_at ASC LIMIT 1"
                ).fetchone()
                
                if oldest is None:
                    break
                
                self._remove_entry(conn, oldest["id"], oldest["file_path"])
                total_size -= oldest["size_bytes"]
                logger.debug(f"Evicted LRU cache entry: {oldest['file_path']}")
    
    def invalidate(self, source_id: str) -> int:
        """
        Invalidate all cache entries for a source.
        
        Args:
            source_id: Source identifier to invalidate
            
        Returns:
            Number of entries invalidated
        """
        with self._get_connection() as conn:
            # Find all entries starting with source_id
            pattern = source_id + ":%"
            rows = conn.execute(
                "SELECT id, file_path FROM cache_entries WHERE cache_key LIKE ?",
                (pattern,)
            ).fetchall()
            
            count = 0
            for row in rows:
                self._remove_entry(conn, row["id"], row["file_path"])
                count += 1
            
            return count
    
    def clear(self) -> int:
        """
        Clear all cache entries.
        
        Returns:
            Number of entries cleared
        """
        with self._get_connection() as conn:
            rows = conn.execute("SELECT id, file_path FROM cache_entries").fetchall()
            
            count = 0
            for row in rows:
                self._remove_entry(conn, row["id"], row["file_path"])
                count += 1
            
            return count
    
    def get_stats(self) -> dict:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        with self._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
            total_size = self._get_total_size(conn)
            oldest = conn.execute(
                "SELECT MIN(created_at) FROM cache_entries"
            ).fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(created_at) FROM cache_entries"
            ).fetchone()[0]
        
        return {
            "enabled": self.enabled,
            "entries_count": count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "max_size_mb": self.max_size_bytes // (1024 * 1024),
            "ttl_days": self.ttl_days,
            "oldest_entry": oldest,
            "newest_entry": newest,
            "cache_dir": str(self.cache_dir),
        }


# Global cache manager instance
_cache_manager: Optional[RenderCacheManager] = None


def get_render_cache() -> RenderCacheManager:
    """Get or create the global render cache manager."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = RenderCacheManager()
    return _cache_manager

