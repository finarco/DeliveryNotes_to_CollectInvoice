"""Database wipe operation with safety mechanisms."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from extensions import db
from db_tools.config import DELETION_ORDER, CONFIG_TABLES
from db_tools.core.backup import BackupManager
from db_tools.core.database_inspector import DatabaseInspector


class DatabaseWiper:
    """Safe database wipe with multiple safety checks.

    Safety features:
    - Automatic backup creation before wipe
    - Double confirmation required (typed phrase)
    - Environment check (warns on PROD-like URIs)
    - FK-aware deletion order
    - Preserved tables option (keep config tables)
    - Dry-run mode to preview what would be deleted
    - Audit log entry before and after wipe
    """

    CONFIRMATION_PHRASE = "DELETE ALL DATA"

    def __init__(
        self,
        database_uri: str,
        backup_manager: Optional[BackupManager] = None,
        inspector: Optional[DatabaseInspector] = None,
        app_root: Optional[str] = None,
    ):
        """Initialize wiper.

        Args:
            database_uri: SQLAlchemy database URI
            backup_manager: BackupManager instance (created if not provided)
            inspector: DatabaseInspector instance (created if not provided)
            app_root: Flask app root path for resolving relative SQLite URIs
        """
        self.database_uri = database_uri
        self.backup_manager = backup_manager or BackupManager(
            database_uri, app_root=app_root
        )
        self.inspector = inspector or DatabaseInspector()
        self._progress_callback: Optional[Callable[[str, int, int], None]] = None

    def set_progress_callback(
        self, callback: Callable[[str, int, int], None]
    ) -> None:
        """Set a callback for progress updates.

        Args:
            callback: Function(table_name, current, total) called during wipe
        """
        self._progress_callback = callback

    def _report_progress(self, table_name: str, current: int, total: int) -> None:
        """Report progress to callback if set."""
        if self._progress_callback:
            self._progress_callback(table_name, current, total)

    def is_production_environment(self) -> bool:
        """Check if we appear to be connected to a production database.

        Returns:
            True if database URI contains production indicators
        """
        uri_lower = self.database_uri.lower()
        prod_indicators = ["production", "prod", "live", "master"]
        env = os.environ.get("FLASK_ENV", "").lower()

        return (
            any(indicator in uri_lower for indicator in prod_indicators)
            or env == "production"
        )

    def get_deletion_preview(
        self, include_config: bool = False
    ) -> List[Tuple[str, int]]:
        """Preview what would be deleted.

        Args:
            include_config: Whether to include config tables

        Returns:
            List of (table_name, row_count) tuples
        """
        return self.inspector.get_deletion_preview(include_config)

    def validate_confirmation(self, user_input: str) -> bool:
        """Validate user confirmation phrase.

        Args:
            user_input: What the user typed

        Returns:
            True if confirmation matches exactly
        """
        return user_input.strip() == self.CONFIRMATION_PHRASE

    def wipe(
        self,
        *,
        include_config: bool = False,
        create_backup: bool = True,
        dry_run: bool = False,
        reset_sequences: bool = True,
    ) -> dict:
        """Execute database wipe operation.

        Args:
            include_config: Whether to delete config tables
            create_backup: Whether to create backup before wipe
            dry_run: If True, only show what would be deleted
            reset_sequences: Whether to reset number sequences after wipe

        Returns:
            Dict with operation results:
            - success: bool
            - backup_path: Path or None
            - deleted_counts: Dict[table_name, count]
            - errors: List[str]
            - dry_run: bool
        """
        result = {
            "success": False,
            "backup_path": None,
            "deleted_counts": {},
            "errors": [],
            "dry_run": dry_run,
            "started_at": datetime.utcnow().isoformat(),
        }

        # Get preview
        preview = self.get_deletion_preview(include_config)
        total_tables = len(preview)

        if dry_run:
            result["deleted_counts"] = dict(preview)
            result["success"] = True
            return result

        # Create backup if requested
        if create_backup:
            try:
                backup_path = self.backup_manager.create_backup(prefix="pre_wipe")
                result["backup_path"] = str(backup_path)

                # Verify backup
                if not self.backup_manager.verify_backup(backup_path):
                    result["errors"].append("Backup verification failed")
                    return result
            except Exception as e:
                result["errors"].append(f"Backup failed: {str(e)}")
                return result

        # Log start of wipe operation
        try:
            self._log_wipe_start(include_config)
        except Exception:
            pass  # Don't fail if audit logging fails

        # Execute deletion in FK-safe order
        try:
            for i, table_name in enumerate(DELETION_ORDER):
                # Skip config tables if not included
                if not include_config and table_name in CONFIG_TABLES:
                    continue

                model = self.inspector.get_model_class(table_name)
                if not model:
                    continue

                self._report_progress(table_name, i + 1, total_tables)

                try:
                    count = db.session.query(model).delete()
                    result["deleted_counts"][table_name] = count
                except Exception as e:
                    result["errors"].append(f"Error deleting {table_name}: {str(e)}")
                    db.session.rollback()
                    return result

            # Reset number sequences if requested
            if reset_sequences:
                try:
                    self._reset_sequences()
                except Exception as e:
                    result["errors"].append(f"Error resetting sequences: {str(e)}")

            # Commit all changes
            db.session.commit()
            result["success"] = True

            # Log completion
            try:
                self._log_wipe_complete(result["deleted_counts"])
                db.session.commit()
            except Exception:
                pass

            # Cleanup old backups
            try:
                self.backup_manager.cleanup_old_backups()
            except Exception:
                pass

        except Exception as e:
            db.session.rollback()
            result["errors"].append(f"Wipe failed: {str(e)}")

        result["completed_at"] = datetime.utcnow().isoformat()
        return result

    def _reset_sequences(self) -> None:
        """Reset all number sequences to 0."""
        import models

        db.session.query(models.NumberSequence).update({"last_value": 0})

    def _log_wipe_start(self, include_config: bool) -> None:
        """Log the start of a wipe operation to audit log."""
        import models

        log_entry = models.AuditLog(
            user_id=None,  # System operation
            action="db_tool_wipe_started",
            entity_type="database",
            entity_id=None,
            details=f"include_config={include_config}",
        )
        db.session.add(log_entry)
        db.session.flush()

    def _log_wipe_complete(self, deleted_counts: dict) -> None:
        """Log the completion of a wipe operation to audit log."""
        import models

        total_deleted = sum(deleted_counts.values())
        tables_affected = len([c for c in deleted_counts.values() if c > 0])

        log_entry = models.AuditLog(
            user_id=None,
            action="db_tool_wipe_completed",
            entity_type="database",
            entity_id=None,
            details=f"total_deleted={total_deleted}, tables_affected={tables_affected}",
        )
        db.session.add(log_entry)
