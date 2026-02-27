#!/usr/bin/env python3
"""
OpenRouter API Key Management System

Manages API keys for classroom distribution with full audit trail.

Roster format: first_name,last_name,email,mq_id,budget,limit_reset
Key naming convention: YYYYMMDD_FirstName LastName_MQID

limit_reset values: daily, weekly, monthly, or empty (no reset).
"""

import csv
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import rich_click as click
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

# Configure rich-click
click.rich_click.USE_MARKDOWN = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.STYLE_ERRORS_SUGGESTION = "magenta italic"
click.rich_click.ERRORS_EPILOGUE = "Run manage_keys.py --help for usage"

console = Console()

PLACEHOLDER_DOMAIN = "@FIXME.mq.edu.au"
VALID_LIMIT_RESETS = {"daily", "weekly", "monthly"}
SCHEMA_VERSION = 2


# ============ COMMON FUNCTIONS ============


def fetch_openrouter_keys(api_key):
    """Fetch all keys from OpenRouter. This is the source of truth."""
    url = "https://openrouter.ai/api/v1/keys"
    headers = {"Authorization": f"Bearer {api_key}"}

    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        raise click.ClickException(f"Error fetching keys: {response.status_code}")

    return response.json()["data"]


def validate_roster_row(row, line_number):
    """Validate a roster row has all required fields populated.

    Raises click.ClickException with a clear message if any required field
    is missing or empty.
    """
    required_fields = ["first_name", "last_name", "mq_id"]
    for field in required_fields:
        value = row.get(field, "").strip()
        if not value:
            raise click.ClickException(
                f"Roster row {line_number} (email={row.get('email', '?')}): "
                f"required field '{field}' is empty. "
                f"All students must have first_name, last_name, and mq_id."
            )


def load_roster(roster_path="roster.csv"):
    """Load roster mapping email -> student info dict.

    Returns {email: {'first_name': str, 'last_name': str, 'mq_id': str,
                      'budget': float|None, 'limit_reset': str|None}}

    Raises click.ClickException if any row is missing required fields.
    """
    roster = {}
    if Path(roster_path).exists():
        with open(roster_path) as f:
            reader = csv.DictReader(f)
            for line_number, row in enumerate(
                reader, start=2
            ):  # start=2: header is line 1
                validate_roster_row(row, line_number)
                budget = row.get("budget", "")
                limit_reset = row.get("limit_reset", "").strip().lower() or None
                if limit_reset and limit_reset not in VALID_LIMIT_RESETS:
                    raise click.ClickException(
                        f"Invalid limit_reset '{limit_reset}' for {row['email']}. "
                        f"Valid values: {', '.join(sorted(VALID_LIMIT_RESETS))}"
                    )
                roster[row["email"]] = {
                    "first_name": row["first_name"].strip(),
                    "last_name": row["last_name"].strip(),
                    "mq_id": row["mq_id"].strip(),
                    "budget": float(budget) if budget else None,
                    "limit_reset": limit_reset,
                }
    return roster


def save_roster(roster_dict, roster_path="roster.csv"):
    """Save roster from dict"""
    with open(roster_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "first_name",
                "last_name",
                "email",
                "mq_id",
                "budget",
                "limit_reset",
            ],
        )
        writer.writeheader()
        for email, info in sorted(roster_dict.items()):
            writer.writerow(
                {
                    "email": email,
                    "first_name": info["first_name"],
                    "last_name": info["last_name"],
                    "mq_id": info.get("mq_id", ""),
                    "budget": info["budget"] if info.get("budget") else "",
                    "limit_reset": info.get("limit_reset") or "",
                }
            )


def display_name(student_info):
    """Build display name from student info dict."""
    return f"{student_info['first_name']} {student_info['last_name']}".strip()


def parse_key_name(key_name):
    """Extract date, display name, and mq_id from key name like '20260227_Chaeyeon Kim_60853425'"""
    match = re.match(r"^(\d{8})_(.+)_(\w+)$", key_name)
    if match:
        return match.group(1), match.group(2), match.group(3)  # date, name, mq_id
    # Fallback for keys without mq_id suffix
    match = re.match(r"^(\d{8})_(.+)$", key_name)
    if match:
        return match.group(1), match.group(2), None
    return None, key_name, None


def build_key_name(student_info, date=None):
    """Build key name: YYYYMMDD_FirstName LastName_MQID"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    name = display_name(student_info)
    return f"{date}_{name}_{student_info['mq_id']}"


def map_keys_to_roster(openrouter_keys, roster):
    """Match OpenRouter keys to roster entries by MQ ID.

    Returns (matched, orphaned) where:
      matched = [(key, email, student_info), ...]
      orphaned = [(key, key_name), ...]
    """
    # Build mq_id -> (email, info) lookup
    mq_id_lookup = {}
    for email, info in roster.items():
        if info.get("mq_id"):
            mq_id_lookup[info["mq_id"]] = (email, info)

    matched = []
    orphaned = []

    for key in openrouter_keys:
        _, _, extracted_mq_id = parse_key_name(key["name"])

        if extracted_mq_id and extracted_mq_id in mq_id_lookup:
            email, info = mq_id_lookup[extracted_mq_id]
            matched.append((key, email, info))
        else:
            orphaned.append((key, key["name"]))

    return matched, orphaned


def load_limits(limits_path="limits.csv"):
    """Load existing limits file with targets"""
    limits = {}
    if Path(limits_path).exists():
        with open(limits_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                limits[row["email"]] = {
                    "target_limit": row.get("target_limit"),
                    "target_disabled": row.get("target_disabled"),
                }
    return limits


def save_limits(openrouter_keys, roster, limits_path="limits.csv"):
    """Save limits.csv preserving targets where they exist"""
    existing_limits = load_limits(limits_path)
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster)

    with open(limits_path, "w", newline="") as f:
        fieldnames = [
            "email",
            "name",
            "mq_id",
            "target_limit",
            "actual_limit",
            "target_disabled",
            "actual_disabled",
            "key_name",
            "hash",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for key, email, info in sorted(matched, key=lambda x: x[1]):
            if email in existing_limits and existing_limits[email].get("target_limit"):
                target_limit_str = existing_limits[email]["target_limit"]
                target_limit = (
                    None if target_limit_str == "unlimited" else float(target_limit_str)
                )
            else:
                target_limit = key.get("limit")

            if email in existing_limits and existing_limits[email].get(
                "target_disabled"
            ):
                target_disabled = (
                    existing_limits[email]["target_disabled"].lower() == "true"
                )
            else:
                target_disabled = key.get("disabled", False)

            writer.writerow(
                {
                    "email": email,
                    "name": display_name(info),
                    "mq_id": info.get("mq_id", ""),
                    "target_limit": target_limit if target_limit else "unlimited",
                    "actual_limit": key.get("limit")
                    if key.get("limit")
                    else "unlimited",
                    "target_disabled": str(target_disabled).lower(),
                    "actual_disabled": str(key.get("disabled", False)).lower(),
                    "key_name": key["name"],
                    "hash": key["hash"],
                }
            )


def export_snapshot(openrouter_keys, roster, prefix="snapshot"):
    """Export timestamped snapshot"""
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{timestamp}.csv"

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "email",
                "name",
                "mq_id",
                "key_name",
                "hash",
                "usage",
                "limit",
                "limit_reset",
                "disabled",
            ]
        )

        for key, email, info in sorted(matched, key=lambda x: x[1]):
            writer.writerow(
                [
                    email,
                    display_name(info),
                    info.get("mq_id", ""),
                    key["name"],
                    key["hash"],
                    key.get("usage", 0),
                    key.get("limit") if key.get("limit") else "unlimited",
                    key.get("limit_reset") or "",
                    key.get("disabled", False),
                ]
            )

    return filename


def update_database(conn, openrouter_keys, roster):
    """Update database with current state"""
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster)

    for key, email, info in matched:
        # Upsert student: ON CONFLICT(email) preserves created_at and avoids the
        # DELETE+INSERT semantics of INSERT OR REPLACE, which could silently remove
        # rows when mq_id uniqueness is violated by duplicate empty values.
        c.execute(
            """INSERT INTO student (email, first_name, last_name, mq_id, created_at)
                     VALUES (?, ?, ?, ?, ?)
                     ON CONFLICT(email) DO UPDATE SET
                         first_name=excluded.first_name,
                         last_name=excluded.last_name,
                         mq_id=excluded.mq_id""",
            (email, info["first_name"], info["last_name"], info["mq_id"], timestamp),
        )

        # Upsert key
        c.execute(
            """INSERT OR REPLACE INTO key
                    (key_hash, key_label, email, key_name, created_at, credit_limit, disabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                key["hash"],
                key.get("label", ""),
                email,
                key["name"],
                key.get("created_at", timestamp),
                key.get("limit"),
                key.get("disabled", False),
            ),
        )

        # Record usage snapshot
        c.execute(
            "INSERT INTO usage (key_hash, usage, checked_at) VALUES (?, ?, ?)",
            (key["hash"], key.get("usage", 0), timestamp),
        )

    conn.commit()


def create_openrouter_key(api_key, name, limit=None, limit_reset=None):
    """Create a new key in OpenRouter"""
    url = "https://openrouter.ai/api/v1/keys"
    payload = {"name": name}
    if limit is not None:
        payload["limit"] = limit
    if limit_reset is not None:
        payload["limit_reset"] = limit_reset

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    response = requests.post(url, json=payload, headers=headers, timeout=30)
    if response.status_code not in [200, 201]:
        raise click.ClickException(
            f"Error creating key for {name}: {response.status_code}"
        )

    return response.json()


def update_openrouter_key(
    api_key, key_hash, limit=None, disabled=None, limit_reset=None
):
    """Update an existing key in OpenRouter"""
    url = f"https://openrouter.ai/api/v1/keys/{key_hash}"
    payload = {}
    if limit is not None:
        payload["limit"] = limit
    if disabled is not None:
        payload["disabled"] = disabled
    if limit_reset is not None:
        payload["limit_reset"] = limit_reset

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    response = requests.patch(url, json=payload, headers=headers, timeout=30)
    if response.status_code not in [200, 201, 204]:
        raise click.ClickException(
            f"Error updating key {key_hash[:8]}...: {response.status_code}"
        )

    return response.json() if response.status_code != 204 else {}


def print_key_table(matched):
    """Print a rich table of key status"""
    table = Table(title="Key Status", show_header=True, header_style="bold magenta")
    table.add_column("Email", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("MQ ID", style="dim")
    table.add_column("Key Name", style="dim")
    table.add_column("Usage", justify="right", style="green")
    table.add_column("Limit", justify="right")
    table.add_column("Reset", style="dim")
    table.add_column("Status", justify="center")

    for key, email, info in sorted(matched, key=lambda x: x[1]):
        needs_fixing = PLACEHOLDER_DOMAIN in email
        status = "[yellow]FIX EMAIL[/yellow]" if needs_fixing else "[green]OK[/green]"
        usage = f"${key.get('usage', 0):.4f}"
        limit = f"${key.get('limit')}" if key.get("limit") else "unlimited"
        reset = key.get("limit_reset") or "-"

        table.add_row(
            email,
            display_name(info),
            info.get("mq_id", ""),
            key["name"],
            usage,
            limit,
            reset,
            status,
        )

    console.print(table)


# ============ CLI COMMANDS ============


@click.group()
@click.pass_context
def cli(ctx):
    """
    # OpenRouter API Key Provisioning System

    Manage API keys for classroom distribution with full audit trail.

    ## Quick Start:
    1. `init-db` - Initialize database
    2. `check` - View current state
    3. `provision` - Create keys from roster
    4. `update` - Apply limit changes

    **Environment:** Requires `OPENROUTER_PROVISIONING_KEY`
    """
    ctx.ensure_object(dict)
    ctx.obj["api_key"] = os.getenv("OPENROUTER_PROVISIONING_KEY")
    if not ctx.obj["api_key"]:
        console.print("[red]Error: OPENROUTER_PROVISIONING_KEY not set[/red]")
        sys.exit(1)


@cli.command()
@click.option("--db", default="keys.db")
def init_db(db):
    """Initialize the database schema"""
    conn = sqlite3.connect(db)
    c = conn.cursor()

    # Check if this is an existing database with an old schema.
    # schema_version table was added in schema version 2. If it is absent the
    # database was created by an older version of this tool and may be missing
    # columns or tables added since then.
    c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    has_version_table = c.fetchone() is not None

    if not has_version_table:
        # Check whether any of our core tables already exist (old schema).
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='student'"
        )
        student_table_exists = c.fetchone() is not None
        if student_table_exists:
            conn.close()
            raise click.ClickException(
                "Database schema is outdated. Delete keys.db and re-run init-db."
            )

    if has_version_table:
        c.execute("SELECT version FROM schema_version")
        row = c.fetchone()
        existing_version = row[0] if row else 0
        if existing_version < SCHEMA_VERSION:
            conn.close()
            raise click.ClickException(
                f"Database schema is outdated (version {existing_version}, current is {SCHEMA_VERSION}). "
                "Delete keys.db and re-run init-db."
            )
        # Already at current version — nothing to do.
        conn.close()
        console.print(
            f"[green]Database already at schema version {SCHEMA_VERSION}: {db}[/green]"
        )
        return

    c.execute("""CREATE TABLE IF NOT EXISTS student
                 (email TEXT PRIMARY KEY,
                  first_name TEXT NOT NULL,
                  last_name TEXT NOT NULL,
                  mq_id TEXT NOT NULL UNIQUE,
                  created_at TIMESTAMP)""")

    c.execute("""CREATE TABLE IF NOT EXISTS key
                 (key_hash TEXT PRIMARY KEY,
                  key_label TEXT NOT NULL,
                  email TEXT NOT NULL,
                  key_name TEXT NOT NULL,
                  created_at TIMESTAMP,
                  credit_limit REAL,
                  disabled BOOLEAN,
                  FOREIGN KEY(email) REFERENCES student(email))""")

    c.execute("""CREATE TABLE IF NOT EXISTS usage
                 (id INTEGER PRIMARY KEY,
                  key_hash TEXT NOT NULL,
                  usage REAL,
                  checked_at TIMESTAMP NOT NULL,
                  FOREIGN KEY(key_hash) REFERENCES key(key_hash))""")

    c.execute("""CREATE TABLE IF NOT EXISTS changelog
                 (id INTEGER PRIMARY KEY,
                  key_hash TEXT NOT NULL,
                  action TEXT NOT NULL,
                  old_value TEXT,
                  new_value TEXT,
                  changed_at TIMESTAMP NOT NULL,
                  FOREIGN KEY(key_hash) REFERENCES key(key_hash))""")

    c.execute("""CREATE TABLE schema_version (version INTEGER NOT NULL)""")
    c.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    conn.commit()
    conn.close()

    console.print(
        f"[green]Database initialized (schema v{SCHEMA_VERSION}): {db}[/green]"
    )


@cli.command()
@click.option("--roster", default="roster.csv", type=click.Path())
@click.option("--db", default="keys.db")
@click.pass_context
def check(ctx, roster, db):
    """Check current state of all keys and reconcile with roster"""

    if not Path(db).exists():
        console.print(f"[red]Error: Database {db} not found. Run 'init-db' first[/red]")
        sys.exit(1)

    # 1. Fetch all keys from OpenRouter (source of truth)
    console.print("[cyan]Fetching keys from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj["api_key"])
    console.print(f"Found {len(openrouter_keys)} keys in OpenRouter")

    # 2. Load roster
    roster_data = load_roster(roster)
    if roster_data:
        console.print(f"Found {len(roster_data)} students in roster")
    else:
        console.print(f"No roster file found at {roster} or file is empty")

    # 3. Match keys to roster
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster_data)

    # 4. Report orphaned keys (do NOT auto-add to roster)
    if orphaned:
        console.print(
            f"\n[yellow]{len(orphaned)} keys in OpenRouter not matched to roster:[/yellow]"
        )
        for key, _extracted_id in orphaned:
            usage = f"${key.get('usage', 0):.4f}"
            disabled = " [dim](disabled)[/dim]" if key.get("disabled") else ""
            console.print(f"  {key['name']} - usage: {usage}{disabled}")
        console.print(
            "[dim]These keys are not managed by this tool. Update roster or manage via OpenRouter dashboard.[/dim]"
        )

    # 5. Update database with current state
    conn = sqlite3.connect(db)
    update_database(conn, openrouter_keys, roster_data)
    conn.close()

    # 6. Export snapshot
    snapshot_file = export_snapshot(openrouter_keys, roster_data)
    console.print(f"\n[green]Exported snapshot to {snapshot_file}[/green]")

    # 7. Summary with rich table
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  Total keys in OpenRouter: {len(openrouter_keys)}")
    console.print(f"  Matched to roster: {len(matched)}")
    console.print(f"  Orphaned (not in roster): {len(orphaned)}")

    if matched:
        console.print()
        print_key_table(matched)


@cli.command()
@click.option("--roster", default="roster.csv", type=click.Path(exists=True))
@click.option(
    "--limit",
    default=None,
    type=float,
    help="Override all budgets with a flat amount (default: per-student from roster)",
)
@click.option("--db", default="keys.db")
@click.option(
    "--dry-run", is_flag=True, help="Show what would happen without creating keys"
)
@click.pass_context
def provision(ctx, roster, limit, db, dry_run):
    """
    **Provision new API keys** for students in roster.csv

    Creates keys with format: `YYYYMMDD_FirstName LastName_MQID`

    Budget is read from each student's `budget` column in roster.csv.
    Use `--limit` to override all budgets with a flat amount.
    """

    if not Path(db).exists():
        console.print(f"[red]Error: Database {db} not found. Run 'init-db' first[/red]")
        sys.exit(1)

    # 1. Fetch current state from OpenRouter (source of truth)
    console.print("[cyan]Fetching current keys from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj["api_key"])
    console.print(f"Found {len(openrouter_keys)} existing keys")

    # 2. Load roster
    roster_data = load_roster(roster)
    if not roster_data:
        console.print("[red]Error: roster.csv is empty[/red]")
        sys.exit(1)
    console.print(f"Found {len(roster_data)} students in roster")

    # 3. Match current keys to roster
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster_data)

    # 4. Find who needs provisioning (in roster but no key)
    already_provisioned = {email for key, email, info in matched}
    to_provision = []

    for email, info in roster_data.items():
        if email not in already_provisioned and PLACEHOLDER_DOMAIN not in email:
            student_limit = limit if limit is not None else info.get("budget")
            if student_limit is None:
                console.print(
                    f"[red]Error: No budget for {email} and no --limit override[/red]"
                )
                sys.exit(1)
            student_reset = info.get("limit_reset")
            to_provision.append((email, info, student_limit, student_reset))

    # Check for placeholder emails
    placeholder_emails = [email for email in roster_data if PLACEHOLDER_DOMAIN in email]
    if placeholder_emails:
        console.print(
            f"\n[yellow]Warning: {len(placeholder_emails)} entries with placeholder emails - skipping[/yellow]"
        )
        for email in placeholder_emails:
            console.print(f"  - {email}")

    if not to_provision:
        console.print("\n[green]All students in roster already have keys[/green]")
        save_limits(openrouter_keys, roster_data)
        console.print("Updated limits.csv with current state")
        snapshot_file = export_snapshot(openrouter_keys, roster_data)
        console.print(f"Exported snapshot to {snapshot_file}")
        return

    # 6. Show provisioning plan
    console.print(f"\n[cyan]Ready to provision {len(to_provision)} new keys:[/cyan]")
    for email, info, student_limit, student_reset in sorted(
        to_provision, key=lambda x: x[0]
    ):
        key_name = build_key_name(info)
        reset_str = f", resets {student_reset}" if student_reset else ""
        console.print(
            f"  {email} ({info.get('mq_id', '')}) -> {key_name} (limit: ${student_limit}{reset_str})"
        )

    if dry_run:
        console.print("\n[yellow]--dry-run specified, no keys created[/yellow]")
        return

    # 7. Create keys with Progress bar
    console.print("\n[cyan]Creating keys...[/cyan]")
    created_keys = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            "[cyan]Provisioning keys...[/cyan]", total=len(to_provision)
        )

        for email, info, student_limit, student_reset in to_provision:
            key_name = build_key_name(info)
            progress.update(
                task,
                description=f"Creating key for [yellow]{display_name(info)}[/yellow]",
            )

            try:
                result = create_openrouter_key(
                    ctx.obj["api_key"], key_name, student_limit, student_reset
                )

                created_keys.append(
                    {
                        "email": email,
                        "info": info,
                        "limit": student_limit,
                        "limit_reset": student_reset,
                        "key_data": result["data"],
                        "api_key": result["key"],
                    }
                )

                progress.advance(task)
                time.sleep(1)  # Be nice to the API

            except Exception as e:
                console.print(f"\n[red]Error creating key for {email}: {e}[/red]")
                console.print(
                    "[yellow]Provisioning halted. Keys created so far have been preserved.[/yellow]"
                )

                if created_keys:
                    console.print(
                        f"[green]Successfully created {len(created_keys)} keys before failure[/green]"
                    )
                sys.exit(1)

    # 8. Fetch updated state from OpenRouter
    console.print("\n[cyan]Fetching updated state from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj["api_key"])

    # 9. Update database
    conn = sqlite3.connect(db)
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    for key_info in created_keys:
        c.execute(
            """INSERT INTO changelog
                     (key_hash, action, old_value, new_value, changed_at)
                     VALUES (?, ?, ?, ?, ?)""",
            (
                key_info["key_data"]["hash"],
                "provisioned",
                None,
                f"limit={key_info['limit']},reset={key_info.get('limit_reset')}",
                timestamp,
            ),
        )

    update_database(conn, openrouter_keys, roster_data)
    conn.commit()
    conn.close()

    # 10. Save limits.csv
    save_limits(openrouter_keys, roster_data)
    console.print("\n[green]Updated limits.csv with current state[/green]")

    # 11. Export snapshot
    snapshot_file = export_snapshot(openrouter_keys, roster_data)
    console.print(f"[green]Exported snapshot to {snapshot_file}[/green]")

    # 12. Save API keys for distribution
    if created_keys:
        keys_file = f"api_keys_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(keys_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "first_name",
                    "last_name",
                    "email",
                    "mq_id",
                    "api_key",
                    "key_name",
                    "budget",
                    "limit_reset",
                ]
            )
            for key_info in created_keys:
                info = key_info["info"]
                writer.writerow(
                    [
                        info["first_name"],
                        info["last_name"],
                        key_info["email"],
                        info.get("mq_id", ""),
                        key_info["api_key"],
                        key_info["key_data"]["name"],
                        key_info["limit"],
                        key_info.get("limit_reset") or "",
                    ]
                )

        console.print(
            Panel.fit(
                f"[bold green]Successfully created {len(created_keys)} keys![/bold green]\n"
                f"Total active keys: {len(openrouter_keys)}\n\n"
                f"API keys saved to: [cyan]{keys_file}[/cyan]\n"
                f"[yellow]This file contains secrets - handle with care![/yellow]",
                title="Provisioning Complete",
                border_style="green",
            )
        )


@cli.command()
@click.option("--limits", default="limits.csv", type=click.Path(exists=True))
@click.option("--roster", default="roster.csv", type=click.Path(exists=True))
@click.option("--db", default="keys.db")
@click.option(
    "--dry-run", is_flag=True, help="Show what would happen without applying changes"
)
@click.pass_context
def update(ctx, limits, roster, db, dry_run):
    """Apply limit changes from limits.csv to OpenRouter"""

    if not Path(db).exists():
        console.print(f"[red]Error: Database {db} not found. Run 'init-db' first[/red]")
        sys.exit(1)

    # 1. Fetch current state from OpenRouter (source of truth)
    console.print("[cyan]Fetching current keys from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj["api_key"])
    console.print(f"Found {len(openrouter_keys)} keys")

    # 2. Load roster for identity mapping
    roster_data = load_roster(roster)

    # 3. Build mapping of email -> key
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster_data)
    email_to_key = {email: key for key, email, info in matched}

    # 4. Load desired changes from limits.csv
    changes_to_apply = []
    with open(limits) as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row["email"]
            if email not in email_to_key:
                console.print(
                    f"[yellow]Warning: {email} in limits.csv but not found in OpenRouter - skipping[/yellow]"
                )
                continue

            key = email_to_key[email]

            target_limit = row.get("target_limit")
            actual_limit = key.get("limit")

            if target_limit == "unlimited":
                target_limit = None
            elif target_limit:
                target_limit = float(target_limit)

            if target_limit != actual_limit:
                changes_to_apply.append(
                    {
                        "email": email,
                        "key_hash": key["hash"],
                        "key_name": key["name"],
                        "change_type": "limit",
                        "old_value": actual_limit,
                        "new_value": target_limit,
                    }
                )

            target_disabled = row.get("target_disabled", "false").lower() == "true"
            actual_disabled = key.get("disabled", False)

            if target_disabled != actual_disabled:
                changes_to_apply.append(
                    {
                        "email": email,
                        "key_hash": key["hash"],
                        "key_name": key["name"],
                        "change_type": "disabled",
                        "old_value": actual_disabled,
                        "new_value": target_disabled,
                    }
                )

    if not changes_to_apply:
        console.print("\n[green]No changes needed - targets match actuals[/green]")
        save_limits(openrouter_keys, roster_data, limits)
        snapshot_file = export_snapshot(openrouter_keys, roster_data)
        console.print(f"Exported snapshot to {snapshot_file}")
        return

    # 5. Show planned changes
    console.print(f"\n[cyan]Planned changes ({len(changes_to_apply)}):[/cyan]")
    for change in changes_to_apply:
        if change["change_type"] == "limit":
            old = f"${change['old_value']}" if change["old_value"] else "unlimited"
            new = f"${change['new_value']}" if change["new_value"] else "unlimited"
        else:
            old = "disabled" if change["old_value"] else "enabled"
            new = "disabled" if change["new_value"] else "enabled"
        console.print(f"  {change['key_name']}: {change['change_type']} {old} -> {new}")

    if dry_run:
        console.print("\n[yellow]--dry-run specified, no changes applied[/yellow]")
        return

    # 6. Apply changes
    console.print("\n[cyan]Applying changes...[/cyan]")
    conn = sqlite3.connect(db)
    c = conn.cursor()
    timestamp = datetime.now().isoformat()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            "[cyan]Updating keys...[/cyan]", total=len(changes_to_apply)
        )

        for change in changes_to_apply:
            progress.update(
                task, description=f"Updating [yellow]{change['key_name']}[/yellow]"
            )

            try:
                if change["change_type"] == "limit":
                    update_openrouter_key(
                        ctx.obj["api_key"],
                        change["key_hash"],
                        limit=change["new_value"],
                    )
                else:
                    update_openrouter_key(
                        ctx.obj["api_key"],
                        change["key_hash"],
                        disabled=change["new_value"],
                    )

                c.execute(
                    """INSERT INTO changelog
                            (key_hash, action, old_value, new_value, changed_at)
                            VALUES (?, ?, ?, ?, ?)""",
                    (
                        change["key_hash"],
                        f"update_{change['change_type']}",
                        str(change["old_value"]),
                        str(change["new_value"]),
                        timestamp,
                    ),
                )

                progress.advance(task)
                time.sleep(1)

            except Exception as e:
                console.print(f"\n[red]Error updating {change['key_name']}: {e}[/red]")
                console.print(
                    "[yellow]Update halted. Changes applied so far have been preserved.[/yellow]"
                )
                conn.commit()
                conn.close()
                sys.exit(1)

    conn.commit()
    conn.close()

    # 7. Fetch fresh state and update all files
    console.print("\n[cyan]Fetching updated state from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj["api_key"])

    conn = sqlite3.connect(db)
    update_database(conn, openrouter_keys, roster_data)
    conn.close()

    save_limits(openrouter_keys, roster_data, limits)
    console.print(f"\n[green]Updated {limits} with current state[/green]")

    snapshot_file = export_snapshot(openrouter_keys, roster_data)
    console.print(f"[green]Exported snapshot to {snapshot_file}[/green]")

    console.print(
        f"\n[bold green]Update complete! Applied {len(changes_to_apply)} changes[/bold green]"
    )


@cli.command("refresh-limits-file")
@click.option("--limits", default="limits.csv")
@click.option("--roster", default="roster.csv", type=click.Path(exists=True))
@click.pass_context
def refresh_limits_file(ctx, limits, roster):
    """Refresh limits.csv with current state from OpenRouter (preserves targets)"""

    console.print("[cyan]Fetching current keys from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj["api_key"])
    console.print(f"Found {len(openrouter_keys)} keys")

    roster_data = load_roster(roster)
    if not roster_data:
        console.print("[red]Error: roster.csv is empty or missing[/red]")
        sys.exit(1)

    save_limits(openrouter_keys, roster_data, limits)

    matched, orphaned = map_keys_to_roster(openrouter_keys, roster_data)

    console.print(f"\n[green]Refreshed {limits}:[/green]")
    console.print(f"  Total keys: {len(openrouter_keys)}")
    console.print(f"  Matched to roster: {len(matched)}")

    mismatches = []
    with open(limits) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["target_limit"] != row["actual_limit"]:
                mismatches.append(
                    f"  {row['name']}: limit target=${row['target_limit']} actual=${row['actual_limit']}"
                )
            if row["target_disabled"] != row["actual_disabled"]:
                mismatches.append(
                    f"  {row['name']}: disabled target={row['target_disabled']} actual={row['actual_disabled']}"
                )

    if mismatches:
        console.print("\n[yellow]Mismatches between target and actual:[/yellow]")
        for mismatch in mismatches:
            console.print(mismatch)
        console.print(
            "\n[cyan]Run 'update' to apply target values to OpenRouter[/cyan]"
        )
    else:
        console.print("\n[green]All targets match actuals[/green]")


@cli.command("export-keys")
@click.option("--db", default="keys.db")
@click.option(
    "--output", default=None, help="Output filename (default: api_keys_TIMESTAMP.csv)"
)
@click.option(
    "--format", type=click.Choice(["csv", "json"]), default="csv", help="Output format"
)
@click.pass_context
def export_keys(ctx, db, output, format):
    """
    **Export API keys** from database

    Retrieves stored API keys for distribution to students.

    **Security Warning**: Output contains secret API keys!
    """

    if not Path(db).exists():
        console.print("[red]Error: Database not found. Run 'init-db' first[/red]")
        sys.exit(1)

    conn = sqlite3.connect(db)
    c = conn.cursor()

    c.execute("""
        SELECT k.key_hash, k.key_label, k.email, k.key_name, k.credit_limit, k.disabled,
               s.first_name, s.last_name, s.mq_id
        FROM key k
        JOIN student s ON k.email = s.email
        ORDER BY k.email
    """)

    keys = c.fetchall()
    conn.close()

    if not keys:
        console.print("[yellow]No keys found in database[/yellow]")
        return

    if output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = f"api_keys_{timestamp}.{format}"

    if format == "csv":
        with open(output, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "first_name",
                    "last_name",
                    "email",
                    "mq_id",
                    "api_key",
                    "key_name",
                    "limit",
                    "disabled",
                ]
            )

            for (
                _key_hash,
                api_key,
                email,
                key_name,
                credit_limit,
                disabled,
                first_name,
                last_name,
                mq_id,
            ) in keys:
                writer.writerow(
                    [
                        first_name,
                        last_name,
                        email,
                        mq_id or "",
                        api_key if api_key else "[Key not stored - check OpenRouter]",
                        key_name,
                        credit_limit if credit_limit else "unlimited",
                        "true" if disabled else "false",
                    ]
                )

    else:  # json
        keys_data = []
        for (
            _key_hash,
            api_key,
            email,
            key_name,
            credit_limit,
            disabled,
            first_name,
            last_name,
            mq_id,
        ) in keys:
            keys_data.append(
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "mq_id": mq_id or "",
                    "api_key": api_key
                    if api_key
                    else "[Key not stored - check OpenRouter]",
                    "key_name": key_name,
                    "limit": credit_limit if credit_limit else "unlimited",
                    "disabled": disabled,
                }
            )

        with open(output, "w") as f:
            json.dump(keys_data, f, indent=2)

    console.print(
        Panel.fit(
            f"[bold green]Exported {len(keys)} keys to {output}[/bold green]\n\n"
            f"[yellow]This file contains API keys - handle with care![/yellow]\n"
            f"[dim]Consider using secure distribution methods (encrypted email, secure file share)[/dim]",
            title="Keys Exported",
            border_style="green",
        )
    )

    table = Table(title="Exported Keys Summary", show_header=True)
    table.add_column("Email", style="cyan")
    table.add_column("Name")
    table.add_column("MQ ID", style="dim")
    table.add_column("Key Name", style="dim")
    table.add_column("Has API Key?", justify="center")

    for (
        _key_hash,
        api_key,
        email,
        key_name,
        _credit_limit,
        _disabled,
        first_name,
        last_name,
        mq_id,
    ) in keys:
        has_key = "yes" if api_key else "no"
        table.add_row(
            email, f"{first_name} {last_name}", mq_id or "", key_name, has_key
        )

    console.print(table)


if __name__ == "__main__":
    cli()
