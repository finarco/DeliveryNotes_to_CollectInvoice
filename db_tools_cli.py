#!/usr/bin/env python3
"""Standalone CLI runner for database maintenance tools.

Usage:
    python db_tools_cli.py --help
    python db_tools_cli.py backup
    python db_tools_cli.py wipe --dry-run
    python db_tools_cli.py import partners.csv --entity-type partner
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_tools.cli import cli

if __name__ == "__main__":
    cli()
