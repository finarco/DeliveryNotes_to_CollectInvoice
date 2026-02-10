"""Routes for database maintenance tools GUI."""

from __future__ import annotations

import json
import os
import tempfile
from functools import wraps
from pathlib import Path
from typing import Optional

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
    jsonify,
    send_file,
)
from werkzeug.utils import secure_filename

from extensions import db
from db_tools.config import BACKUP_DIR
from db_tools.core.backup import BackupManager
from db_tools.core.database_inspector import DatabaseInspector
from db_tools.operations.wipe import DatabaseWiper
from db_tools.operations.import_data import DataImporter, ValidationError
from db_tools.operations.maintenance import MaintenanceTool

db_tools_bp = Blueprint("db_tools", __name__, url_prefix="/admin/db-tools")

# Allowed file extensions for import
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}

# Session keys for import workflow
IMPORT_SESSION_KEY = "db_tools_import"


def admin_required(f):
    """Decorator to require admin role for access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = getattr(g, "current_user", None)
        if not user or user.role != "admin":
            flash("Prístup odmietnutý. Vyžaduje sa administrátorské oprávnenie.", "danger")
            return redirect(url_for("dashboard.index"))
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_database_uri() -> str:
    """Get the database URI from the app config."""
    return current_app.config["SQLALCHEMY_DATABASE_URI"]


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------


@db_tools_bp.route("/")
@admin_required
def index():
    """Database tools dashboard."""
    inspector = DatabaseInspector()
    stats = inspector.get_statistics()
    integrity_issues = inspector.check_integrity()

    return render_template(
        "admin/db_tools/index.html",
        stats=stats,
        integrity_issues=integrity_issues,
    )


# ---------------------------------------------------------------------------
# Data Wipe
# ---------------------------------------------------------------------------


@db_tools_bp.route("/wipe", methods=["GET", "POST"])
@admin_required
def wipe():
    """Database wipe interface."""
    wiper = DatabaseWiper(get_database_uri())
    inspector = DatabaseInspector()

    is_production = wiper.is_production_environment()
    preview = wiper.get_deletion_preview(include_config=False)
    preview_with_config = wiper.get_deletion_preview(include_config=True)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "preview":
            include_config = request.form.get("include_config") == "on"
            preview = wiper.get_deletion_preview(include_config=include_config)
            return render_template(
                "admin/db_tools/wipe.html",
                is_production=is_production,
                preview=preview,
                preview_with_config=preview_with_config,
                include_config=include_config,
                show_preview=True,
            )

        elif action == "wipe":
            confirmation = request.form.get("confirmation", "")
            include_config = request.form.get("include_config") == "on"

            if not wiper.validate_confirmation(confirmation):
                flash(
                    f"Nesprávne potvrdenie. Zadajte presne: {wiper.CONFIRMATION_PHRASE}",
                    "danger",
                )
                return redirect(url_for("db_tools.wipe"))

            result = wiper.wipe(
                include_config=include_config,
                create_backup=True,
                reset_sequences=True,
            )

            if result["success"]:
                total = sum(result["deleted_counts"].values())
                flash(
                    f"Databáza bola vymazaná. Zálohované do: {result['backup_path']}. "
                    f"Vymazaných záznamov: {total}",
                    "success",
                )
            else:
                flash(f"Chyba pri mazaní: {', '.join(result['errors'])}", "danger")

            return redirect(url_for("db_tools.index"))

    return render_template(
        "admin/db_tools/wipe.html",
        is_production=is_production,
        preview=preview,
        preview_with_config=preview_with_config,
        include_config=False,
        show_preview=False,
    )


# ---------------------------------------------------------------------------
# Data Import
# ---------------------------------------------------------------------------


@db_tools_bp.route("/import", methods=["GET"])
@admin_required
def import_index():
    """Import data - entity selection."""
    # Clear any previous import session
    session.pop(IMPORT_SESSION_KEY, None)

    entity_types = [
        ("partner", "Partneri", "Zákazníci, dodávatelia"),
        ("contact", "Kontakty", "Kontaktné osoby partnerov"),
        ("product", "Produkty", "Tovary a služby"),
        ("bundle", "Kombinácie", "Balíčky produktov"),
        ("bundle_item", "Položky kombinácií", "Produkty v balíčkoch"),
        ("vehicle", "Vozidlá", "Nákladné autá a dodávky"),
    ]

    return render_template(
        "admin/db_tools/import_select.html",
        entity_types=entity_types,
    )


@db_tools_bp.route("/import/<entity_type>", methods=["GET", "POST"])
@admin_required
def import_upload(entity_type: str):
    """Import data - file upload."""
    valid_types = ["partner", "contact", "product", "bundle", "bundle_item", "vehicle"]
    if entity_type not in valid_types:
        flash("Neplatný typ entity.", "danger")
        return redirect(url_for("db_tools.import_index"))

    if request.method == "POST":
        if "file" not in request.files:
            flash("Súbor nebol nahraný.", "danger")
            return redirect(request.url)

        file = request.files["file"]
        if file.filename == "":
            flash("Súbor nebol vybraný.", "danger")
            return redirect(request.url)

        if not allowed_file(file.filename):
            flash("Nepovolený typ súboru. Použite CSV alebo XLSX.", "danger")
            return redirect(request.url)

        # Save file temporarily
        filename = secure_filename(file.filename)
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, filename)
        file.save(file_path)

        # Validate file
        importer = DataImporter()
        try:
            headers, validated_rows, errors = importer.validate_file(
                Path(file_path), entity_type
            )
        except Exception as e:
            flash(f"Chyba pri čítaní súboru: {str(e)}", "danger")
            os.remove(file_path)
            os.rmdir(temp_dir)
            return redirect(request.url)

        # Store in session for review (only store filename, not full path)
        import uuid
        import_id = str(uuid.uuid4())
        # Store temp path mapping in app-level storage, not in session cookie
        if not hasattr(current_app, '_import_paths'):
            current_app._import_paths = {}
        current_app._import_paths[import_id] = {
            "file_path": file_path,
            "temp_dir": temp_dir,
        }
        session[IMPORT_SESSION_KEY] = {
            "entity_type": entity_type,
            "import_id": import_id,
            "headers": headers,
            "total_rows": len(validated_rows) + len(errors),
            "valid_rows": len(validated_rows),
            "errors": [
                {
                    "row_number": e.row_number,
                    "column": e.column,
                    "message": e.message,
                    "value": str(e.value) if e.value is not None else "",
                    "suggestions": e.suggestions,
                    "action": "pending",  # pending, ignore, fix
                }
                for e in errors
            ],
        }

        return redirect(url_for("db_tools.import_review"))

    # GET - show upload form
    importer = DataImporter()
    template = importer.generate_template(entity_type)

    type_labels = {
        "partner": "Partneri",
        "contact": "Kontakty",
        "product": "Produkty",
        "bundle": "Kombinácie",
        "bundle_item": "Položky kombinácií",
        "vehicle": "Vozidlá",
    }

    return render_template(
        "admin/db_tools/import_upload.html",
        entity_type=entity_type,
        entity_label=type_labels.get(entity_type, entity_type),
        template=template,
    )


@db_tools_bp.route("/import/review", methods=["GET", "POST"])
@admin_required
def import_review():
    """Import data - review errors and confirm."""
    import_data = session.get(IMPORT_SESSION_KEY)
    if not import_data:
        flash("Import session vypršala. Začnite znova.", "warning")
        return redirect(url_for("db_tools.import_index"))

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_errors":
            # Update error actions from form
            errors = import_data.get("errors", [])
            for i, error in enumerate(errors):
                error_action = request.form.get(f"error_action_{i}")
                if error_action in ("ignore", "fix"):
                    error["action"] = error_action
            session[IMPORT_SESSION_KEY] = import_data
            flash("Akcie aktualizované.", "info")
            return redirect(url_for("db_tools.import_review"))

        elif action == "commit":
            # Check all errors have been addressed
            errors = import_data.get("errors", [])
            unresolved = [e for e in errors if e["action"] == "pending"]
            if unresolved:
                flash(
                    f"Zostáva {len(unresolved)} nevyriešených chýb. "
                    "Označte ich ako 'Ignorovať' alebo 'Opraviť'.",
                    "danger",
                )
                return redirect(url_for("db_tools.import_review"))

            # Execute import
            conflict_mode = request.form.get("conflict_mode", "skip")
            importer = DataImporter()

            # Resolve file path from app-level storage
            import_id = import_data.get("import_id", "")
            paths = getattr(current_app, '_import_paths', {}).get(import_id, {})
            resolved_file_path = paths.get("file_path", "")
            if not resolved_file_path or not os.path.exists(resolved_file_path):
                flash("Import session vypršala. Začnite znova.", "warning")
                _cleanup_import_session()
                return redirect(url_for("db_tools.import_index"))

            try:
                result = importer.import_file(
                    Path(resolved_file_path),
                    import_data["entity_type"],
                    conflict_mode=conflict_mode,
                    partial_commit=True,
                )

                if result.success:
                    flash(
                        f"Import dokončený. Importovaných: {result.imported_count}, "
                        f"Aktualizovaných: {result.updated_count}, "
                        f"Preskočených: {result.skipped_count}",
                        "success",
                    )
                else:
                    new_errors = [f"R{e.row_number}: {e.message}" for e in result.errors[:5]]
                    flash(f"Import zlyhal: {'; '.join(new_errors)}", "danger")

            except Exception as e:
                flash(f"Chyba pri importe: {str(e)}", "danger")

            # Cleanup
            _cleanup_import_session()
            return redirect(url_for("db_tools.index"))

        elif action == "cancel":
            _cleanup_import_session()
            flash("Import zrušený.", "info")
            return redirect(url_for("db_tools.import_index"))

    # GET - show review page
    type_labels = {
        "partner": "Partneri",
        "contact": "Kontakty",
        "product": "Produkty",
        "bundle": "Kombinácie",
        "bundle_item": "Položky kombinácií",
        "vehicle": "Vozidlá",
    }

    return render_template(
        "admin/db_tools/import_review.html",
        import_data=import_data,
        entity_label=type_labels.get(import_data["entity_type"], import_data["entity_type"]),
    )


@db_tools_bp.route("/import/template/<entity_type>")
@admin_required
def download_template(entity_type: str):
    """Download CSV template for entity type."""
    import io
    importer = DataImporter()
    template = importer.generate_template(entity_type)

    buf = io.BytesIO(template.encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"{entity_type}_template.csv",
        mimetype="text/csv",
    )


def _cleanup_import_session():
    """Clean up import session and temp files."""
    import_data = session.pop(IMPORT_SESSION_KEY, None)
    if import_data:
        import_id = import_data.get("import_id", "")
        paths = getattr(current_app, '_import_paths', {}).pop(import_id, {})
        file_path = paths.get("file_path")
        temp_dir = paths.get("temp_dir")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        if temp_dir and os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Maintenance Tools
# ---------------------------------------------------------------------------


@db_tools_bp.route("/maintenance")
@admin_required
def maintenance():
    """Maintenance tools dashboard."""
    tool = MaintenanceTool()
    stats = tool.get_statistics()
    table_counts = tool.get_table_counts()
    integrity_issues = tool.check_integrity()

    return render_template(
        "admin/db_tools/maintenance.html",
        stats=stats,
        table_counts=table_counts,
        integrity_issues=integrity_issues,
    )


@db_tools_bp.route("/maintenance/reset-sequences", methods=["POST"])
@admin_required
def reset_sequences():
    """Reset number sequences."""
    tool = MaintenanceTool()
    result = tool.reset_number_sequences()

    flash(
        f"Sekvencie resetované: {', '.join(f'{k}={v}' for k, v in result.items())}",
        "success",
    )
    return redirect(url_for("db_tools.maintenance"))


@db_tools_bp.route("/maintenance/repair-orphans", methods=["POST"])
@admin_required
def repair_orphans():
    """Repair orphaned records."""
    tool = MaintenanceTool()
    result = tool.repair_orphaned_records()

    if result:
        flash(
            f"Opravené osirelé záznamy: {', '.join(f'{k}={v}' for k, v in result.items())}",
            "success",
        )
    else:
        flash("Žiadne osirelé záznamy neboli nájdené.", "info")

    return redirect(url_for("db_tools.maintenance"))


@db_tools_bp.route("/maintenance/unlock/<entity_type>/<int:entity_id>", methods=["POST"])
@admin_required
def unlock_document(entity_type: str, entity_id: int):
    """Unlock a locked document."""
    tool = MaintenanceTool()
    result = tool.unlock_document(entity_type, entity_id)

    if result["success"]:
        flash(f"Dokument {entity_type} #{entity_id} bol odomknutý.", "success")
    else:
        flash(f"Chyba: {result['error']}", "danger")

    return redirect(url_for("db_tools.maintenance"))


@db_tools_bp.route("/maintenance/export/<entity_type>")
@admin_required
def export_entity(entity_type: str):
    """Export entity to CSV."""
    import shutil
    tool = MaintenanceTool()

    temp_dir = tempfile.mkdtemp()
    try:
        file_path = os.path.join(temp_dir, f"{entity_type}_export.csv")
        result = tool.export_entity_to_csv(entity_type, file_path)

        if result["success"]:
            response = send_file(
                file_path,
                as_attachment=True,
                download_name=f"{entity_type}_export.csv",
                mimetype="text/csv",
            )
            return response
        else:
            flash(f"Chyba pri exporte: {result['error']}", "danger")
            return redirect(url_for("db_tools.maintenance"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@db_tools_bp.route("/maintenance/query", methods=["GET", "POST"])
@admin_required
def sql_query():
    """Execute read-only SQL query."""
    result = None
    query = ""

    if request.method == "POST":
        query = request.form.get("query", "")
        tool = MaintenanceTool()
        result = tool.execute_read_only_query(query)

    return render_template(
        "admin/db_tools/sql_query.html",
        query=query,
        result=result,
    )


# ---------------------------------------------------------------------------
# Backup Management
# ---------------------------------------------------------------------------


@db_tools_bp.route("/backups")
@admin_required
def list_backups():
    """List available backups."""
    manager = BackupManager(get_database_uri())
    backups = manager.list_backups()

    return render_template(
        "admin/db_tools/backups.html",
        backups=backups,
    )


@db_tools_bp.route("/backups/create", methods=["POST"])
@admin_required
def create_backup():
    """Create a new backup."""
    manager = BackupManager(get_database_uri())

    try:
        backup_path = manager.create_backup(prefix="manual")
        flash(f"Záloha vytvorená: {backup_path.name}", "success")
    except Exception as e:
        flash(f"Chyba pri vytváraní zálohy: {str(e)}", "danger")

    return redirect(url_for("db_tools.list_backups"))


@db_tools_bp.route("/backups/cleanup", methods=["POST"])
@admin_required
def cleanup_backups():
    """Remove old backups based on retention policy."""
    manager = BackupManager(get_database_uri())

    try:
        removed = manager.cleanup_old_backups()
        if removed:
            flash(f"Odstránených {len(removed)} starých záloh.", "success")
        else:
            flash("Žiadne zálohy na odstránenie.", "info")
    except Exception as e:
        flash(f"Chyba pri čistení záloh: {str(e)}", "danger")

    return redirect(url_for("db_tools.list_backups"))
