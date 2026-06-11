# CQR Backend Design Decisions & Clarifications

This document tracks ambiguities encountered during the implementation of the CQR backend (PDR v1.0) and the decisions made to resolve them. Items marked **RESOLVED** have been implemented; items marked **OPEN** require future action.

---

## 1. CORS Configuration (Orchestration API)

**Status:** OPEN  
**Context:** The Orchestration API requires CORS middleware to accept requests from the frontend, but the frontend origin is not yet known.  
**Decision:** `allow_origins=["*"]` is temporarily configured in `packages/orchestration/src/main.py`.  
**Action Required:** Tighten to the specific Stitch frontend origin before production deployment.

---

## 2. In-Memory Stores vs. Postgres (RESOLVED — cp-1-corrections)

**Status:** RESOLVED  
**Context:** The PDR states that Orchestration should route tasks and store state persistently, but the initial MVP used in-memory Python dicts (`_projects`, `_tasks`).  
**Decision (cp-1-corrections):** Replaced all in-memory state with Postgres-backed persistence:
- `packages/orchestration/src/db.py` — async SQLAlchemy 2.x engine with `asyncpg` driver.
- `packages/orchestration/alembic/` — Alembic migration scaffold with `0001_initial.py` creating `projects` and `tasks` tables.
- `packages/orchestration/src/router.py` — all CRUD calls now go through `db.py`.
- `DATABASE_URL` is read from environment; `alembic upgrade head` must be run on first deploy.

---

## 3. Tree-sitter Language Support (RESOLVED — cp-1-corrections)

**Status:** RESOLVED  
**Context:** The PDR mandates support for Python, JS/TS, and Go parsing. The initial MVP used a Python regex fallback for JS/TS/Go.  
**Decision (cp-1-corrections):** Full Tree-sitter parsers implemented for all four language groups:
- `ingestion/js_parser.py` — JS/JSX using `tree-sitter-javascript`
- `ingestion/ts_parser.py` — TS/TSX using `tree-sitter-typescript` (separate grammars for `.ts` and `.tsx`)
- `ingestion/go_parser.py` — Go using `tree-sitter-go`
- `ingestion/parser.py` — `parse_file()` now routes to the correct parser by extension. Regex fallback is Python-only.
- All three parsers extract: Functions, Classes, Imports, EnvRefs.
- Go structs and interfaces are mapped to Class nodes (Go has no class keyword).
- Go EnvRefs detect `os.Getenv()` and `os.LookupEnv()` calls.

---

## 4. Kuzu Edge Idempotency (RESOLVED — cp-1-corrections)

**Status:** RESOLVED  
**Context:** Kuzu does not support `MERGE` on relationship tables in all versions. The initial implementation used `CREATE` with exception swallowing, which silently created duplicate edges on re-ingestion.  
**Decision (cp-1-corrections):** Implemented a two-step check-then-create strategy in `graph/crud.py`:
1. **In-process cache** (`_edge_cache: set[tuple]`) — fast path, avoids DB round-trip for edges already created in the current ingestion run.
2. **DB existence check** — `MATCH (a)-[r:TYPE]->(b) RETURN count(r)` before every `CREATE` — handles re-ingestion of previously-ingested projects.
3. `clear_edge_cache()` is called at the start of every `ingest_project()` run to reset the cache.

---

## 5. Token Savings Calculation (RESOLVED — cp-1-corrections)

**Status:** RESOLVED  
**Context:** The `TokenUsage` model requires a `savings_vs_raw` percentage. The initial implementation hardcoded `0.0`.  
**Decision (cp-1-corrections):**
- `raw_token_count` is now stored on every `File` node in the KG (schema updated, `upsert_file_node` computes it as `len(content) // 4`).
- `context_assembler.py` fetches all File nodes via `GET /kg/nodes`, sums `raw_token_count`, and computes:
  ```
  savings_vs_raw = round((1 - context_tokens / raw_total_tokens) * 100, 1)
  ```
- The value is passed through `assemble_context()` → `dispatch_claude()` / `dispatch_codex()` → `TokenUsage.savings_vs_raw`.
- Logged at INFO level as `{"event": "context_assembled", ..., "savings_vs_raw": X.X}`.

---

## 6. User-Supplied API Keys (RESOLVED — cp-1-corrections)

**Status:** RESOLVED  
**Context:** The initial implementation read `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` from environment variables, meaning the platform would hold user credentials.  
**Decision (cp-1-corrections):** CQR does not hold LLM API keys. The flow is:
1. User supplies `api_key` and `api_key_type` in the `POST /api/tasks` request body.
2. Orchestration forwards them in the `DispatchPayload` to Agent Bridge.
3. Agent Bridge passes the key directly to `dispatch_claude()` or `dispatch_codex()`.
4. Each dispatcher instantiates the SDK client with the key as a local variable — it is never stored, never logged, and never forwarded to KG/LSM/Vault.
5. `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` have been removed from `.env.example`.
- `api_key` is required at dispatch time; a 422 is returned if absent.

---

## 7. Security Scanner Architecture (RESOLVED — feature/security-scanner)

**Status:** RESOLVED  
**Context:** The initial implementation used regex pattern matching on node property strings. The PDR §10 specifies graph path traversal as the differentiator.  
**Decision:** Replaced with a three-layer graph analysis engine:
- `traversal.py` — BFS taint-flow engine walking `CALLS` edges from source nodes (EnvRef, user-input) to sink nodes (sql_execute, subprocess, file_write, log), pruning validated paths.
- `patterns.py` — classifies traversal results into the 7 named PDR §10.1 patterns.
- `scanner.py` — orchestrates KG fetch → traversal → classification pipeline.
- 28/28 unit tests passing.
