# Schema Migration Framework v3 Design

## Current State (v2)
- `SCHEMA_VERSION = 2`
- Hardcoded migration functions in `Repository._apply_migration()`
- `schema_version` table tracks current version
- Migrations: v1 (notifications), v2 (host_rate_limits)

## Proposed v3 Architecture

### 1. Migration Files
```
src/web_watcher/migrations/
├── __init__.py
├── 0001_init_notifications.py
├── 0002_init_host_rate_limits.py
└── 0003_... .py
```

### 2. Migration Contract
```python
class Migration:
    version: int
    description: str
    up(connection): None
    down(connection): None  # Optional
    checksum: str  # SHA-256 of migration SQL/code
```

### 3. Schema Migrations Table
```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT,
    applied_at TEXT,
    checksum TEXT,
    duration_ms INTEGER
)
```

### 4. Application Flow
1. Scan `migrations/` directory for migration files
2. Sort by version
3. For each unapplied migration:
   - Verify checksum
   - Execute `up()`
   - Record in `schema_migrations`
4. Support `down()` for rollback (optional)

### 5. Benefits
- Declarative migrations
- Checksum verification prevents tampering
- Rollback support
- Clear migration history
- Easier testing and review

### 6. Migration Path
- v2 → v3: Introduce migration file format, migrate existing hardcoded migrations
- Backward compatible: v3 can read v2 `schema_version` table

## Implementation Plan
1. Create `migrations/` directory structure
2. Implement `Migration` base class
3. Implement `MigrationRunner` with checksum verification
4. Migrate existing v1/v2 migrations to file format
5. Update `Repository` to use `MigrationRunner`
6. Add tests for migration discovery, checksum, rollback

## Status
Design complete. Implementation pending.
