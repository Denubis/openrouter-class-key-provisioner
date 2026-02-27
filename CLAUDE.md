# OpenRouter Class Key Provisioner

Manages OpenRouter API keys for Macquarie University classroom distribution with full audit trail.

## Architecture

Single-file CLI tool (`manage_keys.py`) using rich-click, with SQLite for local state and OpenRouter API as source of truth.

### Data Flow

```
roster.csv → provision → OpenRouter API → keys.db + api_keys_TIMESTAMP.csv
                              ↑
                         source of truth
```

OpenRouter is always authoritative. Local DB and CSVs are derived state.

### Key Naming Convention

`YYYYMMDD_FirstName LastName_MQID` — MQ ID is the matching key between roster and OpenRouter keys.

### Roster Format

`first_name,last_name,email,mq_id,budget,limit_reset`

- `limit_reset`: `daily`, `weekly`, `monthly`, or empty
- `budget`: per-student dollar amount (USD)

### Database Schema (v2)

- `student`: email (PK), first_name, last_name, mq_id (UNIQUE), created_at
- `key`: key_hash (PK), key_label, email (FK), key_name, created_at, credit_limit, disabled
- `usage`: id (PK), key_hash (FK), usage, checked_at
- `changelog`: id (PK), key_hash (FK), action, old_value, new_value, changed_at
- `schema_version`: version

Student upsert uses `ON CONFLICT(email) DO UPDATE` (not `INSERT OR REPLACE`).

## Commands

```
init-db              Initialise database (one-time)
check                Fetch state from OpenRouter, reconcile with roster
provision            Create keys for students in roster without keys
  --dry-run          Preview without creating
  --limit N          Override per-student budget
update               Apply limit changes from limits.csv
refresh-limits-file  Refresh limits.csv from OpenRouter (preserves targets)
export-keys          Export keys from database for distribution
```

## Environment

- Python 3.12+, managed with uv
- `OPENROUTER_PROVISIONING_KEY` env var required (set in `.envrc` via direnv)

## Development

```bash
uv sync                          # Install deps
uv run pytest tests/ -v          # Run tests (51 tests)
uv run ruff check .              # Lint
uv run pre-commit run --all-files  # All hooks
```

### Pre-commit Hooks

ruff (lint + format), bandit (security), ty (type checker). Configured in `.pre-commit-config.yaml`.

### Ruff Config

Rules: E, F, W, I, UP, B, SIM, S. Line length 120. `demo/` excluded.

## Security

- `.envrc` is gitignored (contains provisioning key)
- `*.csv` and `keys.db` are gitignored (contain PII and API keys)
- `api_keys_TIMESTAMP.csv` files contain secret keys — handle with care
- This is a PUBLIC repo (`Denubis/openrouter-class-key-provisioner`)

## Freshness

Last verified: 2026-02-27
