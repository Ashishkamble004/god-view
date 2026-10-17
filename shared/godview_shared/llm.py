"""LlmClient wrapping google-genai Vertex AI with Argon->Flash fallback and token metering."""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from godview_shared.config import (
    DEFAULT_AGENT_FALLBACK_MODEL_ID,
    DEFAULT_AGENT_MODEL_ID,
    MODEL_TABLE,
    GodViewSettings,
)


@dataclass
class LlmResponse:
    """Standardized response from LlmClient.generate()."""

    text: str = ""
    parsed: Any | None = None
    function_calls: list[Any] = field(default_factory=list)
    model_id: str = ""
    location: str = "global"
    usage: dict[str, Any] = field(default_factory=dict)


def _deterministic_embedding(text: str, dim: int = 768) -> list[float]:
    """Produce a deterministic normalized 768-dim embedding vector for testing/hermetic mode."""
    # Hash repeatedly to seed 768 floats
    vec: list[float] = []
    seed = text.encode("utf-8")
    while len(vec) < dim:
        h = hashlib.sha256(seed).digest()
        for b in h:
            vec.append((float(b) - 128.0) / 128.0)
            if len(vec) == dim:
                break
        seed = h

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        vec = [x / norm for x in vec]
    return vec


class LlmClient:
    """Client for Vertex AI LLMs with fallback, structured schemas, and token metering."""

    def __init__(
        self,
        settings: GodViewSettings | None = None,
        auth: Any = None,
        token_governor: Any = None,
        project: str | None = None,
    ) -> None:
        self.settings = settings or GodViewSettings.from_env()
        self.auth = auth
        self.token_governor = token_governor
        self.project = (
            project
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or ""
        )
        self._clients: dict[str, Any] = {}

    def _get_genai_client(self, location: str) -> Any:
        if location not in self._clients:
            try:
                from google import genai

                self._clients[location] = genai.Client(
                    vertexai=True,
                    project=self.project,
                    location=location,
                )
            except Exception as exc:
                if self.settings.env_mode == "hermetic":
                    return None
                raise RuntimeError(
                    f"Failed to initialize google-genai Client for location '{location}': {exc}"
                ) from exc
        return self._clients[location]

    async def generate(
        self,
        role: str = "reasoning",
        contents: Any = "",
        tools: list[Any] | None = None,
        response_schema: Any | None = None,
        agent_id: str = "sovereign",
        system_instruction: str | None = None,
        **kwargs: Any,
    ) -> LlmResponse:
        """Generate content with model fallback and token metering."""
        cfg = MODEL_TABLE.get(role, MODEL_TABLE["reasoning"])

        # Determine primary model and location
        model_candidates: list[tuple[str, str]] = [(cfg.model_id, cfg.location)]
        for fb in cfg.fallbacks:
            model_candidates.append((fb.model_id, fb.location))

        if self.token_governor is not None:
            if not self.token_governor.can_escalate_to_argon(agent_id):
                # Put fallback model first if Argon cannot be used due to budget
                model_candidates = [
                    (DEFAULT_AGENT_FALLBACK_MODEL_ID, "global"),
                    (DEFAULT_AGENT_MODEL_ID, "global"),
                ]

        if self.settings.env_mode == "hermetic":
            parsed_obj = None
            if response_schema is not None and isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
                try:
                    parsed_obj = response_schema()
                except Exception:
                    # Provide default values for required fields if needed
                    parsed_obj = None

            return LlmResponse(
                text="Hermetic mock response for role " + role,
                parsed=parsed_obj,
                model_id=model_candidates[0][0],
                location=model_candidates[0][1],
                usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            )

        last_exc: Exception | None = None
        for model_id, location in model_candidates:
            try:
                client = self._get_genai_client(location)
                if client is None:
                    continue

                from google.genai import types

                config = types.GenerateContentConfig(
                    response_schema=response_schema,
                    tools=tools,
                    system_instruction=system_instruction,
                    **kwargs,
                )

                # Use asyncio to execute generate_content without blocking
                import asyncio

                res = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model_id,
                    contents=contents,
                    config=config,
                )

                prompt_tokens = 0
                comp_tokens = 0
                if hasattr(res, "usage_metadata") and res.usage_metadata:
                    prompt_tokens = getattr(res.usage_metadata, "prompt_token_count", 0) or 0
                    comp_tokens = getattr(res.usage_metadata, "candidates_token_count", 0) or 0

                total_tokens = prompt_tokens + comp_tokens

                # Meter tokens if token_governor is available
                if self.token_governor is not None and total_tokens > 0:
                    # Rough cost estimate
                    usd_cost = Decimal(str(total_tokens)) * Decimal("0.000002")
                    await self.token_governor.record_usage(
                        agent_id=agent_id,
                        model_id=model_id,
                        location=location,
                        tokens=total_tokens,
                        usd_cost=usd_cost,
                    )

                text_resp = getattr(res, "text", "") or ""
                parsed_resp = getattr(res, "parsed", None)
                func_calls = getattr(res, "function_calls", []) or []

                return LlmResponse(
                    text=text_resp,
                    parsed=parsed_resp,
                    function_calls=list(func_calls),
                    model_id=model_id,
                    location=location,
                    usage={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": comp_tokens,
                        "total_tokens": total_tokens,
                    },
                )
            except Exception as exc:
                last_exc = exc
                # Try next fallback model candidate on 429, 503, or rate limits
                continue

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No LLM model candidate succeeded")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Compute embeddings for a sequence of texts using text-embedding-005."""
        if not texts:
            return []

        if self.settings.env_mode == "hermetic":
            return [_deterministic_embedding(t) for t in texts]

        try:
            import asyncio

            client = self._get_genai_client("us-central1")
            res = await asyncio.to_thread(
                client.models.embed_content,
                model="text-embedding-005",
                contents=list(texts),
            )
            embeddings = []
            if hasattr(res, "embeddings") and res.embeddings:
                for emb in res.embeddings:
                    embeddings.append(list(emb.values))
                return embeddings
            if hasattr(res, "embedding") and res.embedding:
                return [list(res.embedding.values)]
            return [_deterministic_embedding(t) for t in texts]
        except Exception:
            if self.settings.env_mode == "local":
                return [_deterministic_embedding(t) for t in texts]
            raise
