# 0033: The business DB generator lives in the backend, opened read-only in three layers

**Status:** accepted (2026-11-15)

## Context

PLAN_HYBRID.md says the business database "ships as `scripts/generar_bd.py`", run by
`python -m app.cli seed-business-db`. The script was written before this repo existed,
as a standalone file that writes `hilda_luna.db` next to itself.

## Decision

- **The generator is a backend module, `app/agent/business_seed.py`, not a script in
  `scripts/`.** The CLI has to import it, and the Docker image copies only `backend/`,
  so a file in `scripts/` would be missing from the image. There's one copy, and
  `seed-business-db` is the only way to run it.
- **Same data, byte for byte.** The module keeps the script's tables, values and seed
  (2026). It uses its own `random.Random(SEED)` instead of the global generator, which
  draws the same sequence. Checked on 2026-11-15: all 2,921 `INSERT` rows of the
  database it builds are identical to the original script's. Only the whitespace of the
  `CREATE TABLE` text changed. The schema and data stay in Spanish, like the company. The
  tools will translate for the model (Phase 2).
- **Small fixes on the way in.** The retail customers' city and state were pasted into
  the SQL text; they're now parameters (same values). The file is written to a
  `.tmp` next to the target and moved over it, so a reader never sees half a database.
- **Location:** `BUSINESS_DB_PATH`, empty by default, which means
  `DATA_DIR/business/hilda_luna.db`. It is separate from `app.db`, and the agent never
  writes to it.
- **Read-only in three layers** (`app/agent/business_db.py`): the file is opened with
  `mode=ro`; `PRAGMA query_only = ON`; and an SQLite authorizer allows only SELECT,
  reading columns, functions and recursive CTEs. The authorizer denies writes, DDL,
  PRAGMA, ATTACH and transactions when the statement is prepared. Each layer would stop a
  write on its own, so a bug in the `sql_select` text guard (Phase 2) still can't change
  the data.
- **No pre-built `.db` in the repo.** The database is derived data and takes a fraction
  of a second to build. The query and tool notes the script came with are kept as
  [docs/business-db/CONSULTAS_Y_TOOLS.md](../business-db/CONSULTAS_Y_TOOLS.md).

## Consequences

- `seed-business-db` replaces the file each time, which is safe because the data is
  always the same.
- The app doesn't seed the database by itself yet. The agent tools (Phase 2 on) report a
  missing database with the command that creates it. Seeding it together with
  **Load the demo company** can come with the agent UI (Phase 6).
- Tests assert the meeting-notes figures (CH-2004: 4,120 units in Q3, and so on), the
  row counts, and that every kind of write raises and leaves the file unchanged.
