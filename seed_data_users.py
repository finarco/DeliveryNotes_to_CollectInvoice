#!/usr/bin/env python3
"""Seed script - populates the database with realistic mock data for testing.

Usage:
    # Fresh seed (drops and recreates all tables first):
    python seed_data.py

    # Append to existing data (no table drop):
    python seed_data.py --append

    # Custom database URI:
    DATABASE_URI=postgresql://user:pass@host/db python seed_data.py
"""
from __future__ import annotations

import argparse
import datetime
import sys

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import (
    User,
)


def seed(append: bool = False):
    app = create_app()
    with app.app_context():
        if not append:
            print("Dropping all tables...")
            db.drop_all()
            print("Creating all tables...")
            db.create_all()
        else:
            print("Appending to existing data...")
            db.create_all()

        # ── 1. Users (4 roles) ──────────────────────────────────────────
        print("Creating users...")
        admin = User(
            username="admin",
            password_hash=generate_password_hash("admin123"),
            role="admin",
            must_change_password=True,
        )
        operator = User(
            username="operator",
            password_hash=generate_password_hash("operator123"),
            role="operator",
            must_change_password=True,
        )
        db.session.add_all([admin, operator])
        db.session.flush()

        # ── Commit everything ──────────────────────────────────────────
        db.session.commit()
        print()
        print("=" * 60)
        print("  Mock data seeded successfully!")
        print("=" * 60)
        print()
        print("  Test accounts:")
        print("  ┌──────────────┬───────────────┬──────────┐")
        print("  │ Username     │ Password      │ Role     │")
        print("  ├──────────────┼───────────────┼──────────┤")
        print("  │ admin        │ admin123      │ admin    │")
        print("  │ operator     │ operator123   │ operator │")
        print("  └──────────────┴───────────────┴──────────┘")
        print()
        print("  Data summary:")
        print(f"    Users:           {User.query.count()}")
        print("  Run the app with:  python app.py")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the database with mock data")
    parser.add_argument("--append", action="store_true",
                        help="Append data without dropping tables")
    args = parser.parse_args()
    seed(append=args.append)
