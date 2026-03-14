# Flyway Schema Extractor

Parses Flyway SQL migration files and reconstructs the current database schema by replaying all migrations in version order. Emits a single `SchemaDoc` JSON representing the schema as of the most recent migration.

## What it extracts

By processing `V*.sql` migration files in version order:

- **Tables**: all current tables with column names, data types, nullability, defaults, primary keys
- **Indexes**: all `CREATE INDEX` and `CREATE UNIQUE INDEX` statements applied
- **Foreign keys**: all `ADD CONSTRAINT ... FOREIGN KEY ... REFERENCES` constraints
- **Views**: view names (content not parsed in detail)
- **Migration history**: which migration version each table was first created in

## Why this approach (not querying the live DB)

1. **Offline-capable**: the extraction runs on the build machine with access to the source code, not necessarily the live database
2. **Historical**: we can reconstruct the schema at any past migration version if needed
3. **No DB credentials required**: the extractor reads SQL files, not a live database
4. **Consistent with source of truth**: Flyway migrations are the authoritative schema definition; the live database is derived from them

## Architecture

```
extractors/flyway-schema/
├── extractor.py          # Main extractor: reads and replays migrations
├── sql_parser.py         # Wraps sqlglot for DDL statement parsing
├── schema_state.py       # In-memory schema state machine
└── tests/
    └── test_extractor.py
```

Uses `sqlglot` for SQL parsing. `sqlglot` is a pure Python SQL parser with strong PostgreSQL support. It handles the full DDL grammar including `CREATE TABLE`, `ALTER TABLE`, and complex constraint definitions.

## Configuration

```yaml
flyway:
  migrations_dir: /path/to/flyway/migrations
  # If migrations are distributed across service repos, specify multiple:
  # migrations_dirs:
  #   - /services/shared-db/migrations
  #   - /services/identity-svc/migrations
```

## Migration File Format Support

| Pattern | Example | Handled |
|---|---|---|
| Versioned | `V1__initial_schema.sql` | ✅ |
| Versioned with sub-version | `V1_1__add_indexes.sql` | ✅ |
| Repeatable | `R__add_views.sql` | ✅ (applied last) |
| Baseline | `B__baseline.sql` | ⚠️ (recorded, not applied) |
| Undo | `U1__rollback.sql` | ⚠️ (recorded, not applied) |

## Output

Single file written to `extracted/schema.json`.

```json
{
  "tables": [
    {
      "name": "biometric_records",
      "columns": [
        {"name": "id", "data_type": "UUID", "nullable": false, "default": null, "is_primary_key": true},
        {"name": "subject_id", "data_type": "UUID", "nullable": false, "default": null, "is_primary_key": false},
        {"name": "modality", "data_type": "TEXT", "nullable": false, "default": null, "is_primary_key": false},
        {"name": "enrolled_at", "data_type": "TIMESTAMPTZ", "nullable": false, "default": "NOW()", "is_primary_key": false}
      ],
      "indexes": [
        "idx_biometric_records_subject_id ON (subject_id)",
        "idx_biometric_records_modality ON (modality)"
      ],
      "foreign_keys": [
        "subject_id REFERENCES subjects(id) ON DELETE CASCADE"
      ],
      "migration_version_created": "V003"
    }
  ],
  "views": ["enrollment_summary_view"],
  "as_of_migration": "V047"
}
```

## Running

```bash
python scripts/run_extractors.py --config config/config.yaml --extractor flyway-schema
```

## Known Limitations

- Does not handle procedural SQL (PL/pgSQL functions, stored procedures) — function bodies are recorded but not analyzed for table access
- Does not resolve `CREATE TABLE ... LIKE other_table` patterns (uncommon in Flyway migrations)
- Column type aliases (`INT` → `INTEGER`, `SERIAL`) are kept as written, not normalized
- Does not verify that the extracted schema matches the live database (use `SELECT * FROM information_schema.columns` to spot-check)
