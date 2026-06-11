"""
Code embedding pipeline for the LSM layer.
Uses OpenAI text-embedding-3-small to embed code nodes into vector space.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
_EMBEDDING_DIM = 1536  # text-embedding-3-small output dimension


def get_embedding(text: str) -> list[float]:
    """
    Embed a text snippet using the configured OpenAI embedding model.
    Returns a list of floats representing the embedding vector.
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE"),
    )
    text = text.replace("\n", " ").strip()
    if not text:
        return [0.0] * _EMBEDDING_DIM

    try:
        response = client.embeddings.create(input=[text], model=_EMBEDDING_MODEL)
        return response.data[0].embedding
    except Exception as exc:  # noqa: BLE001
        logger.error("Embedding failed: %s", exc)
        return [0.0] * _EMBEDDING_DIM


def embedding_dim() -> int:
    """Return the dimensionality of the embedding model output."""
    return _EMBEDDING_DIM
