"""Backup and restore utilities for database maintenance."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from db_tools.config import BACKUP_DIR, BACKUP_RETENTION_COUNT, BACKUP_RETENTION_DAYS


class BackupManager:
    """Manages database backups with automatic cleanup."""

    def __init__(
        self,
        database_uri: str,
        backup_dir: Optional[Path] = None,
        retention_count: int = BACKUP_RETENTION_COUNT,
        retention_days: int = BACKUP_RETENTION_DAYS,
        app_root: Optional[str] = None,
    ):
        """Initialize backup manager.

        Args:
            database_uri: SQLAlchemy database URI
            backup_dir: Directory to store backups (default: ./backups)
            retention_count: Maximum number of backups to keep
            retention_days: Maximum age of backups in days
            app_root: Flask app root path for resolving relative SQLite URIs
        """
        self.database_uri = database_uri
        self.backup_dir = backup_dir or BACKUP_DIR
        self.retention_count = retention_count
        self.retention_days = retention_days
        self.app_root = app_root
        self._parsed_uri = urlparse(database_uri)

    @property
    def db_type(self) -> str:
        """Get database type from URI."""
        scheme = self._parsed_uri.scheme
        if scheme.startswith("sqlite"):
            return "sqlite"
        elif scheme.startswith("postgresql"):
            return "postgresql"
        else:
            return scheme

    @property
    def is_sqlite(self) -> bool:
        return self.db_type == "sqlite"

    @property
    def is_postgresql(self) -> bool:
        return self.db_type == "postgresql"

    def _ensure_backup_dir(self) -> None:
        """Create backup directory if it doesn't exist."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _generate_backup_filename(self, prefix: str = "backup") -> str:
        """Generate a timestamped backup filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.is_sqlite:
            return f"{prefix}_{timestamp}.db"
        else:
            return f"{prefix}_{timestamp}.sql"

    def create_backup(self, prefix: str = "backup") -> Path:
        """Create a database backup.

        Args:
            prefix: Prefix for the backup filename

        Returns:
            Path to the created backup file

        Raises:
            RuntimeError: If backup creation fails
        """
        self._ensure_backup_dir()
        filename = self._generate_backup_filename(prefix)
        backup_path = self.backup_dir / filename

        if self.is_sqlite:
            return self._backup_sqlite(backup_path)
        elif self.is_postgresql:
            return self._backup_postgresql(backup_path)
        else:
            raise RuntimeError(f"Unsupported database type: {self.db_type}")

    def _resolve_sqlite_path(self) -> str:
        """Resolve the actual SQLite database file path from the URI.

        Handles both relative (sqlite:///file.db) and absolute
        (sqlite:////absolute/path) URI formats. For relative paths,
        checks the Flask app root, CWD, and instance/ directory.
        """
        db_path = self._parsed_uri.path

        # Absolute path: sqlite:////absolute/path → parsed path starts with //
        if db_path.startswith("//"):
            return db_path[1:]

        # Relative path: sqlite:///file.db → parsed path = /file.db
        relative = db_path.lstrip("/")
        if not relative:
            return db_path

        # Check Flask app root directory (Flask-SQLAlchemy resolves relative
        # URIs from here)
        if self.app_root:
            app_root_path = os.path.join(self.app_root, relative)
            if os.path.exists(app_root_path):
                return os.path.abspath(app_root_path)

        # Check CWD
        if os.path.exists(relative):
            return os.path.abspath(relative)

        # Check Flask instance directory
        instance = os.path.join("instance", relative)
        if os.path.exists(instance):
            return os.path.abspath(instance)

        # If app_root was provided, use that as the canonical location even
        # if the file doesn't exist yet (e.g. first run before DB creation)
        if self.app_root:
            return os.path.abspath(os.path.join(self.app_root, relative))

        # Fallback: return relative path (will trigger meaningful error in caller)
        return relative

    def _backup_sqlite(self, backup_path: Path) -> Path:
        """Create SQLite backup by copying the database file."""
        db_path = self._resolve_sqlite_path()

        if not db_path or not os.path.exists(db_path):
            raise RuntimeError(f"SQLite database file not found: {db_path}")

        shutil.copy2(db_path, backup_path)
        return backup_path

    def _backup_postgresql(self, backup_path: Path) -> Path:
        """Create PostgreSQL backup using pg_dump."""
        # Build pg_dump command
        host = self._parsed_uri.hostname or "localhost"
        port = self._parsed_uri.port or 5432
        user = self._parsed_uri.username
        password = self._parsed_uri.password
        dbname = self._parsed_uri.path.lstrip("/")

        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password

        cmd = [
            "pg_dump",
            "-h", host,
            "-p", str(port),
            "-U", user,
            "-d", dbname,
            "-f", str(backup_path),
            "--format=plain",
        ]

        try:
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"pg_dump failed: {e.stderr}")
        except FileNotFoundError:
            raise RuntimeError("pg_dump not found. Is PostgreSQL client installed?")

        return backup_path

    def restore_backup(self, backup_path: Path) -> None:
        """Restore a database from backup.

        Args:
            backup_path: Path to the backup file

        Raises:
            RuntimeError: If restore fails
            FileNotFoundError: If backup file doesn't exist
        """
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        if self.is_sqlite:
            self._restore_sqlite(backup_path)
        elif self.is_postgresql:
            self._restore_postgresql(backup_path)
        else:
            raise RuntimeError(f"Unsupported database type: {self.db_type}")

    def _restore_sqlite(self, backup_path: Path) -> None:
        """Restore SQLite database by copying backup file."""
        db_path = self._resolve_sqlite_path()
        shutil.copy2(backup_path, db_path)

    def _restore_postgresql(self, backup_path: Path) -> None:
        """Restore PostgreSQL database using psql."""
        host = self._parsed_uri.hostname or "localhost"
        port = self._parsed_uri.port or 5432
        user = self._parsed_uri.username
        password = self._parsed_uri.password
        dbname = self._parsed_uri.path.lstrip("/")

        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password

        cmd = [
            "psql",
            "-h", host,
            "-p", str(port),
            "-U", user,
            "-d", dbname,
            "-f", str(backup_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"psql restore failed: {e.stderr}")

    def list_backups(self) -> List[Tuple[Path, datetime]]:
        """List all backup files with their timestamps.

        Returns:
            List of (path, modified_time) tuples, sorted newest first
        """
        if not self.backup_dir.exists():
            return []

        backups = []
        for f in self.backup_dir.iterdir():
            if f.is_file() and (f.suffix == ".db" or f.suffix == ".sql"):
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                backups.append((f, mtime))

        # Sort by modification time, newest first
        backups.sort(key=lambda x: x[1], reverse=True)
        return backups

    def cleanup_old_backups(self) -> List[Path]:
        """Remove old backups based on retention policy.

        Removes backups that exceed either:
        - Maximum count (keeps newest N backups)
        - Maximum age (removes backups older than X days)

        Returns:
            List of removed backup paths
        """
        backups = self.list_backups()
        removed = []
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)

        for i, (path, mtime) in enumerate(backups):
            should_remove = False

            # Remove if exceeds count limit
            if i >= self.retention_count:
                should_remove = True

            # Remove if older than retention period
            if mtime < cutoff_date:
                should_remove = True

            if should_remove:
                path.unlink()
                removed.append(path)

        return removed

    def verify_backup(self, backup_path: Path) -> bool:
        """Verify a backup file is valid.

        Args:
            backup_path: Path to the backup file

        Returns:
            True if backup appears valid, False otherwise
        """
        if not backup_path.exists():
            return False

        # Check file has reasonable size
        if backup_path.stat().st_size == 0:
            return False

        if self.is_sqlite:
            # Try to open as SQLite and run integrity check
            import sqlite3
            try:
                conn = sqlite3.connect(str(backup_path))
                cursor = conn.execute("PRAGMA integrity_check")
                result = cursor.fetchone()
                conn.close()
                return result[0] == "ok"
            except sqlite3.Error:
                return False
        else:
            # For SQL dumps, just check it's not empty and starts with valid SQL
            try:
                with open(backup_path, "r") as f:
                    first_line = f.readline()
                    return first_line.startswith("--") or first_line.startswith("SET")
            except Exception:
                return False
