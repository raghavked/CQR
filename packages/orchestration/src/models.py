"""
Shared Pydantic models for CQR inter-package communication.
All packages import from this module to ensure consistent schemas.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


class Project(BaseModel):
    """Represents a CQR project with its container and status."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    repo_path: str  # Local path in execution container
    container_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: Literal["provisioning", "ready", "error", "stopped"] = "provisioning"


class TokenUsage(BaseModel):
    """Token consumption breakdown for a single LLM dispatch."""

    context_tokens: int = 0
    response_tokens: int = 0
    total_tokens: int = 0
    savings_vs_raw: float = 0.0  # Percentage saved vs. sending raw files
    prompt_tokens: int | None = None   # alias for context_tokens (OpenAI naming)
    completion_tokens: int | None = None  # alias for response_tokens
    context_node_count: int | None = None  # number of KG nodes in context window


class Task(BaseModel):
    """Represents an agent task submitted to the orchestration layer."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    description: str
    agent: Literal["claude", "codex", "cqr-native"] = "claude"
    budget_tier: Literal["micro", "standard", "extended"] = "standard"
    status: Literal["queued", "running", "done", "failed", "rejected", "applied"] = "queued"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    token_usage: TokenUsage | None = None
    diff: str | None = None  # Unified diff produced by agent
    confidence: float | None = None  # 0.0–1.0


class KGNode(BaseModel):
    """A single node in the Knowledge Graph."""

    id: str
    type: Literal["File", "Function", "Class", "Import", "Variable", "EnvRef"]
    properties: dict[str, Any]
    proximity_score: float | None = None


class KGEdge(BaseModel):
    """A directed edge in the Knowledge Graph."""

    from_id: str
    to_id: str
    edge_type: Literal[
        "CONTAINS",
        "CALLS",
        "IMPORTS",
        "INHERITS",
        "REFERENCES_ENV",
        "MODIFIED_BY_AGENT",
    ]
    properties: dict[str, Any] = Field(default_factory=dict)


class KGSubgraph(BaseModel):
    """A subgraph response from the KG engine."""

    nodes: list[KGNode]
    edges: list[KGEdge]
    token_estimate: int  # Estimated tokens if serialised for LLM context


class SecurityFinding(BaseModel):
    """A vulnerability finding produced by the Security Scanner."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    pattern: str
    severity: Literal["low", "medium", "high", "critical"]
    node_path: list[str]  # KG node IDs forming the vulnerability path
    description: str
    suggested_fix: str | None = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    resolved: bool = False
    task_id: str | None = None  # Task that triggered this finding (if via MODIFIED_BY_AGENT)


# ---------------------------------------------------------------------------
# Request / response models for the Orchestration REST API
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    """Request body for POST /projects."""

    name: str
    repo_path: str


class SubmitTaskRequest(BaseModel):
    """Request body for POST /tasks."""

    project_id: str
    description: str
    agent: Literal["claude", "codex", "cqr-native"] = "claude"
    budget_tier: Literal["micro", "standard", "extended"] = "standard"
    # User-supplied API key — CQR never stores platform keys at rest.
    # The key is forwarded to the Agent Bridge at call time only and
    # destroyed after the dispatch completes. Never logged.
    api_key: str | None = None
    api_key_type: Literal["anthropic", "openai"] | None = None


class TaskStatusResponse(BaseModel):
    """Response for GET /tasks/{task_id}."""

    task: Task


class ProjectResponse(BaseModel):
    """Response for project endpoints."""

    project: Project


class SecurityReportResponse(BaseModel):
    """Response for GET /security/report/{project_id}."""

    project_id: str
    findings: list[SecurityFinding]
    findings_count: int = 0
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)
    # scanned_at is an alias kept for backward compatibility
    scanned_at: datetime | None = None


# ---------------------------------------------------------------------------
# Agent Bridge models
# ---------------------------------------------------------------------------


class DispatchPayload(BaseModel):
    """Payload sent to the LLM dispatcher."""

    task_id: str
    project_id: str
    task_description: str
    agent: Literal["claude", "codex", "cqr-native"] = "claude"
    budget_tier: Literal["micro", "standard", "extended"] = "standard"
    kg_context: KGSubgraph | None = None
    vault_keys: list[str] = Field(default_factory=list)
    # User-supplied API key — forwarded from SubmitTaskRequest.
    # Never stored, never logged, never passed to KG/LSM/Vault.
    api_key: str | None = None
    api_key_type: Literal["anthropic", "openai"] | None = None


class AgentResponse(BaseModel):
    """Structured response from the LLM dispatcher."""

    task_id: str
    diff: str  # Unified diff
    explanation: str
    confidence: float  # 0.0–1.0
    token_usage: TokenUsage | None = None
    flagged: bool = False  # True if response attempted to access restricted paths
    flag_reason: str | None = None


# ---------------------------------------------------------------------------
# WebSocket event models
# ---------------------------------------------------------------------------


class WSEvent(BaseModel):
    """Base WebSocket event envelope."""

    event: str
    project_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: dict[str, Any] = Field(default_factory=dict)
