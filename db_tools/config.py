"""Configuration for database tools."""

from __future__ import annotations

import os
from pathlib import Path

# Backup settings
BACKUP_DIR = Path(os.environ.get("DB_TOOLS_BACKUP_DIR", "./backups"))
BACKUP_RETENTION_COUNT = int(os.environ.get("DB_TOOLS_BACKUP_RETENTION_COUNT", "10"))
BACKUP_RETENTION_DAYS = int(os.environ.get("DB_TOOLS_BACKUP_RETENTION_DAYS", "30"))

# Import settings
LARGE_IMPORT_THRESHOLD = int(os.environ.get("DB_TOOLS_LARGE_IMPORT_THRESHOLD", "500"))

# Tables that contain configuration (not business data)
CONFIG_TABLES = frozenset({
    "app_setting",
    "numbering_config",
    "number_sequence",
    "pdf_template",
})

# FK-safe deletion order (leaf tables first, foundation tables last)
# This order respects foreign key constraints
DELETION_ORDER = [
    # Level 1: Leaf tables (no incoming FKs, or only from cascade-delete parents)
    "audit_log",
    "product_price_history",
    "bundle_price_history",
    "vehicle_schedule",

    # Level 2: Child tables with FKs to multiple parents
    "delivery_item_component",
    "invoice_item",
    "delivery_item",
    "logistics_plan",
    "delivery_note_order",
    "contact",
    "product_restriction",

    # Level 3: Intermediate tables
    "order_item",
    "bundle_item",
    "delivery_note",
    "invoice",

    # Level 4: Core business tables
    "order",
    "bundle",
    "product",
    "vehicle",
    "partner_address",

    # Level 5: Foundation tables
    "partner",
    "user",

    # Level 6: Config tables (optional - only deleted if explicitly requested)
    "number_sequence",
    "numbering_config",
    "app_setting",
    "pdf_template",
]

# Import order (foundation tables first, dependent tables last)
# Reverse of deletion order for the data tables
IMPORT_ORDER = [
    "user",
    "partner",
    "partner_address",
    "contact",
    "product",
    "bundle",
    "bundle_item",
    "vehicle",
    "vehicle_schedule",
    "order",
    "order_item",
    "delivery_note",
    "delivery_note_order",
    "delivery_item",
    "delivery_item_component",
    "invoice",
    "invoice_item",
    "logistics_plan",
]

# Entity type to model class mapping
ENTITY_MODEL_MAP = {
    "user": "User",
    "partner": "Partner",
    "partner_address": "PartnerAddress",
    "contact": "Contact",
    "product": "Product",
    "bundle": "Bundle",
    "bundle_item": "BundleItem",
    "vehicle": "Vehicle",
    "vehicle_schedule": "VehicleSchedule",
    "order": "Order",
    "order_item": "OrderItem",
    "delivery_note": "DeliveryNote",
    "delivery_note_order": "DeliveryNoteOrder",
    "delivery_item": "DeliveryItem",
    "delivery_item_component": "DeliveryItemComponent",
    "invoice": "Invoice",
    "invoice_item": "InvoiceItem",
    "logistics_plan": "LogisticsPlan",
    "audit_log": "AuditLog",
    "app_setting": "AppSetting",
    "numbering_config": "NumberingConfig",
    "number_sequence": "NumberSequence",
    "pdf_template": "PdfTemplate",
    "product_price_history": "ProductPriceHistory",
    "bundle_price_history": "BundlePriceHistory",
    "product_restriction": "ProductRestriction",
}
