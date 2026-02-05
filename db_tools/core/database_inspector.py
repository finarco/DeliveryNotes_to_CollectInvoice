"""Database inspection utilities for maintenance operations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Type

from sqlalchemy import inspect, func
from sqlalchemy.orm import Session

from extensions import db
from db_tools.config import DELETION_ORDER, CONFIG_TABLES, ENTITY_MODEL_MAP


class DatabaseInspector:
    """Inspects database structure and provides statistics."""

    def __init__(self, session: Optional[Session] = None):
        """Initialize inspector.

        Args:
            session: SQLAlchemy session (uses db.session if not provided)
        """
        self.session = session or db.session

    def get_model_class(self, table_name: str) -> Optional[Type]:
        """Get SQLAlchemy model class for a table name.

        Args:
            table_name: Snake_case table name (e.g., 'delivery_note')

        Returns:
            Model class or None if not found
        """
        import models

        class_name = ENTITY_MODEL_MAP.get(table_name)
        if class_name:
            return getattr(models, class_name, None)
        return None

    def get_table_counts(self) -> Dict[str, int]:
        """Get row counts for all tables.

        Returns:
            Dict mapping table name to row count
        """
        counts = {}
        for table_name in DELETION_ORDER:
            model = self.get_model_class(table_name)
            if model:
                try:
                    counts[table_name] = self.session.query(model).count()
                except Exception:
                    counts[table_name] = -1  # Error
            else:
                counts[table_name] = -1
        return counts

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive database statistics.

        Returns:
            Dict with categorized statistics
        """
        import models

        stats = {
            "users": {
                "total": self.session.query(models.User).count(),
                "active": self.session.query(models.User).filter_by(is_active=True).count(),
            },
            "partners": {
                "total": self.session.query(models.Partner).count(),
                "active": self.session.query(models.Partner).filter_by(
                    is_active=True, is_deleted=False
                ).count(),
            },
            "products": {
                "total": self.session.query(models.Product).count(),
                "active": self.session.query(models.Product).filter_by(is_active=True).count(),
            },
            "bundles": {
                "total": self.session.query(models.Bundle).count(),
                "active": self.session.query(models.Bundle).filter_by(is_active=True).count(),
            },
            "orders": {
                "total": self.session.query(models.Order).count(),
                "confirmed": self.session.query(models.Order).filter_by(confirmed=True).count(),
                "locked": self.session.query(models.Order).filter_by(is_locked=True).count(),
            },
            "delivery_notes": {
                "total": self.session.query(models.DeliveryNote).count(),
                "confirmed": self.session.query(models.DeliveryNote).filter_by(confirmed=True).count(),
                "invoiced": self.session.query(models.DeliveryNote).filter_by(invoiced=True).count(),
            },
            "invoices": {
                "total": self.session.query(models.Invoice).count(),
                "draft": self.session.query(models.Invoice).filter_by(status="draft").count(),
                "sent": self.session.query(models.Invoice).filter_by(status="sent").count(),
                "paid": self.session.query(models.Invoice).filter_by(status="paid").count(),
            },
            "vehicles": {
                "total": self.session.query(models.Vehicle).count(),
                "active": self.session.query(models.Vehicle).filter_by(active=True).count(),
            },
            "audit_log": {
                "total": self.session.query(models.AuditLog).count(),
            },
        }
        return stats

    def check_integrity(self) -> List[Dict[str, Any]]:
        """Check for common data integrity issues.

        Returns:
            List of issue dicts with 'type', 'description', 'count' keys
        """
        import models

        issues = []

        # Check for orphaned order items
        orphaned_order_items = (
            self.session.query(models.OrderItem)
            .filter(~models.OrderItem.order_id.in_(
                self.session.query(models.Order.id)
            ))
            .count()
        )
        if orphaned_order_items > 0:
            issues.append({
                "type": "orphan",
                "entity": "OrderItem",
                "description": "Order items without parent order",
                "count": orphaned_order_items,
            })

        # Check for orphaned delivery items
        orphaned_delivery_items = (
            self.session.query(models.DeliveryItem)
            .filter(~models.DeliveryItem.delivery_note_id.in_(
                self.session.query(models.DeliveryNote.id)
            ))
            .count()
        )
        if orphaned_delivery_items > 0:
            issues.append({
                "type": "orphan",
                "entity": "DeliveryItem",
                "description": "Delivery items without parent delivery note",
                "count": orphaned_delivery_items,
            })

        # Check for inactive products still in orders
        inactive_products_in_orders = (
            self.session.query(models.Product)
            .filter(
                models.Product.is_active == False,
                models.Product.id.in_(
                    self.session.query(models.OrderItem.product_id)
                ),
            )
            .count()
        )
        if inactive_products_in_orders > 0:
            issues.append({
                "type": "warning",
                "entity": "Product",
                "description": "Inactive products referenced in orders",
                "count": inactive_products_in_orders,
            })

        # Check for duplicate invoice numbers
        duplicate_invoices = (
            self.session.query(
                models.Invoice.invoice_number,
                func.count(models.Invoice.id).label("cnt"),
            )
            .filter(models.Invoice.invoice_number.isnot(None))
            .group_by(models.Invoice.invoice_number)
            .having(func.count(models.Invoice.id) > 1)
            .all()
        )
        if duplicate_invoices:
            issues.append({
                "type": "error",
                "entity": "Invoice",
                "description": "Duplicate invoice numbers found",
                "count": len(duplicate_invoices),
                "details": [d[0] for d in duplicate_invoices],
            })

        # Check for soft-deleted partners still referenced in active orders
        deleted_partners_in_orders = (
            self.session.query(models.Partner)
            .filter(
                models.Partner.is_deleted == True,
                models.Partner.id.in_(
                    self.session.query(models.Order.partner_id)
                    .filter(models.Order.confirmed == True)
                ),
            )
            .count()
        )
        if deleted_partners_in_orders > 0:
            issues.append({
                "type": "warning",
                "entity": "Partner",
                "description": "Deleted partners referenced in confirmed orders",
                "count": deleted_partners_in_orders,
            })

        return issues

    def get_foreign_key_references(self, table_name: str) -> Dict[str, List[Tuple[str, str]]]:
        """Get foreign key relationships for a table.

        Args:
            table_name: The table to inspect

        Returns:
            Dict with 'incoming' and 'outgoing' FK lists
        """
        model = self.get_model_class(table_name)
        if not model:
            return {"incoming": [], "outgoing": []}

        inspector = inspect(db.engine)
        result = {"incoming": [], "outgoing": []}

        # Get outgoing FKs (this table references others)
        try:
            fks = inspector.get_foreign_keys(table_name)
            for fk in fks:
                result["outgoing"].append((
                    fk["constrained_columns"][0],
                    fk["referred_table"],
                ))
        except Exception:
            pass

        # Get incoming FKs (other tables reference this one)
        for other_table in DELETION_ORDER:
            if other_table == table_name:
                continue
            try:
                fks = inspector.get_foreign_keys(other_table)
                for fk in fks:
                    if fk["referred_table"] == table_name:
                        result["incoming"].append((
                            other_table,
                            fk["constrained_columns"][0],
                        ))
            except Exception:
                pass

        return result

    def get_reference_counts(self, table_name: str, record_id: int) -> Dict[str, int]:
        """Get counts of records referencing a specific record.

        Args:
            table_name: The table containing the record
            record_id: The ID of the record

        Returns:
            Dict mapping referencing table to count
        """
        refs = self.get_foreign_key_references(table_name)
        counts = {}

        for ref_table, ref_column in refs["incoming"]:
            ref_model = self.get_model_class(ref_table)
            if ref_model:
                col = getattr(ref_model, ref_column, None)
                if col is not None:
                    count = self.session.query(ref_model).filter(col == record_id).count()
                    if count > 0:
                        counts[ref_table] = count

        return counts

    def is_config_table(self, table_name: str) -> bool:
        """Check if a table is a configuration table."""
        return table_name in CONFIG_TABLES

    def get_deletion_preview(self, include_config: bool = False) -> List[Tuple[str, int]]:
        """Preview what would be deleted in a wipe operation.

        Args:
            include_config: Whether to include config tables

        Returns:
            List of (table_name, row_count) tuples in deletion order
        """
        preview = []
        for table_name in DELETION_ORDER:
            if not include_config and self.is_config_table(table_name):
                continue

            model = self.get_model_class(table_name)
            if model:
                count = self.session.query(model).count()
                if count > 0:
                    preview.append((table_name, count))

        return preview
