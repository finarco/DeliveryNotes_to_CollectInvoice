"""Data import operations for CSV/XLS files."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from extensions import db
from db_tools.config import LARGE_IMPORT_THRESHOLD, ENTITY_MODEL_MAP
from db_tools.core.normalization import (
    normalize_for_matching,
    find_best_match,
    suggest_similar,
)
from db_tools.core.database_inspector import DatabaseInspector


@dataclass
class ValidationError:
    """Represents a validation error for an import row."""

    row_number: int
    column: str
    message: str
    value: Any = None
    suggestions: List[str] = field(default_factory=list)


@dataclass
class ImportResult:
    """Result of an import operation."""

    success: bool
    total_rows: int
    imported_count: int
    skipped_count: int
    updated_count: int
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# Validation rules for each entity type
VALIDATION_RULES = {
    "partner": {
        "name": {"required": True, "max_length": 120},
        "ico": {"pattern": r"^\d{8}$", "required": False},
        "dic": {"pattern": r"^\d{10}$", "required": False},
        "ic_dph": {"pattern": r"^SK\d{10}$", "required": False},
        "email": {"pattern": r"^[^@]+@[^@]+\.[^@]+$", "required": False},
        "discount_percent": {"type": "decimal", "min": 0, "max": 100},
        "price_level": {"max_length": 60},
        "group_code": {"max_length": 60},
    },
    "contact": {
        "name": {"required": True, "max_length": 120},
        "partner_id": {"required": True, "type": "fk", "fk_entity": "partner"},
        "email": {"pattern": r"^[^@]+@[^@]+\.[^@]+$", "required": False},
    },
    "product": {
        "name": {"required": True, "max_length": 120},
        "price": {"required": True, "type": "decimal", "min": 0},
        "vat_rate": {"type": "decimal", "min": 0, "max": 100, "default": 20.0},
        "product_number": {"max_length": 60},
    },
    "bundle": {
        "name": {"required": True, "max_length": 120},
        "bundle_price": {"required": True, "type": "decimal", "min": 0},
        "bundle_number": {"max_length": 60},
    },
    "bundle_item": {
        "bundle_id": {"required": True, "type": "fk", "fk_entity": "bundle"},
        "product_id": {"required": True, "type": "fk", "fk_entity": "product"},
        "quantity": {"required": True, "type": "integer", "min": 1},
    },
    "vehicle": {
        "name": {"required": True, "max_length": 120},
        "registration_number": {"max_length": 20},
    },
}

# FK resolution: column name -> (entity_type, lookup_column)
FK_LOOKUPS = {
    "partner_id": ("partner", "id"),
    "partner_name": ("partner", "name"),
    "product_id": ("product", "id"),
    "product_name": ("product", "name"),
    "bundle_id": ("bundle", "id"),
    "bundle_name": ("bundle", "name"),
    "vehicle_id": ("vehicle", "id"),
    "vehicle_name": ("vehicle", "name"),
}


class DataImporter:
    """Imports data from CSV/XLS files with validation and FK resolution."""

    def __init__(self, inspector: Optional[DatabaseInspector] = None):
        """Initialize importer.

        Args:
            inspector: DatabaseInspector instance
        """
        self.inspector = inspector or DatabaseInspector()
        self._progress_callback: Optional[Callable[[int, int], None]] = None
        self._fk_cache: Dict[str, List[Tuple[int, str]]] = {}

    def set_progress_callback(self, callback: Callable[[int, int], None]) -> None:
        """Set a callback for progress updates.

        Args:
            callback: Function(current_row, total_rows) called during import
        """
        self._progress_callback = callback

    def _report_progress(self, current: int, total: int) -> None:
        """Report progress to callback if set."""
        if self._progress_callback:
            self._progress_callback(current, total)

    def _load_fk_candidates(self, entity_type: str) -> List[Tuple[int, str]]:
        """Load all candidates for FK resolution.

        Args:
            entity_type: The entity type to load

        Returns:
            List of (id, name) tuples
        """
        if entity_type in self._fk_cache:
            return self._fk_cache[entity_type]

        model = self.inspector.get_model_class(entity_type)
        if not model:
            return []

        # Determine the name column
        name_col = "name"
        if entity_type == "partner":
            name_col = "name"
        elif entity_type == "product":
            name_col = "name"
        elif entity_type == "bundle":
            name_col = "name"
        elif entity_type == "vehicle":
            name_col = "name"

        try:
            results = db.session.query(
                model.id, getattr(model, name_col)
            ).all()
            self._fk_cache[entity_type] = results
            return results
        except Exception:
            return []

    def _resolve_fk(
        self, column: str, value: Any, row_number: int
    ) -> Tuple[Optional[int], Optional[ValidationError]]:
        """Resolve a foreign key value to an ID.

        Args:
            column: The column name (e.g., 'partner_id' or 'partner_name')
            value: The value to resolve
            row_number: Current row number for error reporting

        Returns:
            Tuple of (resolved_id, error_if_any)
        """
        if value is None or value == "":
            return None, None

        # Check if this is a direct ID
        if column.endswith("_id"):
            try:
                return int(value), None
            except (ValueError, TypeError):
                # Not a valid ID, might be a name - try to resolve
                base_name = column[:-3]  # Remove '_id'
                entity_type = base_name
        else:
            # It's a name column like 'partner_name'
            base_name = column[:-5]  # Remove '_name'
            entity_type = base_name

        # Get candidates
        candidates = self._load_fk_candidates(entity_type)
        if not candidates:
            return None, ValidationError(
                row_number=row_number,
                column=column,
                message=f"No {entity_type} records found in database",
                value=value,
            )

        # Try to find a match
        try:
            match = find_best_match(str(value), candidates)
            if match:
                return match[0], None
            else:
                suggestions = suggest_similar(str(value), candidates)
                return None, ValidationError(
                    row_number=row_number,
                    column=column,
                    message=f"{entity_type.title()} '{value}' not found",
                    value=value,
                    suggestions=suggestions,
                )
        except ValueError as e:
            # Ambiguous match
            return None, ValidationError(
                row_number=row_number,
                column=column,
                message=str(e),
                value=value,
            )

    def _validate_value(
        self,
        value: Any,
        column: str,
        rules: Dict[str, Any],
        row_number: int,
    ) -> Tuple[Any, Optional[ValidationError]]:
        """Validate and convert a single value.

        Args:
            value: The raw value
            column: Column name
            rules: Validation rules for this column
            row_number: Row number for error reporting

        Returns:
            Tuple of (converted_value, error_if_any)
        """
        # Handle required check
        is_empty = value is None or (isinstance(value, str) and value.strip() == "")
        if rules.get("required", False) and is_empty:
            return None, ValidationError(
                row_number=row_number,
                column=column,
                message=f"Required field '{column}' is empty",
                value=value,
            )

        if is_empty:
            return rules.get("default"), None

        # Convert string value
        str_value = str(value).strip()

        # Type conversion
        value_type = rules.get("type", "string")

        if value_type == "decimal":
            try:
                converted = float(str_value.replace(",", "."))
                min_val = rules.get("min")
                max_val = rules.get("max")
                if min_val is not None and converted < min_val:
                    return None, ValidationError(
                        row_number=row_number,
                        column=column,
                        message=f"Value {converted} is below minimum {min_val}",
                        value=value,
                    )
                if max_val is not None and converted > max_val:
                    return None, ValidationError(
                        row_number=row_number,
                        column=column,
                        message=f"Value {converted} exceeds maximum {max_val}",
                        value=value,
                    )
                return converted, None
            except (ValueError, InvalidOperation):
                return None, ValidationError(
                    row_number=row_number,
                    column=column,
                    message=f"Invalid decimal value: {str_value}",
                    value=value,
                )

        elif value_type == "integer":
            try:
                converted = int(float(str_value))
                min_val = rules.get("min")
                max_val = rules.get("max")
                if min_val is not None and converted < min_val:
                    return None, ValidationError(
                        row_number=row_number,
                        column=column,
                        message=f"Value {converted} is below minimum {min_val}",
                        value=value,
                    )
                if max_val is not None and converted > max_val:
                    return None, ValidationError(
                        row_number=row_number,
                        column=column,
                        message=f"Value {converted} exceeds maximum {max_val}",
                        value=value,
                    )
                return converted, None
            except ValueError:
                return None, ValidationError(
                    row_number=row_number,
                    column=column,
                    message=f"Invalid integer value: {str_value}",
                    value=value,
                )

        elif value_type == "boolean":
            lower = str_value.lower()
            if lower in ("true", "1", "yes", "ano", "áno"):
                return True, None
            elif lower in ("false", "0", "no", "nie"):
                return False, None
            else:
                return None, ValidationError(
                    row_number=row_number,
                    column=column,
                    message=f"Invalid boolean value: {str_value}",
                    value=value,
                )

        elif value_type == "fk":
            fk_entity = rules.get("fk_entity")
            if fk_entity:
                return self._resolve_fk(column, str_value, row_number)

        # String type - check constraints
        if rules.get("max_length") and len(str_value) > rules["max_length"]:
            return None, ValidationError(
                row_number=row_number,
                column=column,
                message=f"Value exceeds maximum length of {rules['max_length']}",
                value=value,
            )

        if rules.get("pattern"):
            if not re.match(rules["pattern"], str_value):
                return None, ValidationError(
                    row_number=row_number,
                    column=column,
                    message=f"Value does not match required format",
                    value=value,
                )

        return str_value, None

    def _detect_file_type(self, file_path: Path) -> str:
        """Detect file type from extension.

        Args:
            file_path: Path to the file

        Returns:
            'csv' or 'xlsx'

        Raises:
            ValueError: If file type is not supported
        """
        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            return "csv"
        elif suffix in (".xlsx", ".xls"):
            return "xlsx"
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

    def _read_csv(self, file_path: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Read CSV file.

        Args:
            file_path: Path to CSV file

        Returns:
            Tuple of (headers, rows_as_dicts)
        """
        rows = []
        headers = []

        with open(file_path, "r", encoding="utf-8-sig") as f:
            # Try to detect delimiter
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(f, dialect=dialect)
            headers = reader.fieldnames or []
            for row in reader:
                rows.append(row)

        return headers, rows

    def _read_xlsx(self, file_path: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Read XLSX file.

        Args:
            file_path: Path to XLSX file

        Returns:
            Tuple of (headers, rows_as_dicts)
        """
        try:
            import openpyxl
        except ImportError:
            raise ImportError(
                "openpyxl is required for Excel import. "
                "Install it with: pip install openpyxl"
            )

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active

        rows = []
        headers = []

        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                headers = [str(cell) if cell else f"col_{j}" for j, cell in enumerate(row)]
            else:
                row_dict = {}
                for j, cell in enumerate(row):
                    if j < len(headers):
                        row_dict[headers[j]] = cell
                if any(v is not None for v in row_dict.values()):
                    rows.append(row_dict)

        wb.close()
        return headers, rows

    def validate_file(
        self,
        file_path: Path,
        entity_type: str,
    ) -> Tuple[List[str], List[Dict[str, Any]], List[ValidationError]]:
        """Validate an import file without importing.

        Args:
            file_path: Path to the import file
            entity_type: Type of entity being imported

        Returns:
            Tuple of (headers, validated_rows, errors)
        """
        file_type = self._detect_file_type(file_path)

        if file_type == "csv":
            headers, rows = self._read_csv(file_path)
        else:
            headers, rows = self._read_xlsx(file_path)

        rules = VALIDATION_RULES.get(entity_type, {})
        validated_rows = []
        errors = []

        for i, row in enumerate(rows):
            row_number = i + 2  # Account for header row and 1-based indexing
            validated_row = {}
            row_has_error = False

            for column, value in row.items():
                # Skip empty columns
                if not column or column.startswith("col_"):
                    continue

                # Check if this is a name-based FK that needs resolution
                if column in FK_LOOKUPS and column.endswith("_name"):
                    id_column = column[:-5] + "_id"
                    resolved_id, error = self._resolve_fk(column, value, row_number)
                    if error:
                        errors.append(error)
                        row_has_error = True
                    else:
                        validated_row[id_column] = resolved_id
                    continue

                # Get rules for this column
                col_rules = rules.get(column, {})
                converted, error = self._validate_value(
                    value, column, col_rules, row_number
                )

                if error:
                    errors.append(error)
                    row_has_error = True
                else:
                    validated_row[column] = converted

            if not row_has_error:
                validated_rows.append(validated_row)

        return headers, validated_rows, errors

    def import_file(
        self,
        file_path: Path,
        entity_type: str,
        *,
        conflict_mode: str = "skip",  # skip, update, error
        dry_run: bool = False,
        partial_commit: bool = False,
    ) -> ImportResult:
        """Import data from a file.

        Args:
            file_path: Path to the import file
            entity_type: Type of entity being imported
            conflict_mode: How to handle existing records
                - 'skip': Skip rows that match existing records
                - 'update': Update existing records
                - 'error': Fail on conflicts
            dry_run: If True, validate only without importing
            partial_commit: If True, commit valid rows even if some fail

        Returns:
            ImportResult with details of the operation
        """
        result = ImportResult(
            success=False,
            total_rows=0,
            imported_count=0,
            skipped_count=0,
            updated_count=0,
        )

        # Clear FK cache
        self._fk_cache.clear()

        # Validate file
        headers, validated_rows, errors = self.validate_file(file_path, entity_type)
        result.total_rows = len(validated_rows) + len(errors)
        result.errors = errors

        if dry_run:
            result.success = len(errors) == 0
            result.imported_count = len(validated_rows)
            return result

        # Check if we should abort on errors
        if errors and not partial_commit:
            return result

        # Get model class
        model = self.inspector.get_model_class(entity_type)
        if not model:
            result.errors.append(ValidationError(
                row_number=0,
                column="",
                message=f"Unknown entity type: {entity_type}",
            ))
            return result

        # Determine unique key for conflict detection
        unique_key = self._get_unique_key(entity_type)

        # Import rows
        try:
            for i, row_data in enumerate(validated_rows):
                self._report_progress(i + 1, len(validated_rows))

                # Check for existing record
                existing = None
                if unique_key and unique_key in row_data:
                    existing = db.session.query(model).filter(
                        getattr(model, unique_key) == row_data[unique_key]
                    ).first()

                if existing:
                    if conflict_mode == "skip":
                        result.skipped_count += 1
                        continue
                    elif conflict_mode == "error":
                        result.errors.append(ValidationError(
                            row_number=i + 2,
                            column=unique_key,
                            message=f"Record already exists with {unique_key}={row_data[unique_key]}",
                            value=row_data[unique_key],
                        ))
                        if not partial_commit:
                            db.session.rollback()
                            return result
                        continue
                    elif conflict_mode == "update":
                        for key, value in row_data.items():
                            if value is not None and hasattr(existing, key):
                                setattr(existing, key, value)
                        result.updated_count += 1
                        continue

                # Create new record
                new_record = model(**row_data)
                db.session.add(new_record)
                result.imported_count += 1

            db.session.commit()
            result.success = True

        except Exception as e:
            db.session.rollback()
            result.errors.append(ValidationError(
                row_number=0,
                column="",
                message=f"Import failed: {str(e)}",
            ))

        return result

    def _get_unique_key(self, entity_type: str) -> Optional[str]:
        """Get the unique key column for an entity type.

        Args:
            entity_type: The entity type

        Returns:
            Column name to use for uniqueness check, or None
        """
        unique_keys = {
            "partner": "ico",
            "product": "product_number",
            "bundle": "bundle_number",
            "vehicle": "registration_number",
            "user": "username",
        }
        return unique_keys.get(entity_type)

    def generate_template(self, entity_type: str) -> str:
        """Generate a CSV template for an entity type.

        Args:
            entity_type: The entity type

        Returns:
            CSV template string with headers and example row
        """
        templates = {
            "partner": (
                "name,street,street_number,postal_code,city,ico,dic,ic_dph,"
                "email,phone,price_level,discount_percent,group_code,note\n"
                '"ABC Company s.r.o.",Hlavná,15,81101,Bratislava,12345678,'
                '2012345678,SK2012345678,info@abc.sk,+421903111222,A,5.0,STAVBY,'
                '"Important customer"'
            ),
            "contact": (
                "partner_name,name,email,phone,role,can_order,can_receive\n"
                '"ABC Company s.r.o.","Ján Novák",jan@abc.sk,+421903111222,'
                "Manager,true,true"
            ),
            "product": (
                "product_number,name,description,price,vat_rate,is_service,"
                "discount_excluded\n"
                'PROD-001,"Cement 25kg","Portland cement bag",6.50,20.0,false,false'
            ),
            "bundle": (
                "bundle_number,name,bundle_price,discount_excluded\n"
                'BUN-001,"Starter Pack",99.00,false'
            ),
            "bundle_item": (
                "bundle_name,product_name,quantity\n"
                '"Starter Pack","Cement 25kg",10'
            ),
            "vehicle": (
                "name,registration_number,notes,active\n"
                '"MAN TGS 26.400",BA-123AB,"Heavy truck",true'
            ),
        }

        return templates.get(entity_type, "# No template available for this entity type")
