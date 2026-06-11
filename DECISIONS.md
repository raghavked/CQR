# CQR Backend Design Decisions & Clarifications

This document tracks ambiguities encountered during the implementation of the CQR backend phase (PDR v1.0) and the decisions made to satisfy the checkpoint requirements.

## 1. CORS Configuration (Orchestration API)
**Context:** The Orchestration API requires CORS middleware to accept requests from the frontend, but the frontend origin is not yet known.
**Decision:** `allow_origins=["*"]` is temporarily configured in `packages/orchestration/src/main.py`.
**Action Required:** Tighten this to the specific Stitch frontend origin before production deployment.

## 2. In-Memory Stores vs. Database
**Context:** The PDR states that Orchestration should route tasks and store state, and Vault should store secrets, but it doesn't specify the persistent database for these components (only pgvector for LSM and Kuzu for KG are explicitly mandated).
**Decision:** The MVP implementation uses in-memory Python dictionaries for Orchestration state (`_projects`, `_tasks`) and Vault secrets (as a fallback if `keyring` is unavailable).
**Action Required:** Replace these with persistent Postgres-backed stores in CP-3.

## 3. Tree-sitter Language Support
**Context:** The PDR mandates support for Python, JS/TS, and Go parsing.
**Decision:** Tree-sitter is used for Python parsing. A fallback regex parser is implemented for JS/TS/Go to satisfy the MVP requirement without requiring complex multi-language grammar compilation in the local environment.
**Action Required:** Add full Tree-sitter grammars for JS/TS/Go in future iterations.

## 4. Kuzu Edge Creation (MERGE vs CREATE)
**Context:** Upserting edges idempotently in Kuzu can be syntax-dependent depending on the version.
**Decision:** The implementation uses a simple `CREATE` with exception handling to catch duplicates, rather than relying on `MERGE` for relationship tables.
**Action Required:** Review Kuzu Cypher documentation for the specific deployed version to optimise idempotent edge creation.

## 5. Token Savings Calculation
**Context:** The `TokenUsage` model requires a `savings_vs_raw` percentage.
**Decision:** Currently hardcoded to `0.0` as the exact raw file sizes are not passed to the LLM dispatcher.
**Action Required:** Implement a calculation comparing the `token_estimate` of the KG subgraph against the total token count of the raw files.
