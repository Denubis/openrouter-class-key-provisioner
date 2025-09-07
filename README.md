Here's a comprehensive README for your colleagues:

# OpenRouter API Key Management System

A command-line tool for managing OpenRouter API keys for classroom distribution, with full audit trail and batch operations.

## Overview

This system allows instructors to:
- Provision API keys for students with credit limits
- Track usage before/during/after class sessions
- Manage credit limits and key status
- Maintain complete audit trail of all changes
- Export keys for distribution

## Prerequisites

- Python 3.8+
- OpenRouter Provisioning API key (contact OpenRouter for provisioning access)
- Unix/Linux environment (or Windows with WSL)

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd provision

# Install dependencies using uv (recommended)
uv pip install rich-click requests

# Or using pip
pip install rich-click requests
```

## Setup

1. **Set your provisioning key**:
```bash
export OPENROUTER_PROVISIONING_KEY="your-provisioning-key-here"
# Add to .envrc or .bashrc for persistence
```

2. **Initialize the database**:
```bash
uv run manage_keys.py init-db
```

## Core Concepts

### Files

- **`roster.csv`** - Source of truth for student identities (email, name)
- **`limits.csv`** - Control plane for credit limits (target vs actual)
- **`keys.db`** - SQLite database with full history
- **`snapshot_*.csv`** - Timestamped exports after each operation
- **`api_keys_*.csv`** - Secret keys for distribution (handle with care!)

### Key Naming Convention

Keys are created with format: `YYYYMMDD_StudentName`
- Example: `20250907_Alice Smith`
- Allows grouping by class date
- Human-readable in OpenRouter dashboard

## Typical Workflow

### Before Class

1. **Create roster.csv**:
```csv
email,name
alice@university.edu,Alice Smith
bob@university.edu,Bob Jones
```

2. **Check existing keys**:
```bash
uv run manage_keys.py check
```

3. **Provision keys** ($2 default limit):
```bash
uv run manage_keys.py provision
# Or with custom limit
uv run manage_keys.py provision --limit 5.0
```

4. **Distribute keys** - Use the generated `api_keys_*.csv` file

### During Class

**Monitor usage**:
```bash
uv run manage_keys.py check
# Creates snapshot_YYYYMMDD_HHMMSS.csv
```

**Adjust limits if needed**:
1. Edit `limits.csv` - change `target_limit` column
2. Apply changes:
```bash
uv run manage_keys.py update
```

### After Class

**Final usage check**:
```bash
uv run manage_keys.py check
```

**Compare snapshots** to see usage during class:
- Before: `snapshot_20250907_090000.csv`
- After: `snapshot_20250907_120000.csv`

## Commands Reference

### `init-db`
Initialize the SQLite database
```bash
uv run manage_keys.py init-db
```

### `check`
View current state and reconcile with roster
- Fetches latest from OpenRouter
- Auto-adds orphaned keys to roster
- Exports timestamped snapshot
```bash
uv run manage_keys.py check
```

### `provision`
Create new API keys for students in roster
```bash
uv run manage_keys.py provision [--limit 2.0] [--dry-run]
```
- Skips students who already have keys
- Skips FIXME emails until corrected
- Outputs `api_keys_*.csv` with secret keys

### `update`
Apply limit changes from limits.csv
```bash
uv run manage_keys.py update [--dry-run]
```
- Reads target values from `limits.csv`
- Updates OpenRouter
- Refreshes `limits.csv` with actual values

### `refresh-limits-file`
Pull current state from OpenRouter
```bash
uv run manage_keys.py refresh-limits-file
```
- Updates actual values in `limits.csv`
- Preserves target values

### `export-keys`
Export API keys from database
```bash
uv run manage_keys.py export-keys [--format csv|json]
```
- Retrieves stored keys for redistribution
- **Security warning**: Contains secret keys!

## Managing Limits

The `limits.csv` file uses target vs actual pattern:

```csv
email,name,target_limit,actual_limit,target_disabled,actual_disabled,key_name,hash
alice@uni.edu,Alice,5.0,5.0,false,false,20250907_Alice,abc123...
bob@uni.edu,Bob,10.0,5.0,false,false,20250907_Bob,def456...
```

- Edit `target_limit` to desired value
- Run `update` to apply
- If `target_limit ≠ actual_limit`, update may have failed

## Handling Edge Cases

### Orphaned Keys
Keys existing in OpenRouter but not in roster are automatically added with FIXME emails:
```
string@FIXME.edu,string
```
Edit roster.csv to fix emails or delete unwanted entries.

### Failed Provisioning
System uses fail-fast approach:
- Stops on first error
- Preserves successfully created keys
- Re-run after fixing issue

### Network Issues
All commands fetch fresh state from OpenRouter first:
- Database is cache only
- OpenRouter is source of truth
- Safe to re-run commands

## Security Notes

1. **Provisioning Key**: Keep `OPENROUTER_PROVISIONING_KEY` secure
2. **API Keys File**: The `api_keys_*.csv` contains secrets
   - Email securely or use encrypted file share
   - Delete after distribution
3. **Database**: `keys.db` stores key hashes and labels (not full keys after initial creation)

## Troubleshooting

### "Database not found"
Run `uv run manage_keys.py init-db` first

### "OPENROUTER_PROVISIONING_KEY not set"
```bash
export OPENROUTER_PROVISIONING_KEY="your-key"
```

### Keys not being created
- Check for FIXME emails in roster (won't provision)
- Verify network connectivity
- Check OpenRouter API status

### Limits not updating
- Ensure email in limits.csv matches roster.csv exactly
- Check actual vs target columns for discrepancies
- Verify API call succeeded (no error messages)

## Database Schema

- **student** - Email, name, creation time
- **key** - Key hash, API key (on creation), limits, status
- **usage** - Time-series usage snapshots
- **changelog** - Audit trail of all changes

## Support

For OpenRouter API issues: https://openrouter.ai/docs
For provisioning key access: Contact OpenRouter support

## License

MIT license

---

*Built for managing classroom API key distribution with full audit capabilities.*