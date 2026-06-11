# CQR Backend — Secure Runtime for Coding Agents

CQR is a sandboxed cloud execution environment for coding agents. It provides an isolated container where AI agents can write code, run terminals, manage secrets, and deploy applications autonomously.

The core differentiator is the Knowledge Graph (KG) + Latent Space Mapping (LSM) layer, which reduces token costs by serving structural subgraphs instead of raw file contents.

## Architecture

The backend is structured as a monorepo with 7 independent packages, composed by the Orchestration layer:

1. **`kg-engine`**: Ingests codebases into a queryable Kuzu graph.
2. **`lsm-layer`**: Provides pgvector embeddings and proximity scoring for context assembly.
3. **`execution-env`**: Manages isolated Docker containers and file operations.
4. **`vault`**: Encrypts and stores project secrets (agents see keys, containers get values).
5. **`agent-bridge`**: Assembles KG/LSM context and dispatches to Claude/Codex.
6. **`security-scanner`**: Traverses the KG to detect vulnerability patterns.
7. **`orchestration`**: The public REST/WebSocket gateway that routes tasks.

## Local Development

1. Clone the repository.
2. Copy `.env.example` to `.env` and fill in your API keys (Anthropic/OpenAI).
3. Ensure Docker is installed and running.
4. Run `docker-compose up -d` to start the infrastructure (Postgres, Redis) and all backend services.

## Testing

Each package contains its own test suite. Run tests from the root directory:

```bash
pytest packages/*/tests/
```

## Security Model

- **Containers**: Outbound network only, no inter-container communication. Commands are sanitised against a deny list (e.g., `rm -rf /`).
- **Secrets**: The master key is stored in the OS keychain. Agents never see secret values in their context, only key names. Values are injected at container startup via a privileged script.
- **Scanning**: The Security Scanner runs automatically after agent edits to detect hardcoded secrets, SQL injection, path traversal, and command injection via AST path analysis.
