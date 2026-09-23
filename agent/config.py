"""Central configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass
class Settings:
    # TigerGraph
    tg_host: str = os.getenv("TG_HOST", "")
    tg_username: str = os.getenv("TG_USERNAME", "tigergraph")
    tg_password: str = os.getenv("TG_PASSWORD", "")
    tg_graph_name: str = os.getenv("TG_GRAPH_NAME", "FraudInvestigation")
    tg_secret: str = os.getenv("TG_SECRET", "")

    # LLM
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5")

    # Agent behavior
    agent_mode: str = os.getenv("AGENT_MODE", "mock")  # "live" or "mock"
    risk_investigate_threshold: float = field(default_factory=lambda: _float("RISK_SCORE_INVESTIGATE_THRESHOLD", 0.5))
    risk_auto_block_threshold: float = field(default_factory=lambda: _float("RISK_SCORE_AUTO_BLOCK_THRESHOLD", 0.9))
    max_evidence_rounds: int = field(default_factory=lambda: _int("MAX_EVIDENCE_ROUNDS", 3))

    # HHGOA dataset raw files
    txn_csv: str = os.getenv("TXN_CSV", "data/raw/transactions.csv")
    identity_csv: str = os.getenv("IDENTITY_CSV", "data/raw/identity.csv")
    closed_cases_csv: str = os.getenv("CLOSED_CASES_CSV", "data/raw/closed_cases_history.csv")
    case_pack_csv: str = os.getenv("CASE_PACK_CSV", "data/raw/case_pack.csv")

    @property
    def has_tigergraph(self) -> bool:
        return bool(self.tg_host and self.tg_password)

    @property
    def has_llm(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key)


settings = Settings()
