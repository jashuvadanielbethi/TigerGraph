"""Fraud Policy v1.0 (HHGOA), encoded exactly -- action names, approval
routes, and the BLOCK_CARD exposure threshold are pulled straight from
docs/fraud_policy.md so an LLM is never the one deciding whether an action
needs a human. Rule application (R1-R10) lives in agent/investigate.py,
which is where the case-specific evidence lives; this module only answers
"is this action name valid" and "what route does it take."
"""
from __future__ import annotations

ACTIONS = {
    "ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS",
    "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS",
    "GENERATE_REPORT", "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD",
}

AUTO_ACTIONS = {
    "ALLOW_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER",
    "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "GENERATE_REPORT", "CREATE_CASE",
    "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD",
}

BLOCK_CARD_L1_MAX_EXPOSURE = 2500.0


def route_for(action: str, exposure_usd: float = 0.0) -> str:
    """Policy Section 2: approval routing."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}' -- not in the policy's action list")
    if action in AUTO_ACTIONS:
        return "auto"
    if action == "DECLINE_TRANSACTION":
        return "L1"
    if action == "BLOCK_CARD":
        return "L1" if exposure_usd <= BLOCK_CARD_L1_MAX_EXPOSURE else "L2"
    if action == "BLOCK_ALL_CARDS":
        return "L2"
    if action == "FILE_REPORT":
        return "L2"
    raise ValueError(f"No route defined for action '{action}'")  # pragma: no cover


def action_item(action: str, exposure_usd: float, reason: str) -> dict:
    return {"action": action, "route": route_for(action, exposure_usd), "reason": reason}


def sar_required(confirmed_or_strong_suspicion: bool, exposure_usd: float,
                  connects_to_shared_origin: bool, is_coordinated_or_undocumented: bool) -> bool:
    """Policy 3a: file a SAR when fraud is confirmed/strongly suspected AND
    at least one of (exposure > $1,000, shared device/region/other-customer
    connection, coordinated/undocumented pattern) holds."""
    if not confirmed_or_strong_suspicion:
        return False
    return exposure_usd > 1000 or connects_to_shared_origin or is_coordinated_or_undocumented
