#!/usr/bin/env python3
"""
OpenRouter API Key Management System

Manages API keys for classroom distribution with full audit trail.
"""

import rich_click as click
import csv
import sqlite3
import requests
import os
import sys
import re
import time
import json
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel

# Configure rich-click
click.rich_click.USE_MARKDOWN = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.STYLE_ERRORS_SUGGESTION = "magenta italic"
click.rich_click.ERRORS_EPILOGUE = "Run manage_keys.py --help for usage"

console = Console()


# ============ COMMON FUNCTIONS ============

def fetch_openrouter_keys(api_key):
    """Fetch all keys from OpenRouter. This is the source of truth."""
    url = "https://openrouter.ai/api/v1/keys"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise click.ClickException(f"Error fetching keys: {response.status_code}")
    
    return response.json()['data']


def load_roster(roster_path='roster.csv'):
    """Load roster mapping email -> name"""
    roster = {}
    if Path(roster_path).exists():
        with open(roster_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                roster[row['email']] = row['name']
    return roster


def save_roster(roster_dict, roster_path='roster.csv'):
    """Save roster from dict"""
    with open(roster_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['email', 'name'])
        writer.writeheader()
        for email, name in sorted(roster_dict.items()):
            writer.writerow({'email': email, 'name': name})


def parse_key_name(key_name):
    """Extract date and student name from key name like '20250904_Alice Smith'"""
    match = re.match(r'^(\d{8})_(.+)$', key_name)
    if match:
        return match.group(1), match.group(2)  # date, name
    return None, key_name  # No date prefix


def build_key_name(student_name, date=None):
    """Build key name with today's date prefix"""
    if date is None:
        date = datetime.now().strftime('%Y%m%d')
    return f"{date}_{student_name}"


def map_keys_to_roster(openrouter_keys, roster):
    """Match OpenRouter keys to roster entries. Returns (matched, orphaned)"""
    matched = []  # List of (key, email, name)
    orphaned = []  # List of (key, extracted_name)
    
    for key in openrouter_keys:
        _, extracted_name = parse_key_name(key['name'])
        
        # Find in roster by name
        found = False
        for email, roster_name in roster.items():
            if roster_name == extracted_name:
                matched.append((key, email, roster_name))
                found = True
                break
        
        if not found:
            orphaned.append((key, extracted_name))
    
    return matched, orphaned


def load_limits(limits_path='limits.csv'):
    """Load existing limits file with targets"""
    limits = {}
    if Path(limits_path).exists():
        with open(limits_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                limits[row['email']] = {
                    'target_limit': row.get('target_limit'),
                    'target_disabled': row.get('target_disabled')
                }
    return limits


def save_limits(openrouter_keys, roster, limits_path='limits.csv'):
    """Save limits.csv preserving targets where they exist"""
    existing_limits = load_limits(limits_path)
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster)
    
    with open(limits_path, 'w', newline='') as f:
        fieldnames = ['email', 'name', 'target_limit', 'actual_limit', 
                     'target_disabled', 'actual_disabled', 'key_name', 'hash']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        # Write all matched keys
        for key, email, name in sorted(matched, key=lambda x: x[1]):
            # Preserve existing targets or use actuals
            if email in existing_limits and existing_limits[email].get('target_limit'):
                target_limit_str = existing_limits[email]['target_limit']
                # Handle 'unlimited' case
                if target_limit_str == 'unlimited':
                    target_limit = None
                else:
                    target_limit = float(target_limit_str)
            else:
                target_limit = key.get('limit')
            
            if email in existing_limits and existing_limits[email].get('target_disabled'):
                target_disabled = existing_limits[email]['target_disabled'].lower() == 'true'
            else:
                target_disabled = key.get('disabled', False)
            
            writer.writerow({
                'email': email,
                'name': name,
                'target_limit': target_limit if target_limit else 'unlimited',
                'actual_limit': key.get('limit') if key.get('limit') else 'unlimited',
                'target_disabled': str(target_disabled).lower(),
                'actual_disabled': str(key.get('disabled', False)).lower(),
                'key_name': key['name'],
                'hash': key['hash']
            })


def export_snapshot(openrouter_keys, roster, prefix='snapshot'):
    """Export timestamped snapshot"""
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{prefix}_{timestamp}.csv"
    
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['email', 'name', 'key_name', 'hash', 'usage', 'limit', 'disabled'])
        
        for key, email, name in sorted(matched, key=lambda x: x[1]):
            writer.writerow([
                email, name, key['name'], key['hash'],
                key.get('usage', 0),
                key.get('limit') if key.get('limit') else 'unlimited',
                key.get('disabled', False)
            ])
    
    return filename


def update_database(conn, openrouter_keys, roster):
    """Update database with current state"""
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster)
    
    for key, email, name in matched:
        # Upsert student
        c.execute("INSERT OR IGNORE INTO student (email, name, created_at) VALUES (?, ?, ?)",
                 (email, name, timestamp))
        
        # Upsert key
        c.execute("""INSERT OR REPLACE INTO key 
                    (key_hash, api_key, email, key_name, created_at, credit_limit, disabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                 (key['hash'], key.get('label', ''), email, key['name'],
                  key.get('created_at', timestamp), key.get('limit'), key.get('disabled', False)))
        
        # Record usage snapshot
        c.execute("INSERT INTO usage (key_hash, usage, checked_at) VALUES (?, ?, ?)",
                 (key['hash'], key.get('usage', 0), timestamp))
    
    conn.commit()


def create_openrouter_key(api_key, name, limit=None):
    """Create a new key in OpenRouter"""
    url = "https://openrouter.ai/api/v1/keys"
    payload = {"name": name}
    if limit is not None:
        payload["limit"] = limit
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code not in [200, 201]:  # 201 Created is success!
        raise click.ClickException(f"Error creating key for {name}: {response.status_code}")
    
    return response.json()


def update_openrouter_key(api_key, key_hash, limit=None, disabled=None):
    """Update an existing key in OpenRouter"""
    url = f"https://openrouter.ai/api/v1/keys/{key_hash}"
    payload = {}
    if limit is not None:
        payload["limit"] = limit
    if disabled is not None:
        payload["disabled"] = disabled
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    response = requests.patch(url, json=payload, headers=headers)
    if response.status_code not in [200, 201, 204]:  # Include all success codes
        raise click.ClickException(f"Error updating key {key_hash[:8]}...: {response.status_code}")
    
    return response.json() if response.status_code != 204 else {}


def print_key_table(matched):
    """Print a rich table of key status"""
    table = Table(title="Key Status", show_header=True, header_style="bold magenta")
    table.add_column("Email", style="cyan", no_wrap=True)
    table.add_column("Key Name", style="dim")
    table.add_column("Usage", justify="right", style="green")
    table.add_column("Limit", justify="right")
    table.add_column("Status", justify="center")
    
    for key, email, name in sorted(matched, key=lambda x: x[1]):
        needs_fixing = "@FIXME.edu" in email
        status = "[yellow]⚠️ FIX EMAIL[/yellow]" if needs_fixing else "[green]✅[/green]"
        usage = f"${key.get('usage', 0):.4f}"
        limit = f"${key.get('limit')}" if key.get('limit') else "unlimited"
        
        table.add_row(email, key['name'], usage, limit, status)
    
    console.print(table)


# ============ CLI COMMANDS ============

@click.group()
@click.pass_context
def cli(ctx):
    """
    # OpenRouter API Key Provisioning System 🔑
    
    Manage API keys for classroom distribution with full audit trail.
    
    ## Quick Start:
    1. `init-db` - Initialize database
    2. `check` - View current state  
    3. `provision` - Create keys from roster
    4. `update` - Apply limit changes
    
    **Environment:** Requires `OPENROUTER_PROVISIONING_KEY`
    """
    ctx.ensure_object(dict)
    ctx.obj['api_key'] = os.getenv('OPENROUTER_PROVISIONING_KEY')
    if not ctx.obj['api_key']:
        console.print("[red]Error: OPENROUTER_PROVISIONING_KEY not set[/red]")
        sys.exit(1)


@cli.command()
@click.option('--db', default='keys.db')
def init_db(db):
    """Initialize the database schema"""
    conn = sqlite3.connect(db)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS student
                 (email TEXT PRIMARY KEY, 
                  name TEXT, 
                  created_at TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS key
                 (key_hash TEXT PRIMARY KEY, 
                  api_key TEXT,
                  email TEXT,
                  key_name TEXT,
                  created_at TIMESTAMP,
                  credit_limit REAL,
                  disabled BOOLEAN,
                  FOREIGN KEY(email) REFERENCES student(email))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS usage
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  key_hash TEXT,
                  usage REAL,
                  checked_at TIMESTAMP,
                  FOREIGN KEY(key_hash) REFERENCES key(key_hash))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS changelog
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  key_hash TEXT,
                  action TEXT,
                  old_value TEXT,
                  new_value TEXT,
                  changed_at TIMESTAMP,
                  FOREIGN KEY(key_hash) REFERENCES key(key_hash))''')
    
    conn.commit()
    conn.close()
    
    console.print(f"[green]Database initialized: {db}[/green]")
    console.print("Tables created:")
    console.print("  - student (email, name, created_at)")
    console.print("  - key (key_hash, api_key, email, key_name, created_at, credit_limit, disabled)")
    console.print("  - usage (id, key_hash, usage, checked_at)")
    console.print("  - changelog (id, key_hash, action, old_value, new_value, changed_at)")


@cli.command()
@click.option('--roster', default='roster.csv', type=click.Path())
@click.option('--db', default='keys.db')
@click.pass_context
def check(ctx, roster, db):
    """Check current state of all keys and reconcile with roster"""
    
    # Ensure database exists
    if not Path(db).exists():
        console.print(f"[red]Error: Database {db} not found. Run 'init-db' first[/red]")
        sys.exit(1)
    
    # 1. Fetch all keys from OpenRouter (source of truth)
    console.print("[cyan]Fetching keys from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj['api_key'])
    console.print(f"Found {len(openrouter_keys)} keys in OpenRouter")
    
    # 2. Load roster
    roster_data = load_roster(roster)
    if roster_data:
        console.print(f"Found {len(roster_data)} students in roster")
    else:
        console.print(f"No roster file found at {roster} or file is empty")
    
    # 3. Match keys to roster
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster_data)
    
    # 4. Handle orphaned keys - add them to roster
    if orphaned:
        console.print(f"\n[yellow]Adding {len(orphaned)} orphaned keys to roster...[/yellow]")
        
        for key, extracted_name in orphaned:
            # Generate placeholder email
            placeholder_email = f"{extracted_name.lower().replace(' ', '.')}@FIXME.edu"
            roster_data[placeholder_email] = extracted_name
            console.print(f"  Added: {placeholder_email},{extracted_name}")
        
        # Save updated roster
        save_roster(roster_data, roster)
        console.print(f"\n[yellow]⚠️  Please edit {roster} to replace FIXME.edu with actual email addresses[/yellow]")
        
        # Re-match now that roster is updated
        matched, orphaned = map_keys_to_roster(openrouter_keys, roster_data)
    
    # 5. Update database with current state
    conn = sqlite3.connect(db)
    update_database(conn, openrouter_keys, roster_data)
    conn.close()
    
    # 6. Export snapshot
    snapshot_file = export_snapshot(openrouter_keys, roster_data)
    console.print(f"\n[green]Exported snapshot to {snapshot_file}[/green]")
    
    # 7. Summary with rich table
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Total keys: {len(openrouter_keys)}")
    console.print(f"  Matched to roster: {len(matched)}")
    
    console.print()
    print_key_table(matched)


@cli.command()
@click.option('--roster', default='roster.csv', type=click.Path(exists=True))
@click.option('--limit', default=2.0, help='Default credit limit for new keys')
@click.option('--db', default='keys.db')
@click.option('--dry-run', is_flag=True, help='Show what would happen without creating keys')
@click.pass_context
def provision(ctx, roster, limit, db, dry_run):
    """
    **Provision new API keys** for students in roster.csv
    
    Creates keys with format: `YYYYMMDD_StudentName`
    """
    
    # Ensure database exists
    if not Path(db).exists():
        console.print(f"[red]Error: Database {db} not found. Run 'init-db' first[/red]")
        sys.exit(1)
    
    # 1. Fetch current state from OpenRouter (source of truth)
    console.print("[cyan]Fetching current keys from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj['api_key'])
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
    already_provisioned = {email for key, email, name in matched}
    to_provision = []
    
    for email, name in roster_data.items():
        if email not in already_provisioned and "@FIXME.edu" not in email:
            to_provision.append((email, name))
    
    # Check for FIXME emails
    fixme_emails = [email for email in roster_data.keys() if "@FIXME.edu" in email]
    if fixme_emails:
        console.print(f"\n[yellow]⚠️  Warning: {len(fixme_emails)} entries with FIXME.edu emails - skipping these[/yellow]")
        for email in fixme_emails:
            console.print(f"  - {email}")
    
    if not to_provision:
        console.print("\n[green]All students in roster already have keys[/green]")
        # Still update limits.csv and export snapshot with current state
        save_limits(openrouter_keys, roster_data)
        console.print(f"Updated limits.csv with current state")
        
        snapshot_file = export_snapshot(openrouter_keys, roster_data)
        console.print(f"Exported snapshot to {snapshot_file}")
        return
    
    # 5. Show provisioning plan
    console.print(f"\n[cyan]Ready to provision {len(to_provision)} new keys:[/cyan]")
    for email, name in sorted(to_provision):
        key_name = build_key_name(name)
        console.print(f"  {email} → {key_name} (limit: ${limit})")
    
    if dry_run:
        console.print("\n[yellow]--dry-run specified, no keys created[/yellow]")
        return
    
    # 6. Create keys with Progress bar
    console.print("\n[cyan]Creating keys...[/cyan]")
    created_keys = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Provisioning keys...[/cyan]", total=len(to_provision))
        
        for email, name in to_provision:
            key_name = build_key_name(name)
            progress.update(task, description=f"Creating key for [yellow]{email}[/yellow]")
            
            try:
                result = create_openrouter_key(ctx.obj['api_key'], key_name, limit)
                
                # Store the created key data
                created_keys.append({
                    'email': email,
                    'name': name,
                    'key_data': result['data'],
                    'api_key': result['key']  # The actual key string
                })
                
                progress.advance(task)
                time.sleep(1)  # Be nice to the API
                
            except Exception as e:
                console.print(f"\n[red]✗ Error creating key for {email}: {e}[/red]")
                console.print("[yellow]Provisioning halted. Keys created so far have been preserved.[/yellow]")
                
                if created_keys:
                    console.print(f"[green]Successfully created {len(created_keys)} keys before failure[/green]")
                sys.exit(1)
    
    # 7. Fetch updated state from OpenRouter (to get the new keys in our list)
    console.print("\n[cyan]Fetching updated state from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj['api_key'])
    
    # 8. Update database with new state
    conn = sqlite3.connect(db)
    
    # Log the provisioning in changelog
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    for key_info in created_keys:
        c.execute("""INSERT INTO changelog 
                     (key_hash, action, old_value, new_value, changed_at)
                     VALUES (?, ?, ?, ?, ?)""",
                  (key_info['key_data']['hash'], 'provisioned', 
                   None, f"limit={limit}", timestamp))
    
    # Update all records
    update_database(conn, openrouter_keys, roster_data)
    conn.commit()
    conn.close()
    
    # 9. Save limits.csv with new state
    save_limits(openrouter_keys, roster_data)
    console.print(f"\n[green]Updated limits.csv with current state[/green]")
    
    # 10. Export snapshot
    snapshot_file = export_snapshot(openrouter_keys, roster_data)
    console.print(f"[green]Exported snapshot to {snapshot_file}[/green]")
    
    # 11. Save API keys and show success panel
    if created_keys:
        keys_file = f"api_keys_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(keys_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['email', 'name', 'api_key', 'key_name'])
            for key_info in created_keys:
                writer.writerow([
                    key_info['email'],
                    key_info['name'], 
                    key_info['api_key'],
                    key_info['key_data']['name']
                ])
        
        console.print(Panel.fit(
            f"[bold green]Successfully created {len(created_keys)} keys![/bold green]\n"
            f"Total active keys: {len(openrouter_keys)}\n\n"
            f"API keys saved to: [cyan]{keys_file}[/cyan]\n"
            f"[yellow]⚠️  This file contains secrets - handle with care![/yellow]",
            title="🔑 Provisioning Complete",
            border_style="green"
        ))


@cli.command()
@click.option('--limits', default='limits.csv', type=click.Path(exists=True))
@click.option('--roster', default='roster.csv', type=click.Path(exists=True))
@click.option('--db', default='keys.db')
@click.option('--dry-run', is_flag=True, help='Show what would happen without applying changes')
@click.pass_context
def update(ctx, limits, roster, db, dry_run):
    """Apply limit changes from limits.csv to OpenRouter"""
    
    # Ensure database exists
    if not Path(db).exists():
        console.print(f"[red]Error: Database {db} not found. Run 'init-db' first[/red]")
        sys.exit(1)
    
    # 1. Fetch current state from OpenRouter (source of truth)
    console.print("[cyan]Fetching current keys from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj['api_key'])
    console.print(f"Found {len(openrouter_keys)} keys")
    
    # 2. Load roster for identity mapping
    roster_data = load_roster(roster)
    
    # 3. Build mapping of email -> key
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster_data)
    email_to_key = {email: key for key, email, name in matched}
    
    # 4. Load desired changes from limits.csv
    changes_to_apply = []
    with open(limits, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row['email']
            if email not in email_to_key:
                console.print(f"[yellow]⚠️  Warning: {email} in limits.csv but not found in OpenRouter - skipping[/yellow]")
                continue
            
            key = email_to_key[email]
            
            # Check for limit changes
            target_limit = row.get('target_limit')
            actual_limit = key.get('limit')

            # Handle unlimited case
            if target_limit == 'unlimited':
                target_limit = None
            elif target_limit:
                target_limit = float(target_limit)

            # Compare None (unlimited) properly
            if target_limit != actual_limit:
                changes_to_apply.append({
                    'email': email,
                    'key_hash': key['hash'],
                    'key_name': key['name'],
                    'change_type': 'limit',
                    'old_value': actual_limit,
                    'new_value': target_limit
                })
            
            # Check for disabled status changes
            target_disabled = row.get('target_disabled', 'false').lower() == 'true'
            actual_disabled = key.get('disabled', False)
            
            if target_disabled != actual_disabled:
                changes_to_apply.append({
                    'email': email,
                    'key_hash': key['hash'],
                    'key_name': key['name'],
                    'change_type': 'disabled',
                    'old_value': actual_disabled,
                    'new_value': target_disabled
                })
    
    if not changes_to_apply:
        console.print("\n[green]No changes needed - targets match actuals[/green]")
        # Still refresh limits.csv and export snapshot
        save_limits(openrouter_keys, roster_data, limits)
        snapshot_file = export_snapshot(openrouter_keys, roster_data)
        console.print(f"Exported snapshot to {snapshot_file}")
        return
    
    # 5. Show planned changes
    console.print(f"\n[cyan]Planned changes ({len(changes_to_apply)}):[/cyan]")
    for change in changes_to_apply:
        if change['change_type'] == 'limit':
            old = f"${change['old_value']}" if change['old_value'] else "unlimited"
            new = f"${change['new_value']}" if change['new_value'] else "unlimited"
        else:
            old = "disabled" if change['old_value'] else "enabled"
            new = "disabled" if change['new_value'] else "enabled"
        console.print(f"  {change['key_name']}: {change['change_type']} {old} → {new}")
    
    if dry_run:
        console.print("\n[yellow]--dry-run specified, no changes applied[/yellow]")
        return
    
    # 6. Apply changes with progress bar
    console.print("\n[cyan]Applying changes...[/cyan]")
    conn = sqlite3.connect(db)
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Updating keys...[/cyan]", total=len(changes_to_apply))
        
        for change in changes_to_apply:
            progress.update(task, description=f"Updating [yellow]{change['key_name']}[/yellow]")
            
            try:
                if change['change_type'] == 'limit':
                    update_openrouter_key(ctx.obj['api_key'], change['key_hash'], 
                                        limit=change['new_value'])
                else:  # disabled
                    update_openrouter_key(ctx.obj['api_key'], change['key_hash'], 
                                        disabled=change['new_value'])
                
                # Log to changelog
                c.execute("""INSERT INTO changelog 
                            (key_hash, action, old_value, new_value, changed_at)
                            VALUES (?, ?, ?, ?, ?)""",
                         (change['key_hash'], f"update_{change['change_type']}", 
                          str(change['old_value']), str(change['new_value']), timestamp))
                
                progress.advance(task)
                time.sleep(1)  # Be nice to API
                
            except Exception as e:
                console.print(f"\n[red]✗ Error updating {change['key_name']}: {e}[/red]")
                console.print("[yellow]Update halted. Changes applied so far have been preserved.[/yellow]")
                conn.commit()
                conn.close()
                sys.exit(1)
    
    conn.commit()
    conn.close()
    
    # 7. Fetch fresh state and update all files
    console.print("\n[cyan]Fetching updated state from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj['api_key'])
    
    # Update database
    conn = sqlite3.connect(db)
    update_database(conn, openrouter_keys, roster_data)
    conn.close()
    
    # Update limits.csv with new actual values
    save_limits(openrouter_keys, roster_data, limits)
    console.print(f"\n[green]Updated {limits} with current state[/green]")
    
    # Export snapshot
    snapshot_file = export_snapshot(openrouter_keys, roster_data)
    console.print(f"[green]Exported snapshot to {snapshot_file}[/green]")
    
    console.print(f"\n[bold green]Update complete! Applied {len(changes_to_apply)} changes[/bold green]")


@cli.command('refresh-limits-file')
@click.option('--limits', default='limits.csv')
@click.option('--roster', default='roster.csv', type=click.Path(exists=True))
@click.pass_context
def refresh_limits_file(ctx, limits, roster):
    """Refresh limits.csv with current state from OpenRouter (preserves targets)"""
    
    # 1. Fetch current state from OpenRouter
    console.print("[cyan]Fetching current keys from OpenRouter...[/cyan]")
    openrouter_keys = fetch_openrouter_keys(ctx.obj['api_key'])
    console.print(f"Found {len(openrouter_keys)} keys")
    
    # 2. Load roster
    roster_data = load_roster(roster)
    if not roster_data:
        console.print("[red]Error: roster.csv is empty or missing[/red]")
        sys.exit(1)
    
    # 3. Save limits with preserved targets
    save_limits(openrouter_keys, roster_data, limits)
    
    # 4. Show summary
    matched, orphaned = map_keys_to_roster(openrouter_keys, roster_data)
    
    console.print(f"\n[green]Refreshed {limits}:[/green]")
    console.print(f"  Total keys: {len(openrouter_keys)}")
    console.print(f"  Matched to roster: {len(matched)}")
    
    # Check for mismatches between target and actual
    mismatches = []
    with open(limits, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['target_limit'] != row['actual_limit']:
                mismatches.append(f"  {row['name']}: limit target=${row['target_limit']} actual=${row['actual_limit']}")
            if row['target_disabled'] != row['actual_disabled']:
                mismatches.append(f"  {row['name']}: disabled target={row['target_disabled']} actual={row['actual_disabled']}")
    
    if mismatches:
        console.print("\n[yellow]⚠️  Mismatches between target and actual:[/yellow]")
        for mismatch in mismatches:
            console.print(mismatch)
        console.print("\n[cyan]Run 'update' to apply target values to OpenRouter[/cyan]")
    else:
        console.print("\n[green]✓ All targets match actuals[/green]")


@cli.command('export-keys')
@click.option('--db', default='keys.db')
@click.option('--output', default=None, help='Output filename (default: api_keys_TIMESTAMP.csv)')
@click.option('--format', type=click.Choice(['csv', 'json']), default='csv', help='Output format')
@click.pass_context
def export_keys(ctx, db, output, format):
    """
    **Export API keys** from database
    
    Retrieves stored API keys for distribution to students.
    
    ⚠️  **Security Warning**: Output contains secret API keys!
    """
    
    if not Path(db).exists():
        console.print("[red]Error: Database not found. Run 'init-db' first[/red]")
        sys.exit(1)
    
    # Load roster for email mapping
    roster_data = load_roster()
    
    # Query database for keys
    conn = sqlite3.connect(db)
    c = conn.cursor()
    
    c.execute("""
        SELECT k.key_hash, k.api_key, k.email, k.key_name, k.credit_limit, k.disabled
        FROM key k
        JOIN student s ON k.email = s.email
        ORDER BY k.email
    """)
    
    keys = c.fetchall()
    conn.close()
    
    if not keys:
        console.print("[yellow]No keys found in database[/yellow]")
        return
    
    # Prepare output filename
    if output is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output = f"api_keys_{timestamp}.{format}"
    
    # Export based on format
    if format == 'csv':
        with open(output, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['email', 'name', 'api_key', 'key_name', 'limit', 'disabled'])
            
            for key_hash, api_key, email, key_name, limit, disabled in keys:
                name = roster_data.get(email, '')
                writer.writerow([
                    email,
                    name,
                    api_key if api_key else f"[Key not stored - check OpenRouter]",
                    key_name,
                    limit if limit else 'unlimited',
                    'true' if disabled else 'false'
                ])
    
    else:  # json format
        keys_data = []
        for key_hash, api_key, email, key_name, limit, disabled in keys:
            keys_data.append({
                'email': email,
                'name': roster_data.get(email, ''),
                'api_key': api_key if api_key else "[Key not stored - check OpenRouter]",
                'key_name': key_name,
                'limit': limit if limit else 'unlimited',
                'disabled': disabled
            })
        
        with open(output, 'w') as f:
            json.dump(keys_data, f, indent=2)
    
    # Display summary with Rich panel
    console.print(Panel.fit(
        f"[bold green]Exported {len(keys)} keys to {output}[/bold green]\n\n"
        f"[yellow]⚠️  This file contains API keys - handle with care![/yellow]\n"
        f"[dim]Consider using secure distribution methods (encrypted email, secure file share)[/dim]",
        title="🔑 Keys Exported",
        border_style="green"
    ))
    
    # Show table of what was exported (without showing actual keys)
    table = Table(title="Exported Keys Summary", show_header=True)
    table.add_column("Email", style="cyan")
    table.add_column("Name")
    table.add_column("Key Name", style="dim")
    table.add_column("Has API Key?", justify="center")
    
    for key_hash, api_key, email, key_name, limit, disabled in keys:
        has_key = "✅" if api_key else "❌"
        table.add_row(
            email,
            roster_data.get(email, ''),
            key_name,
            has_key
        )
    
    console.print(table)


if __name__ == '__main__':
    cli()