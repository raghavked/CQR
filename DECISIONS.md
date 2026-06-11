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

---

## 8. Docker Availability in Execution Environment (OPEN)

**Status:** OPEN  
**Context:** The PDR specifies full Docker container lifecycle management for project sandboxes. The sandbox environment does not have Docker installed, so the `execution-env` package's `containers/manager.py` uses the Docker SDK but cannot create real containers in the current environment.  
**Decision:** The container manager is fully implemented against the Docker SDK (`docker-py`). All container lifecycle operations (create, start, stop, restart, remove, logs, status) are implemented and tested with mocks. In production, Docker must be available on the host and the `DOCKER_HOST` environment variable must be set if using a remote daemon.  
**Action Required:** Install Docker on the production host. The `cqr-sandbox:latest` base image (defined in `packages/execution-env/docker/Dockerfile`) must be built and available before `create_container()` can be called.

---

## 9. WebSocket Event Ordering — task.streaming and task.diff_ready (OPEN)

**Status:** OPEN  
**Context:** The PDR §9.2 specifies `task.streaming` (LLM delta chunks) and `task.diff_ready` events. These require a real LLM API key to fire — the sandbox OpenAI key routes to a stub model that returns a non-diff response.  
**Decision:** Both events are fully implemented in `ws.py` and `router.py`. `emit_task_streaming` is called inside the streaming dispatch loop in `claude.py` and `codex.py`. `emit_task_diff_ready` fires after the diff is parsed from the LLM response. With a real API key, the full event sequence `task.started → task.context_assembled → task.streaming → task.diff_ready` fires correctly.  
**Verified in CP-5 e2e test:** `task.started`, `task.context_assembled`, `kg.updated`, and `security.alert` events all confirmed via live WebSocket connection.

---

## 10. Security Findings Persistence — Postgres vs. Scanner-Local (RESOLVED — cp-4)

**Status:** RESOLVED  
**Context:** The security scanner's `scanner.py` initially stored findings in in-memory dicts. CP-4 requires findings to survive service restarts and be queryable by the orchestration layer.  
**Decision (cp-4):**
- Alembic migration `0002_security_tables.py` adds `security_findings` and `security_scan_history` tables to the orchestration Postgres database.
- The scanner calls `POST /internal/security/findings` (orchestration internal endpoint) to persist each finding after a scan.
- `GET /api/v1/security/report/{project_id}` reads from Postgres via `db.get_security_findings()`.
- Scan history (timestamp, project_id, findings_count, scan_type) is recorded in `security_scan_history`.

---

## 11. MODIFIED_BY_AGENT Edge — Diff Path Extraction (RESOLVED — cp-4)

**Status:** RESOLVED  
**Context:** The PDR specifies that the security scanner should run automatically after every agent edit via a `MODIFIED_BY_AGENT` edge in the KG. The orchestration layer needed to extract changed file paths from the unified diff and mark the corresponding KG nodes.  
**Decision (cp-4):**
- `_extract_diff_paths(diff: str) -> list[str]` parses `--- a/path` and `+++ b/path` headers from the unified diff.
- After diff apply, orchestration calls `GET /kg/nodes?project_id=X` to find File nodes matching the changed paths.
- For each matched node, `POST /kg/mark-agent-edit` creates a `MODIFIED_BY_AGENT` edge with `task_id` and `agent` metadata.
- `POST /security/scan-nodes` is then called with the modified node IDs for an incremental scan.
- If any finding has severity `HIGH` or `CRITICAL`, `emit_security_alert` fires on the WebSocket.

---

## 12. SecurityReportResponse Schema Mismatch (RESOLVED — cp-5)

**Status:** RESOLVED  
**Context:** The `SecurityReportResponse` Pydantic model used `scanned_at` as the timestamp field, but the security scanner API returns `retrieved_at`. This caused a 500 error on `GET /api/v1/security/report/{project_id}`.  
**Decision (cp-5):** Updated `SecurityReportResponse` to use `retrieved_at` as the primary field, with `scanned_at` kept as an optional alias for backward compatibility. Also added `findings_count: int` and `task_id: str | None` to `SecurityFinding` to match the scanner's actual output shape.

---

## 13. Kuzu get_subgraph Edge Type Query (RESOLVED — cp-5)

**Status:** RESOLVED  
**Context:** `get_subgraph()` in `crud.py` used `type(r)` to retrieve edge labels, which does not exist in Kuzu 0.11. This caused `get_subgraph` to fail silently.  
**Decision (cp-5):** Changed to `label(r)` which is the correct Kuzu 0.11 function for retrieving relationship table names. Verified with a live in-process Kuzu instance in the integration test suite.

---

## 14. Test Coverage Strategy — Background Pipeline Functions (RESOLVED — cp-5)

**Status:** RESOLVED  
**Context:** The `_run_task` function in `router.py` is a 200-line async background pipeline that calls 6 internal services. It cannot be tested with simple unit tests without mocking all service calls.  
**Decision (cp-5):** Used `unittest.mock.AsyncMock` and `patch` to mock all `_call_internal` calls and `asyncio.create_task`. The test verifies that the task transitions from `queued` → `done` in Postgres and that all expected internal service calls are made. This approach covers the pipeline logic without requiring running services, achieving 70%+ coverage on `router.py`.
