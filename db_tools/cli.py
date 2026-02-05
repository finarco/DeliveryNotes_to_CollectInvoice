"""CLI commands for database tools.

Provides both Flask CLI integration and standalone CLI functionality.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import click


def get_app_context():
    """Get Flask application context."""
    from app import create_app
    app = create_app()
    return app.app_context()


def get_database_uri():
    """Get database URI from Flask config."""
    from flask import current_app
    return current_app.config["SQLALCHEMY_DATABASE_URI"]


# ---------------------------------------------------------------------------
# Click CLI Group
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """Database maintenance tools for DeliveryNotes_to_CollectInvoice."""
    pass


# ---------------------------------------------------------------------------
# Backup Commands
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--output", "-o", type=click.Path(), help="Output file path")
def backup(output: Optional[str]):
    """Create a database backup."""
    with get_app_context():
        from db_tools.core.backup import BackupManager

        manager = BackupManager(get_database_uri())

        try:
            if output:
                # Custom output path
                from pathlib import Path
                backup_path = Path(output)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                temp_backup = manager.create_backup(prefix="backup")
                shutil.move(str(temp_backup), str(backup_path))
                click.echo(f"Backup created: {backup_path}")
            else:
                backup_path = manager.create_backup(prefix="backup")
                click.echo(f"Backup created: {backup_path}")

        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


@cli.command()
@click.argument("backup_file", type=click.Path(exists=True))
def restore(backup_file: str):
    """Restore database from backup."""
    with get_app_context():
        from db_tools.core.backup import BackupManager

        manager = BackupManager(get_database_uri())

        if not click.confirm(f"Restore from {backup_file}? This will overwrite current data."):
            click.echo("Aborted.")
            return

        try:
            manager.restore_backup(Path(backup_file))
            click.echo("Database restored successfully.")
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


@cli.command("list-backups")
def list_backups():
    """List available backups."""
    with get_app_context():
        from db_tools.core.backup import BackupManager

        manager = BackupManager(get_database_uri())
        backups = manager.list_backups()

        if not backups:
            click.echo("No backups found.")
            return

        click.echo("Available backups:")
        for path, mtime in backups:
            size_kb = path.stat().st_size / 1024
            click.echo(f"  {path.name}  ({mtime:%Y-%m-%d %H:%M})  {size_kb:.1f} KB")


# ---------------------------------------------------------------------------
# Wipe Command
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--dry-run", is_flag=True, help="Preview what would be deleted")
@click.option("--include-config", is_flag=True, help="Include config tables")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
@click.option("--no-backup", is_flag=True, help="Skip backup creation")
def wipe(dry_run: bool, include_config: bool, confirm: bool, no_backup: bool):
    """Wipe all data from database."""
    with get_app_context():
        from db_tools.operations.wipe import DatabaseWiper

        wiper = DatabaseWiper(get_database_uri())

        # Show preview
        preview = wiper.get_deletion_preview(include_config=include_config)

        if not preview:
            click.echo("Database is empty.")
            return

        click.echo("\nTables to be deleted:")
        total = 0
        for table_name, count in preview:
            click.echo(f"  {table_name}: {count}")
            total += count
        click.echo(f"\nTotal records: {total}")

        if dry_run:
            click.echo("\n(Dry run - no changes made)")
            return

        # Production warning
        if wiper.is_production_environment():
            click.echo(click.style(
                "\nWARNING: Production database detected!",
                fg="red", bold=True
            ))

        # Confirmation
        if not confirm:
            phrase = click.prompt(
                f"\nType '{wiper.CONFIRMATION_PHRASE}' to confirm"
            )
            if not wiper.validate_confirmation(phrase):
                click.echo("Invalid confirmation. Aborted.")
                return

        # Execute wipe
        def progress(table, current, total):
            click.echo(f"  Deleting {table}...")

        wiper.set_progress_callback(progress)

        result = wiper.wipe(
            include_config=include_config,
            create_backup=not no_backup,
            reset_sequences=True,
        )

        if result["success"]:
            total_deleted = sum(result["deleted_counts"].values())
            click.echo(f"\nDatabase wiped. {total_deleted} records deleted.")
            if result["backup_path"]:
                click.echo(f"Backup saved to: {result['backup_path']}")
        else:
            click.echo(f"\nWipe failed: {', '.join(result['errors'])}", err=True)
            sys.exit(1)


# ---------------------------------------------------------------------------
# Import Commands
# ---------------------------------------------------------------------------

@cli.command("import")
@click.argument("file", type=click.Path(exists=True))
@click.option("--entity-type", "-t", required=True,
              type=click.Choice(["partner", "contact", "product", "bundle", "bundle_item", "vehicle"]),
              help="Type of entity to import")
@click.option("--preview", is_flag=True, help="Preview without importing")
@click.option("--conflict-mode", "-c", default="skip",
              type=click.Choice(["skip", "update", "error"]),
              help="How to handle conflicts")
def import_data(file: str, entity_type: str, preview: bool, conflict_mode: str):
    """Import data from CSV/XLSX file."""
    with get_app_context():
        from db_tools.operations.import_data import DataImporter

        importer = DataImporter()
        file_path = Path(file)

        # Validate
        click.echo(f"Validating {file_path.name}...")
        headers, validated_rows, errors = importer.validate_file(file_path, entity_type)

        click.echo(f"  Total rows: {len(validated_rows) + len(errors)}")
        click.echo(f"  Valid: {len(validated_rows)}")
        click.echo(f"  Errors: {len(errors)}")

        if errors:
            click.echo("\nValidation errors:")
            for error in errors[:10]:
                click.echo(f"  Row {error.row_number}: {error.column} - {error.message}")
                if error.suggestions:
                    click.echo(f"    Suggestions: {', '.join(error.suggestions[:3])}")
            if len(errors) > 10:
                click.echo(f"  ... and {len(errors) - 10} more errors")

        if preview:
            click.echo("\n(Preview mode - no changes made)")
            return

        if errors:
            if not click.confirm("\nProceed with partial import (skip errors)?"):
                click.echo("Aborted.")
                return

        # Import
        def progress(current, total):
            if current % 50 == 0 or current == total:
                click.echo(f"  Imported {current}/{total}")

        importer.set_progress_callback(progress)

        result = importer.import_file(
            file_path,
            entity_type,
            conflict_mode=conflict_mode,
            partial_commit=True,
        )

        if result.success:
            click.echo(f"\nImport complete:")
            click.echo(f"  Imported: {result.imported_count}")
            click.echo(f"  Updated: {result.updated_count}")
            click.echo(f"  Skipped: {result.skipped_count}")
        else:
            click.echo(f"\nImport failed with {len(result.errors)} errors", err=True)
            sys.exit(1)


@cli.command()
@click.argument("entity_type",
                type=click.Choice(["partner", "contact", "product", "bundle", "bundle_item", "vehicle"]))
def template(entity_type: str):
    """Generate CSV import template."""
    with get_app_context():
        from db_tools.operations.import_data import DataImporter

        importer = DataImporter()
        template_content = importer.generate_template(entity_type)
        click.echo(template_content)


# ---------------------------------------------------------------------------
# Maintenance Commands
# ---------------------------------------------------------------------------

@cli.command("check-integrity")
def check_integrity():
    """Check database integrity."""
    with get_app_context():
        from db_tools.operations.maintenance import MaintenanceTool

        tool = MaintenanceTool()
        issues = tool.check_integrity()

        if not issues:
            click.echo("No integrity issues found.")
            return

        click.echo("Integrity issues found:")
        for issue in issues:
            severity = click.style(issue["type"].upper(), fg="red" if issue["type"] == "error" else "yellow")
            click.echo(f"  [{severity}] {issue['entity']}: {issue['description']} ({issue['count']})")


@cli.command("reset-sequences")
def reset_sequences():
    """Reset number sequences to current max values."""
    with get_app_context():
        from db_tools.operations.maintenance import MaintenanceTool

        tool = MaintenanceTool()

        if not click.confirm("Reset all number sequences?"):
            click.echo("Aborted.")
            return

        result = tool.reset_number_sequences()
        click.echo("Sequences reset:")
        for entity_type, value in result.items():
            click.echo(f"  {entity_type}: {value}")


@cli.command("repair-orphans")
def repair_orphans():
    """Delete orphaned records."""
    with get_app_context():
        from db_tools.operations.maintenance import MaintenanceTool

        tool = MaintenanceTool()

        if not click.confirm("Delete orphaned records?"):
            click.echo("Aborted.")
            return

        result = tool.repair_orphaned_records()
        if result:
            click.echo("Orphaned records deleted:")
            for entity, count in result.items():
                click.echo(f"  {entity}: {count}")
        else:
            click.echo("No orphaned records found.")


@cli.command()
@click.argument("entity_type", type=click.Choice(["order", "delivery_note", "invoice"]))
@click.argument("entity_id", type=int)
def unlock(entity_type: str, entity_id: int):
    """Unlock a locked document."""
    with get_app_context():
        from db_tools.operations.maintenance import MaintenanceTool

        tool = MaintenanceTool()
        result = tool.unlock_document(entity_type, entity_id)

        if result["success"]:
            click.echo(f"Unlocked {entity_type} #{entity_id}")
        else:
            click.echo(f"Error: {result['error']}", err=True)
            sys.exit(1)


@cli.command()
@click.argument("entity_type")
@click.option("--output", "-o", type=click.Path(), help="Output file path")
def export(entity_type: str, output: Optional[str]):
    """Export entity to CSV."""
    with get_app_context():
        from db_tools.operations.maintenance import MaintenanceTool

        tool = MaintenanceTool()
        output_path = output or f"{entity_type}_export.csv"

        result = tool.export_entity_to_csv(entity_type, output_path)

        if result["success"]:
            click.echo(f"Exported {result['count']} records to {output_path}")
        else:
            click.echo(f"Error: {result['error']}", err=True)
            sys.exit(1)


@cli.command()
@click.argument("sql")
def query(sql: str):
    """Execute a read-only SQL query."""
    with get_app_context():
        from db_tools.operations.maintenance import MaintenanceTool

        tool = MaintenanceTool()
        result = tool.execute_read_only_query(sql)

        if not result["success"]:
            click.echo(f"Error: {result['error']}", err=True)
            sys.exit(1)

        if not result["rows"]:
            click.echo("No results.")
            return

        # Print as table
        from tabulate import tabulate
        click.echo(tabulate(result["rows"], headers=result["columns"], tablefmt="simple"))
        click.echo(f"\n({result['row_count']} rows)")


@cli.command()
def stats():
    """Show database statistics."""
    with get_app_context():
        from db_tools.operations.maintenance import MaintenanceTool

        tool = MaintenanceTool()
        stats = tool.get_statistics()

        click.echo("Database Statistics:")
        click.echo("-" * 40)
        for category, values in stats.items():
            if isinstance(values, dict):
                click.echo(f"\n{category.replace('_', ' ').title()}:")
                for key, value in values.items():
                    click.echo(f"  {key}: {value}")
            else:
                click.echo(f"{category}: {values}")


# ---------------------------------------------------------------------------
# Flask CLI Registration
# ---------------------------------------------------------------------------

def register_flask_commands(app):
    """Register CLI commands with Flask application."""

    @app.cli.group("db-tools")
    def db_tools_cli():
        """Database maintenance tools."""
        pass

    @db_tools_cli.command("backup")
    @click.option("--output", "-o", type=click.Path(), help="Output file path")
    def flask_backup(output):
        """Create a database backup."""
        ctx = click.get_current_context()
        ctx.invoke(backup, output=output)

    @db_tools_cli.command("wipe")
    @click.option("--dry-run", is_flag=True)
    @click.option("--include-config", is_flag=True)
    @click.option("--confirm", is_flag=True)
    def flask_wipe(dry_run, include_config, confirm):
        """Wipe all data from database."""
        ctx = click.get_current_context()
        ctx.invoke(wipe, dry_run=dry_run, include_config=include_config,
                  confirm=confirm, no_backup=False)

    @db_tools_cli.command("import")
    @click.argument("file", type=click.Path(exists=True))
    @click.option("--type", "-t", "entity_type", required=True)
    @click.option("--preview", is_flag=True)
    def flask_import(file, entity_type, preview):
        """Import data from CSV/XLSX."""
        ctx = click.get_current_context()
        ctx.invoke(import_data, file=file, entity_type=entity_type,
                  preview=preview, conflict_mode="skip")

    @db_tools_cli.command("check-integrity")
    def flask_check():
        """Check database integrity."""
        ctx = click.get_current_context()
        ctx.invoke(check_integrity)

    @db_tools_cli.command("stats")
    def flask_stats():
        """Show database statistics."""
        ctx = click.get_current_context()
        ctx.invoke(stats)


if __name__ == "__main__":
    cli()
