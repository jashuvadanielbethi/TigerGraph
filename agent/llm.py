"""Minimal LLM abstraction used for reasoning, evidence synthesis, and
explanation generation -- the orchestrator does the tool selection and
control flow itself (policy_engine + orchestrator state machine), so the LLM
is never asked to decide permissions, only to reason over already-gathered
evidence and produce a fraud-type judgment + natural-language explanation.

Falls back to a deterministic heuristic responder when no API key is
configured, so `AGENT_MODE=mock` demos still produce sensible (if templated)
output without any external dependency.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agent.config import settings

logger = logging.getLogger("fraud_agent.llm")


class LLMClient:
    def __init__(self) -> None:
        self.backend = "none"
        if settings.anthropic_api_key:
            self.backend = "anthropic"
        elif settings.openai_api_key:
            self.backend = "openai"
        else:
            logger.warning("No LLM API key configured -- using heuristic fallback reasoner.")

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Ask the model for a JSON object; parse it; fall back to a
        heuristic if no backend is configured or parsing fails."""
        if self.backend == "anthropic":
            return self._anthropic_json(system, user)
        if self.backend == "openai":
            return self._openai_json(system, user)
        return self._heuristic_json(user)

    def complete_text(self, system: str, user: str) -> str:
        if self.backend == "anthropic":
            return self._anthropic_text(system, user)
        if self.backend == "openai":
            return self._openai_text(system, user)
        return self._heuristic_text(user)

    # -- backends ---------------------------------------------------------

    def _anthropic_text(self, system: str, user: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if hasattr(block, "text"))

    def _anthropic_json(self, system: str, user: str) -> dict[str, Any]:
        text = self._anthropic_text(system + "\nRespond with ONLY valid JSON, no prose.", user)
        return _safe_json(text)

    def _openai_text(self, system: str, user: str) -> str:
        import openai

        client = openai.OpenAI(api_key=settings.openai_api_key)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""

    def _openai_json(self, system: str, user: str) -> dict[str, Any]:
        text = self._openai_text(system + "\nRespond with ONLY valid JSON, no prose.", user)
        return _safe_json(text)

    # -- heuristic fallback (no API key) ----------------------------------

    def _heuristic_text(self, user: str) -> str:
        return (
            "[heuristic fallback -- no LLM configured] Evidence and policy context "
            "were reviewed programmatically; see structured findings in the case record."
        )

    def _heuristic_json(self, user: str) -> dict[str, Any]:
        # Deterministic stand-in used only so the pipeline is runnable
        # without API keys; the orchestrator's policy_engine + graph
        # findings still drive the real decision either way.
        return {
            "suspected_fraud_type": "undetermined (heuristic mode, no LLM configured)",
            "confidence": 0.5,
            "reasoning": "No LLM backend configured; relying on graph pattern findings and policy thresholds only.",
        }


def _safe_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        logger.error("Failed to parse LLM JSON output: %s", text[:500])
        return {"suspected_fraud_type": "unknown", "confidence": 0.3, "reasoning": text[:500]}


llm_client = LLMClient()
