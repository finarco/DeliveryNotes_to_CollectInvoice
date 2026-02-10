"""Interactive database maintenance tool."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import g

from extensions import db
from db_tools.core.database_inspector import DatabaseInspector


def _current_user_id():
    """Return the current user's ID for audit logging, or None."""
    user = getattr(g, "current_user", None)
    return user.id if user else None


class MaintenanceTool:
    """Interactive database maintenance operations."""

    def __init__(self, inspector: Optional[DatabaseInspector] = None):
        """Initialize maintenance tool.

        Args:
            inspector: DatabaseInspector instance
        """
        self.inspector = inspector or DatabaseInspector()

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive database statistics.

        Returns:
            Dict with categorized statistics
        """
        return self.inspector.get_statistics()

    def check_integrity(self) -> List[Dict[str, Any]]:
        """Check database for integrity issues.

        Returns:
            List of issues found
        """
        return self.inspector.check_integrity()

    def get_table_counts(self) -> Dict[str, int]:
        """Get row counts for all tables.

        Returns:
            Dict mapping table name to count
        """
        return self.inspector.get_table_counts()

    def reset_number_sequences(self) -> Dict[str, int]:
        """Reset all number sequences to continue from current max values.

        Returns:
            Dict mapping entity_type to new last_value
        """
        import models

        results = {}

        # Entity types and their number columns
        entity_number_map = {
            "order": (models.Order, "order_number"),
            "delivery_note": (models.DeliveryNote, "note_number"),
            "invoice": (models.Invoice, "invoice_number"),
        }

        for entity_type, (model, number_col) in entity_number_map.items():
            # Find max existing number
            max_record = (
                db.session.query(model)
                .filter(getattr(model, number_col).isnot(None))
                .order_by(getattr(model, number_col).desc())
                .first()
            )

            # Extract numeric part and update sequence
            if max_record:
                number = getattr(max_record, number_col)
                # Try to extract the counter part (assumes format like XX-YYYY-NNNN)
                import re
                match = re.search(r"(\d+)$", number or "")
                if match:
                    max_val = int(match.group(1))
                    results[entity_type] = max_val
                else:
                    results[entity_type] = 0
            else:
                results[entity_type] = 0

        # Update NumberSequence table
        for entity_type, last_value in results.items():
            seq = (
                db.session.query(models.NumberSequence)
                .filter_by(entity_type=entity_type)
                .first()
            )
            if seq:
                seq.last_value = last_value

        db.session.commit()
        return results

    def unlock_document(
        self, entity_type: str, entity_id: int
    ) -> Dict[str, Any]:
        """Unlock a locked document.

        Args:
            entity_type: 'order', 'delivery_note', or 'invoice'
            entity_id: ID of the document

        Returns:
            Dict with operation result
        """
        import models

        model_map = {
            "order": models.Order,
            "delivery_note": models.DeliveryNote,
            "invoice": models.Invoice,
        }

        model = model_map.get(entity_type)
        if not model:
            return {"success": False, "error": f"Unknown entity type: {entity_type}"}

        record = db.session.query(model).get(entity_id)
        if not record:
            return {"success": False, "error": f"{entity_type} {entity_id} not found"}

        if not hasattr(record, "is_locked"):
            return {"success": False, "error": f"{entity_type} does not have is_locked field"}

        was_locked = record.is_locked
        record.is_locked = False

        # Log the action
        log_entry = models.AuditLog(
            user_id=_current_user_id(),
            action="db_tool_unlock",
            entity_type=entity_type,
            entity_id=entity_id,
            details=f"was_locked={was_locked}",
        )
        db.session.add(log_entry)
        db.session.commit()

        return {
            "success": True,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "was_locked": was_locked,
        }

    def repair_orphaned_records(self) -> Dict[str, int]:
        """Delete orphaned records that reference non-existent parents.

        Returns:
            Dict mapping entity type to count of deleted records
        """
        import models

        deleted = {}

        # Orphaned OrderItems
        orphaned = (
            db.session.query(models.OrderItem)
            .filter(~models.OrderItem.order_id.in_(
                db.session.query(models.Order.id)
            ))
            .all()
        )
        if orphaned:
            for record in orphaned:
                db.session.delete(record)
            deleted["order_item"] = len(orphaned)

        # Orphaned DeliveryItems
        orphaned = (
            db.session.query(models.DeliveryItem)
            .filter(~models.DeliveryItem.delivery_note_id.in_(
                db.session.query(models.DeliveryNote.id)
            ))
            .all()
        )
        if orphaned:
            for record in orphaned:
                db.session.delete(record)
            deleted["delivery_item"] = len(orphaned)

        # Orphaned InvoiceItems
        orphaned = (
            db.session.query(models.InvoiceItem)
            .filter(~models.InvoiceItem.invoice_id.in_(
                db.session.query(models.Invoice.id)
            ))
            .all()
        )
        if orphaned:
            for record in orphaned:
                db.session.delete(record)
            deleted["invoice_item"] = len(orphaned)

        # Orphaned DeliveryNoteOrders
        orphaned = (
            db.session.query(models.DeliveryNoteOrder)
            .filter(~models.DeliveryNoteOrder.delivery_note_id.in_(
                db.session.query(models.DeliveryNote.id)
            ))
            .all()
        )
        if orphaned:
            for record in orphaned:
                db.session.delete(record)
            deleted["delivery_note_order"] = len(orphaned)

        if deleted:
            # Log the action
            log_entry = models.AuditLog(
                user_id=_current_user_id(),
                action="db_tool_repair_orphans",
                entity_type="database",
                entity_id=None,
                details=str(deleted),
            )
            db.session.add(log_entry)
            db.session.commit()

        return deleted

    def export_entity_to_csv(
        self, entity_type: str, output_path: str
    ) -> Dict[str, Any]:
        """Export an entity type to CSV.

        Args:
            entity_type: The entity type to export
            output_path: Path to write CSV file

        Returns:
            Dict with operation result
        """
        import csv

        model = self.inspector.get_model_class(entity_type)
        if not model:
            return {"success": False, "error": f"Unknown entity type: {entity_type}"}

        records = db.session.query(model).all()
        if not records:
            return {"success": True, "count": 0, "path": output_path}

        # Get column names from first record
        columns = [c.name for c in model.__table__.columns]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()

            for record in records:
                row = {}
                for col in columns:
                    value = getattr(record, col, None)
                    if isinstance(value, datetime):
                        value = value.isoformat()
                    row[col] = value
                writer.writerow(row)

        return {"success": True, "count": len(records), "path": output_path}

    def get_fk_dependencies(self, entity_type: str) -> Dict[str, Any]:
        """Get foreign key dependencies for an entity type.

        Args:
            entity_type: The entity type to inspect

        Returns:
            Dict with incoming and outgoing FK relationships
        """
        return self.inspector.get_foreign_key_references(entity_type)

    def execute_read_only_query(self, sql: str) -> Dict[str, Any]:
        """Execute a read-only SQL query.

        Args:
            sql: SQL query to execute (must be SELECT)

        Returns:
            Dict with columns and rows
        """
        import re
        import sqlite3

        sql_stripped = sql.strip()
        sql_upper = sql_stripped.upper()

        # Only allow SELECT statements
        if not sql_upper.startswith("SELECT"):
            return {
                "success": False,
                "error": "Only SELECT queries are allowed",
            }

        # Block dangerous keywords using word boundary matching
        # to avoid false positives like "UPDATED_AT" matching "UPDATE"
        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"]
        for keyword in dangerous:
            if re.search(r'\b' + keyword + r'\b', sql_upper):
                return {
                    "success": False,
                    "error": f"Query contains forbidden keyword: {keyword}",
                }

        # Block multiple statements (semicolons)
        if ";" in sql_stripped.rstrip(";"):
            return {
                "success": False,
                "error": "Multiple statements are not allowed",
            }

        try:
            # Use a separate read-only connection for safety
            db_uri = db.engine.url.render_as_string(hide_password=False)
            db_path = db_uri.replace("sqlite:///", "")
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                cursor = conn.execute(sql_stripped)
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = [list(row) for row in cursor.fetchall()]
                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                }
            finally:
                conn.close()
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
