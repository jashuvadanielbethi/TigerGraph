"""Per-case investigation pipeline: takes one case_pack.csv row, runs it
through graph evidence gathering (agent/data_index.py + agent/patterns.py),
scores fraud probability with a transparent, evidence-cited heuristic (no
black-box model -- every contribution is traceable to a concrete graph
fact), applies Fraud Policy v1.0 (agent/policy_engine.py) including the
evidence-gathering loop (Section 5) and stopping rule (Section 6), and
emits one answer dict matching the dataset's exact Answer Format.

Design note on "graph vs. LLM": fraud_probability and pattern
classification here are computed deterministically from graph queries, not
by asking an LLM to eyeball the evidence. This keeps the numbers auditable
and reproducible, which matters more for a fraud/compliance deliverable
than marginal gains from LLM judgment -- and it means the pipeline runs
identically whether or not an LLM API key is configured.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from agent import patterns
from agent.data_index import DataIndex
from agent.graphrag import policy_retriever
from agent.policy_engine import action_item, sar_required

CUSTOMER_CONFIRM_BELOW = 0.50  # simulated response leans "confirmed" if pre-request probability is below this
FRAUD_STOP_HI = 0.85
FRAUD_STOP_LO = 0.15
FRAUD_VERDICT_HI = 0.65
FRAUD_VERDICT_LO = 0.25


def _sid(txn_id: Any) -> str:
    return str(int(txn_id))


def _clip(p: float) -> float:
    return max(0.02, min(0.97, p))


class Ctx:
    """Mutable scratch state for one investigation, threaded through the
    scoring helpers below so they can each add evidence/probability without
    a giant argument list."""

    def __init__(self, case_row: pd.Series, idx: DataIndex) -> None:
        self.case_row = case_row
        self.idx = idx
        self.tool_calls = 0
        self.evidence: list[dict] = []
        self.probability = 0.15  # base rate: a case was opened, so it's worth a look
        self.pattern: str = "none"
        self.pattern_description = ""
        self.affected_txn_ids: set[int] = set()
        self.connected_card_ids: set[str] = set()
        self.connected_device_profiles: set[str] = set()
        self.independent_evidence_types: set[str] = set()
        self.card_testing_match = False
        self.testing_big_cleared = False
        self.ring_detected = False
        self.ring_strong = False
        self.ring_kind = ""

    def add_evidence(self, claim: str, source: str, ref: str, entity_ids: list[str]) -> None:
        self.evidence.append({"claim": claim, "source": source, "ref": ref, "entity_ids": entity_ids})

    def bump(self, delta: float, evidence_type: str) -> None:
        self.probability += delta
        if delta != 0:
            self.independent_evidence_types.add(evidence_type)


def investigate(case_row: pd.Series, idx: DataIndex, agent_case_memory: list[dict]) -> dict:
    start = time.time()
    ctx = Ctx(case_row, idx)

    flagged_txn_id = int(case_row["flagged_txn_id"])
    card_id = case_row["card_id"]
    customer_id = case_row["customer_id"]
    trigger_type = case_row["trigger_type"]
    trigger_text = case_row["trigger_text"]

    flagged = idx.get_transaction(flagged_txn_id)
    ctx.tool_calls += 1
    if flagged is None:
        raise ValueError(f"flagged_txn_id {flagged_txn_id} not found in transactions index")

    flagged_identity = idx.get_identity(flagged_txn_id) if flagged["channel"] == "online" else None
    ctx.tool_calls += 1
    device_profile = flagged_identity["device_profile"] if flagged_identity is not None else None

    card_hist = idx.card_window(card_id)
    ctx.tool_calls += 1
    ctx.add_evidence(
        claim=f"Card {card_id} has {len(card_hist)} known transactions on file "
              f"({card_hist['channel'].value_counts().to_dict()}).",
        source="graph", ref=f"query:card_window(card_id={card_id})",
        entity_ids=[_sid(t) for t in card_hist.index[:20]],
    )

    _score_trigger(ctx, trigger_type, trigger_text, case_row.get("risk_score"), flagged)
    _score_card_testing(ctx, card_hist, flagged)
    _score_cnp_anomaly(ctx, card_hist, flagged)
    _score_new_device(ctx, flagged_identity, flagged_txn_id)
    _score_out_of_region(ctx, card_hist, flagged)
    _score_shared_device_ring(ctx, idx, device_profile, card_id, flagged)
    # Region-cluster ring detection (many *other* cards billing in the same
    # addr1) is deliberately NOT scored: a populous billing region produces
    # dozens of unrelated legitimate co-occurrences, and without a per-card
    # "is this ALSO a new region for them" check (expensive at this scale)
    # it's a systematic false-positive generator, not a signal. See
    # agent/patterns.py:region_cluster_ring and docs/architecture.md for the
    # limitation and what a real fix looks like.
    _score_similar_cases(ctx, idx, customer_id, card_id, device_profile, flagged, agent_case_memory)
    _score_policy_context(ctx)

    # Round once, here, and use this rounded value everywhere downstream
    # (verdict thresholds, display, stop_reason) -- otherwise a probability
    # that *displays* as 0.65 but is actually 0.6499 can silently fall on
    # the wrong side of a >= 0.65 threshold, which is confusing to audit.
    ctx.probability = round(_clip(ctx.probability), 2)

    initial_actions, initial_reco_notes = _decide_initial_actions(ctx, exposure_estimate=_current_exposure(ctx))

    evidence_requests: list[dict] = []
    pre_request_probability = ctx.probability
    final_actions = initial_actions
    what_changed = "nothing"

    needs_verification = (
        trigger_type in ("risk_score", "analyst_request")
        and not ctx.card_testing_match
        and not ctx.ring_strong
        and not (ctx.probability >= FRAUD_STOP_HI or ctx.probability <= FRAUD_STOP_LO)
    )
    if needs_verification:
        assumed_confirms = pre_request_probability < CUSTOMER_CONFIRM_BELOW
        response = (
            f"Customer confirms they made this ${flagged['TransactionAmt']:.2f} transaction."
            if assumed_confirms else
            f"Customer states they did not make this ${flagged['TransactionAmt']:.2f} transaction "
            f"and still has the card in their possession."
        )
        evidence_requests.append({
            "type": "customer_validation",
            "asked_after_step": 1,
            "assumed_response": response,
        })
        ctx.add_evidence(
            claim=f"Evidence request (customer_validation): {response}",
            source="customer", ref="evidence_request:1", entity_ids=[_sid(flagged_txn_id)],
        )
        if assumed_confirms:
            ctx.probability = round(_clip(ctx.probability - 0.45), 2)
            what_changed = ("Simulated customer confirmation (assumed because pre-request probability "
                             f"was {pre_request_probability:.2f}, below the midpoint) settled the case as "
                             "legitimate per R3.")
        else:
            ctx.probability = round(_clip(ctx.probability + 0.35), 2)
            what_changed = ("Simulated customer denial (assumed because pre-request probability was "
                             f"{pre_request_probability:.2f}, at/above the midpoint) confirmed the block "
                             "per R2.")
        final_actions = _decide_final_actions(ctx, exposure_estimate=_current_exposure(ctx),
                                               customer_confirmed=assumed_confirms,
                                               customer_denied=not assumed_confirms)

    elif trigger_type == "customer_report" and ctx.probability < FRAUD_STOP_HI and _current_exposure(ctx) > 500:
        evidence_requests.append({
            "type": "analyst_info",
            "asked_after_step": 1,
            "assumed_response": ("Analyst confirms no additional connected-card activity beyond what the "
                                  "graph already shows; scope of the episode is as investigated."),
        })
        ctx.add_evidence(
            claim="Evidence request (analyst_info): analyst confirms investigated scope is complete.",
            source="graph", ref="evidence_request:1", entity_ids=[],
        )
        what_changed = "nothing (analyst confirmation did not change the graph-derived findings)"

    verdict = _verdict_from_probability(ctx.probability)
    pattern, pattern_description = _finalize_pattern(ctx, verdict)

    if verdict == "legitimate":
        affected = []
        exposure = 0.0
        first_suspicious = ""
    else:
        affected = sorted(ctx.affected_txn_ids | {flagged_txn_id})
        exposure = float(sum(abs(idx.get_transaction(t)["TransactionAmt"]) for t in affected))
        first_suspicious = str(min(affected, key=lambda t: idx.get_transaction(t)["ts"]))

    connects_to_shared_origin = bool(ctx.connected_card_ids)
    is_confirmed_or_strong = verdict == "fraud" and ctx.probability >= 0.70
    file_sar = sar_required(is_confirmed_or_strong, exposure, connects_to_shared_origin,
                             pattern == "undocumented")

    final_actions = _reconcile_file_report(final_actions, file_sar, exposure)

    status = _status_from(verdict, final_actions)
    stop_reason = _stop_reason(ctx, verdict, evidence_requests)

    sar = _build_sar(file_sar, ctx, flagged, card_id, customer_id, affected, exposure, pattern,
                      ctx.connected_card_ids, device_profile)

    graph_case_id = f"CASE-{case_row['case_id']}"
    case_obj = {
        "status": status,
        "verdict": verdict,
        "fraud_probability": round(ctx.probability, 2),
        "pattern": pattern,
        "pattern_description": pattern_description,
        "affected_txn_ids": [str(t) for t in affected],
        "first_suspicious_txn_id": first_suspicious,
        "connected_card_ids": sorted(ctx.connected_card_ids),
        "connected_device_profiles": sorted(ctx.connected_device_profiles),
        "exposure_usd": round(exposure, 2),
        "evidence": ctx.evidence,
        "similar_prior_cases": ctx.similar_case_ids if hasattr(ctx, "similar_case_ids") else [],
        "summary": _build_summary(case_row, ctx, verdict, pattern, exposure),
        "written_to_graph": True,
        "graph_case_id": graph_case_id,
    }

    agent_case_memory.append({
        "case_id": graph_case_id,
        "customer_id": customer_id,
        "card_id": card_id,
        "device_profile": device_profile,
        "addr1": float(flagged["addr1"]) if pd.notna(flagged["addr1"]) else None,
        "pattern": pattern,
        "verdict": verdict,
    })

    answer = {
        "case_id": case_row["case_id"],
        "case": case_obj,
        "evidence_requests": evidence_requests,
        "next_best_actions": {
            "initial": initial_actions,
            "final": final_actions,
            "what_changed": what_changed,
        },
        "sar": sar,
        "stop_reason": stop_reason,
        "tool_calls": ctx.tool_calls,
        "tokens": 0,
        "latency_s": round(time.time() - start, 3),
    }
    return answer


# ---------------------------------------------------------------------------
# Scoring helpers -- each adds evidence + a probability contribution to ctx
# ---------------------------------------------------------------------------

def _score_trigger(ctx: Ctx, trigger_type: str, trigger_text: str, risk_score, flagged: pd.Series) -> None:
    if trigger_type == "customer_report":
        # A direct customer denial is dispositive under Policy R2 ("Customer
        # denies the transaction. Recommend BLOCK_CARD and CREATE_CASE") --
        # unconditionally, not gated on independent corroboration. Sized so
        # a bare, uncorroborated denial already clears the fraud verdict
        # threshold (0.15 base + 0.50 = 0.65), matching that the action
        # taken (BLOCK_CARD) is not contingent on further graph evidence;
        # graph evidence still moves probability further in either direction.
        ctx.bump(0.50, "customer_report")
        ctx.add_evidence(claim=f"Customer report: {trigger_text}", source="customer",
                          ref="case_pack.trigger_text", entity_ids=[_sid(flagged.name)])
    elif trigger_type == "risk_score" and pd.notna(risk_score):
        nudge = (float(risk_score) - 0.5) * 0.10
        ctx.bump(nudge, "risk_score")
        ctx.add_evidence(
            claim=f"Bank detection model scored this transaction at {float(risk_score):.2f}. "
                  f"Treated as a weak prior only, per policy Section 0: a risk score is an input, never a verdict.",
            source="graph", ref="case_pack.risk_score", entity_ids=[_sid(flagged.name)])
    elif trigger_type == "analyst_request":
        ctx.bump(0.10, "analyst_request")
        ctx.add_evidence(claim=f"Analyst request: {trigger_text}", source="external",
                          ref="case_pack.trigger_text", entity_ids=[_sid(flagged.name)])


def _score_card_testing(ctx: Ctx, card_hist: pd.DataFrame, flagged: pd.Series) -> None:
    ctx.tool_calls += 1
    sequences = patterns.find_card_testing_sequences(card_hist)
    flagged_id = int(flagged.name)
    for seq in sequences:
        involved = set(seq["small_txn_ids"]) | set(seq["big_txn_ids"])
        if flagged_id in involved:
            ctx.card_testing_match = True
            ctx.testing_big_cleared = len(seq["big_txn_ids"]) > 0
            ctx.affected_txn_ids |= involved
            ctx.bump(0.40, "card_testing")
            ctx.add_evidence(
                claim=(f"{len(seq['small_txn_ids'])} online authorizations under $5 between "
                       f"{seq['window_start']} and {seq['window_end']}, "
                       f"{'followed by a purchase over $100 that already cleared' if seq['big_txn_ids'] else 'with no larger follow-up purchase yet'}."),
                source="graph", ref="query:card_testing_sequence", entity_ids=[_sid(t) for t in involved],
            )
            break


def _score_cnp_anomaly(ctx: Ctx, card_hist: pd.DataFrame, flagged: pd.Series) -> None:
    result = patterns.cnp_anomaly(flagged, card_hist)
    if result is None:
        return
    ctx.cnp_result = result
    if result["reasons"]:
        delta = min(0.10 * len(result["reasons"]), 0.20)
        ctx.bump(delta, "cnp_anomaly")
        ctx.affected_txn_ids.add(int(flagged.name))
        ctx.add_evidence(
            claim="Online transaction inconsistent with this card's history: " + "; ".join(result["reasons"]),
            source="graph", ref="query:card_window(product/amount history)",
            entity_ids=[_sid(flagged.name)],
        )
    if len(result["burst_txn_ids"]) >= 2:
        ctx.bump(0.10, "burst")
        ctx.affected_txn_ids |= set(result["burst_txn_ids"])
        ctx.add_evidence(
            claim=f"{len(result['burst_txn_ids'])} online transactions on this card within a 48-hour window "
                  f"around the flagged transaction.",
            source="graph", ref="query:card_window(48h burst)",
            entity_ids=[_sid(t) for t in result["burst_txn_ids"]],
        )


def _score_new_device(ctx: Ctx, flagged_identity: Optional[pd.Series], flagged_txn_id: int) -> None:
    if flagged_identity is None:
        return
    sig = patterns.new_device_signal(flagged_identity)
    if sig is None:
        return
    ctx.new_device_result = sig
    if sig["is_new"]:
        ctx.bump(0.10, "new_device")
        ctx.add_evidence(
            claim="Identity record marks the device as New for this account"
                  + (f", behind a {sig['proxy']} proxy" if sig["hidden_proxy"] else ""),
            source="graph", ref="query:identity(id_15,id_23)", entity_ids=[_sid(flagged_txn_id)],
        )
        if sig["hidden_proxy"]:
            ctx.bump(0.08, "hidden_proxy")


def _score_out_of_region(ctx: Ctx, card_hist: pd.DataFrame, flagged: pd.Series) -> None:
    sig = patterns.out_of_region_signal(flagged, card_hist)
    if sig is None:
        return
    ctx.region_result = sig
    if sig["parallel_home_activity_txn_ids"]:
        ctx.bump(0.30, "out_of_region_parallel")
        ctx.affected_txn_ids |= set(sig["new_region_txn_ids"])
        ctx.add_evidence(
            claim=(f"Card-present purchase billed in region {sig['flagged_region']}, which this card has no "
                   f"prior history in (home region {sig['home_region']}), while {len(sig['parallel_home_activity_txn_ids'])} "
                   f"transaction(s) continued in the home region within +/-2 days -- consistent with card cloning, "
                   f"not travel."),
            source="graph", ref="query:card_window(region history)",
            entity_ids=[_sid(t) for t in sig["parallel_home_activity_txn_ids"] + sig["new_region_txn_ids"]],
        )
    elif sig["new_region_span_days"] <= 1:
        ctx.bump(0.15, "out_of_region_single")
        ctx.affected_txn_ids |= set(sig["new_region_txn_ids"])
        ctx.add_evidence(
            claim=(f"Card-present purchase billed in region {sig['flagged_region']}, no prior history in that "
                   f"region (home region {sig['home_region']}), and no multi-day pattern suggesting travel."),
            source="graph", ref="query:card_window(region history)",
            entity_ids=[_sid(t) for t in sig["new_region_txn_ids"]],
        )
    else:
        ctx.bump(0.03, "out_of_region_trip")
        ctx.add_evidence(
            claim=(f"Card-present purchase billed in a new region ({sig['flagged_region']}), but activity there "
                   f"spans {sig['new_region_span_days']:.1f} days -- consistent with a trip, not cloning."),
            source="graph", ref="query:card_window(region history)", entity_ids=[],
        )


def _score_shared_device_ring(ctx: Ctx, idx: DataIndex, device_profile: Optional[str], card_id: str,
                               flagged: pd.Series) -> None:
    if not device_profile:
        return
    ctx.tool_calls += 1
    ring = patterns.shared_device_ring(idx, device_profile, card_id, flagged["ts"])
    if ring is None or not ring["verified_connected_cards"]:
        return
    n = len(ring["verified_connected_cards"])
    ctx.ring_detected = True
    ctx.ring_kind = "device profile"
    ctx.connected_card_ids |= set(ring["verified_connected_cards"])
    ctx.connected_device_profiles.add(device_profile)
    delta = min(0.15 + 0.05 * (n - 1), 0.30)
    ctx.bump(delta, "shared_device")
    ctx.add_evidence(
        claim=(f"Device profile '{device_profile}' also appears on {n} other known card(s): "
               f"{', '.join(ring['verified_connected_cards'])}."),
        source="graph", ref=f"query:device_neighbors(device_id={device_profile})",
        entity_ids=ring["sample_txn_ids"],
    )
    confirmed_link = [c for c in ring["verified_connected_cards"]
                       if ((idx.closed["card_id"] == c) & (idx.closed["outcome"] == "confirmed_fraud")).any()]
    if confirmed_link:
        ctx.ring_strong = True
        ctx.bump(0.15, "shared_device_confirmed_link")
        ctx.add_evidence(
            claim=f"Card(s) {', '.join(confirmed_link)} sharing this device were confirmed fraud in a closed case.",
            source="graph", ref="query:similar_prior_cases", entity_ids=[],
        )
    if n >= 3:
        ctx.ring_strong = True


def _score_similar_cases(ctx: Ctx, idx: DataIndex, customer_id: str, card_id: str,
                          device_profile: Optional[str], flagged: pd.Series,
                          agent_case_memory: list[dict]) -> None:
    ctx.tool_calls += 1
    addr1 = flagged["addr1"] if pd.notna(flagged["addr1"]) else None
    closed_matches = idx.similar_prior_cases(customer_id, card_id, device_profile, addr1, top_k=5)
    case_ids = []
    confirmed_budget, cleared_budget = 0.15, 0.15  # capped total contribution per direction --
    # a customer's own history of past incidents is corroborating context,
    # not something that should be allowed to dominate THIS alert's score
    # regardless of how many prior cases they happen to have.
    for row in closed_matches.itertuples():
        case_ids.append(row.case_id)
        same_card_or_device = row.card_id == card_id or (device_profile and row.device_profile == device_profile)
        if row.outcome == "confirmed_fraud" and confirmed_budget > 0:
            delta = min(0.08 if same_card_or_device else 0.03, confirmed_budget)
            confirmed_budget -= delta
            ctx.bump(delta, "similar_confirmed_case")
            ctx.add_evidence(
                claim=f"Similar closed case {row.case_id} ({row.pattern}) on this customer/card/device/region "
                      f"was confirmed fraud: {row.analyst_notes[:200]}",
                source="graph", ref="query:similar_prior_cases", entity_ids=[],
            )
        elif row.outcome == "cleared" and row.customer_id == customer_id and cleared_budget > 0:
            delta = min(0.08, cleared_budget)
            cleared_budget -= delta
            ctx.bump(-delta, "similar_cleared_case")
            ctx.add_evidence(
                claim=f"Similar closed case {row.case_id} on this same customer was cleared as legitimate: "
                      f"{row.analyst_notes[:200]}",
                source="graph", ref="query:similar_prior_cases", entity_ids=[],
            )

    for mem in agent_case_memory:
        if mem["customer_id"] == customer_id or mem["card_id"] == card_id or \
           (device_profile and mem["device_profile"] == device_profile):
            case_ids.append(mem["case_id"])
            ctx.add_evidence(
                claim=f"This run's own case {mem['case_id']} (pattern={mem['pattern']}, verdict={mem['verdict']}) "
                      f"involves the same customer/card/device -- retrieved from this session's case memory.",
                source="graph", ref="query:similar_prior_cases(agent case memory)", entity_ids=[],
            )

    ctx.similar_case_ids = case_ids[:5]


def _score_policy_context(ctx: Ctx) -> None:
    query = ctx.pattern if ctx.pattern != "none" else "fraud investigation risk score card"
    if ctx.card_testing_match:
        query = "card testing small authorizations"
    elif ctx.ring_detected:
        query = "shared device region ring connected cards"
    hits = policy_retriever.retrieve(query, top_k=1)
    for h in hits:
        ctx.add_evidence(claim=f"Relevant policy: {h.text[:280]}", source="document", ref=h.doc_id, entity_ids=[])


# ---------------------------------------------------------------------------
# Decisioning
# ---------------------------------------------------------------------------

def _dedupe_actions(actions: list[dict]) -> list[dict]:
    """Preserves order, keeps the first occurrence's reason when the same
    action was recommended by more than one rule (e.g. both R2 and R6 can
    independently recommend CREATE_CASE for the same case)."""
    seen: set[str] = set()
    out = []
    for a in actions:
        if a["action"] in seen:
            continue
        seen.add(a["action"])
        out.append(a)
    return out


def _current_exposure(ctx: Ctx) -> float:
    if not ctx.affected_txn_ids:
        return float(ctx.idx.get_transaction(int(ctx.case_row["flagged_txn_id"]))["TransactionAmt"])
    return float(sum(abs(ctx.idx.get_transaction(t)["TransactionAmt"]) for t in ctx.affected_txn_ids))


def _decide_initial_actions(ctx: Ctx, exposure_estimate: float) -> tuple[list[dict], str]:
    actions: list[dict] = []
    trigger_type = ctx.case_row["trigger_type"]

    if ctx.card_testing_match:
        actions.append(action_item("DECLINE_TRANSACTION", exposure_estimate,
                                    "R5: card-testing sequence detected on this card"))
        if ctx.testing_big_cleared:
            actions.append(action_item("BLOCK_CARD", exposure_estimate,
                                        "R5: a purchase over $100 already cleared after the testing sequence"))
        else:
            actions.append(action_item("STEP_UP_AUTH", exposure_estimate,
                                        "R5: require step-up authentication before further activity"))

    if ctx.ring_detected:
        actions.append(action_item("CREATE_CASE", exposure_estimate,
                                    f"R6: shared {ctx.ring_kind} links this card to other known cards"))
        if ctx.ring_strong:
            actions.append(action_item("FILE_REPORT", exposure_estimate,
                                        f"R6: shared {ctx.ring_kind} connects to confirmed fraud or a cluster of 3+ cards"))
        actions.append(action_item("MONITOR_CONNECTED_CARDS", exposure_estimate,
                                    f"R6: monitor every card sharing this {ctx.ring_kind}"))

    if trigger_type == "customer_report":
        actions.append(action_item("BLOCK_CARD", exposure_estimate, "R2: customer denies the transaction"))
        actions.append(action_item("CREATE_CASE", exposure_estimate, "R2: customer denial opens a case"))
        if exposure_estimate > 1000 or ctx.connected_card_ids:
            actions.append(action_item("FILE_REPORT", exposure_estimate,
                                        "R2: exposure exceeds $1,000 or connects to a shared device/region"))

    if not actions:
        if ctx.probability >= 0.70:
            actions.append(action_item("DECLINE_TRANSACTION", exposure_estimate,
                                        f"R1: probability {ctx.probability:.2f} clears 0.70, decline pending review"))
            actions.append(action_item("STEP_UP_AUTH", exposure_estimate,
                                        "Confirm legitimacy before restoring full account activity"))
        elif ctx.probability <= 0.15:
            actions.append(action_item("ALLOW_TRANSACTION", exposure_estimate,
                                        f"Probability {ctx.probability:.2f}: no corroborating evidence beyond the trigger"))
        else:
            actions.append(action_item("VERIFY_WITH_CUSTOMER", exposure_estimate,
                                        f"R1: probability {ctx.probability:.2f} rests on a single signal, "
                                        f"verify before any block"))

    if ctx.probability > FRAUD_STOP_LO and ctx.probability < FRAUD_STOP_HI and exposure_estimate > 500 \
            and not (ctx.card_testing_match or ctx.ring_strong or trigger_type == "customer_report"):
        actions.append(action_item("ESCALATE_TO_ANALYST", exposure_estimate,
                                    f"R8: uncertain verdict (probability {ctx.probability:.2f}) with exposure "
                                    f"${exposure_estimate:.2f} over $500"))

    return _dedupe_actions(actions), ""


def _decide_final_actions(ctx: Ctx, exposure_estimate: float, customer_confirmed: bool,
                           customer_denied: bool) -> list[dict]:
    actions: list[dict] = []
    if customer_confirmed:
        actions.append(action_item("CLOSE_NO_FRAUD", exposure_estimate,
                                    "R3: customer confirmed the transaction"))
        return actions

    if customer_denied:
        actions.append(action_item("BLOCK_CARD", exposure_estimate, "R2: customer denies the transaction"))
        actions.append(action_item("CREATE_CASE", exposure_estimate, "R2: customer denial opens a case"))
        if exposure_estimate > 1000 or ctx.connected_card_ids:
            actions.append(action_item("FILE_REPORT", exposure_estimate,
                                        "R2: exposure exceeds $1,000 or connects to a shared device/region"))
        if ctx.connected_card_ids:
            actions.append(action_item("MONITOR_CONNECTED_CARDS", exposure_estimate,
                                        "R6: monitor cards sharing the same origin as this confirmed case"))
    return actions


def _reconcile_file_report(actions: list[dict], file_sar: bool, exposure: float) -> list[dict]:
    has_report = any(a["action"] == "FILE_REPORT" for a in actions)
    if file_sar and not has_report:
        actions.append(action_item("FILE_REPORT", exposure, "Policy 3a: SAR trigger met"))
    if not file_sar and has_report:
        actions = [a for a in actions if a["action"] != "FILE_REPORT"]
    return actions


def _verdict_from_probability(p: float) -> str:
    if p >= FRAUD_VERDICT_HI:
        return "fraud"
    if p <= FRAUD_VERDICT_LO:
        return "legitimate"
    return "uncertain"


def _finalize_pattern(ctx: Ctx, verdict: str) -> tuple[str, str]:
    if verdict == "legitimate":
        return "none", ""
    if ctx.card_testing_match:
        return "card_testing", ""
    cnp = getattr(ctx, "cnp_result", None)
    new_dev = getattr(ctx, "new_device_result", None)
    region = getattr(ctx, "region_result", None)
    if cnp and cnp["reasons"]:
        if new_dev and new_dev["is_new"]:
            return "card_not_present_new_device", ""
        return "card_not_present_fraud", ""
    if region and (region["parallel_home_activity_txn_ids"] or region["new_region_span_days"] <= 1):
        return "out_of_region_use", ""
    if ctx.ring_detected and len(ctx.connected_card_ids) >= 1:
        distinct_customers = len({c.split("-K")[0] for c in ctx.connected_card_ids} | {ctx.case_row["customer_id"]})
        if distinct_customers >= 2:
            desc = (f"Multiple distinct customers' cards ({distinct_customers}) show activity from the same "
                    f"shared {ctx.ring_kind or 'origin'} in a tight window, with no single card explaining the "
                    f"whole pattern -- consistent with a fraud ring or a card-cracking service operating across "
                    f"unrelated accounts rather than any one compromised card or cardholder. Found via graph "
                    f"traversal from the flagged transaction's device/region to sibling cards.")
            return "undocumented", desc
        return "account_takeover", ""
    if verdict == "uncertain":
        # Genuinely not confident enough to assert any typology.
        return "none", ""
    # verdict == "fraud" but none of the five named patterns' *specific*
    # signatures (testing sequence, novel product/amount, region history,
    # multi-customer ring) were independently confirmed in the graph --
    # e.g. a bare customer denial with no other technical detail. Policy R9
    # says not to force activity into a named category it doesn't match, so
    # "undocumented" is reserved for the multi-entity coordinated-abuse case
    # above; here the honest answer is the broadest fitting category (any
    # online-channel unauthorized use is card_not_present_fraud by the
    # pattern's own definition) or, for in-person activity with no further
    # signal, leaving pattern unclassified rather than guessing.
    flagged = ctx.idx.get_transaction(int(ctx.case_row["flagged_txn_id"]))
    if flagged["channel"] == "online":
        return "card_not_present_fraud", ""
    return "none", ""


def _status_from(verdict: str, final_actions: list[dict]) -> str:
    if any(a["action"] == "ESCALATE_TO_ANALYST" for a in final_actions):
        return "escalated"
    if verdict == "fraud":
        return "closed_fraud"
    if verdict == "legitimate":
        return "closed_legitimate"
    return "open"


def _stop_reason(ctx: Ctx, verdict: str, evidence_requests: list[dict]) -> str:
    n_types = len(ctx.independent_evidence_types)
    if ctx.probability >= FRAUD_STOP_HI:
        return (f"Fraud probability {ctx.probability:.2f} is at or above 0.85, supported by {n_types} "
                f"independent evidence type(s) ({sorted(ctx.independent_evidence_types)}); further "
                f"investigation is unlikely to change the recommended action.")
    if ctx.probability <= FRAUD_STOP_LO:
        return (f"Fraud probability {ctx.probability:.2f} is at or below 0.15, supported by {n_types} "
                f"independent evidence type(s); further investigation is unlikely to change the recommendation.")
    if evidence_requests:
        return "Simulated verification response settled the question (Policy Section 6)."
    if ctx.card_testing_match or ctx.ring_strong:
        return "Evidence-driven rule (R5/R6) already determines the action; further steps would not change it."
    return ("Probability remains in the uncertain band after graph evidence and available verification; "
            "escalating to a human analyst per R8 rather than continuing indefinitely (Policy Section 6).")


def _build_summary(case_row: pd.Series, ctx: Ctx, verdict: str, pattern: str, exposure: float) -> str:
    parts = [
        f"Case {case_row['case_id']} ({case_row['trigger_type']}) on card {case_row['card_id']} "
        f"(customer {case_row['customer_id']}): verdict {verdict}, fraud probability {ctx.probability:.2f}."
    ]
    if verdict != "legitimate":
        parts.append(f"Pattern: {pattern}. Exposure: ${exposure:.2f}.")
    if ctx.connected_card_ids:
        parts.append(f"Connected to {len(ctx.connected_card_ids)} other card(s) via shared "
                      f"{ctx.ring_kind or 'origin'}: {', '.join(sorted(ctx.connected_card_ids))}.")
    top_claims = [e["claim"] for e in ctx.evidence if e["source"] == "graph"][:2]
    if top_claims:
        parts.append("Key evidence: " + " ".join(top_claims))
    return " ".join(parts)


def _build_sar(file_sar: bool, ctx: Ctx, flagged: pd.Series, card_id: str, customer_id: str,
               affected: list[int], exposure: float, pattern: str, connected_card_ids: set[str],
               device_profile: Optional[str]) -> dict:
    if not file_sar:
        return {"file": False, "reason": _sar_no_file_reason(ctx, exposure), "narrative": "", "subjects": [],
                "total_amount_usd": 0, "activity_dates": []}

    idx = ctx.idx
    dates = sorted(idx.get_transaction(t)["ts"] for t in affected)
    first_date, last_date = dates[0].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")
    subjects = [customer_id, card_id] + sorted(connected_card_ids)

    device_clause = f" from a device profile ('{device_profile}') also seen on {len(connected_card_ids)} other card(s)" \
        if device_profile and connected_card_ids else ""
    txn_desc = "; ".join(
        f"${abs(idx.get_transaction(t)['TransactionAmt']):.2f} on {idx.get_transaction(t)['ts'].strftime('%Y-%m-%d %H:%M')} "
        f"({idx.get_transaction(t)['channel']})"
        for t in affected[:6]
    )

    narrative = (
        f"Between {first_date} and {last_date}, card {card_id} belonging to customer {customer_id} was used for "
        f"{len(affected)} transaction(s) totaling ${exposure:.2f}, identified as {pattern.replace('_', ' ')} "
        f"activity{device_clause}. Transactions: {txn_desc}. "
        f"The activity was flagged by a {ctx.case_row['trigger_type'].replace('_', ' ')} trigger and corroborated "
        f"by graph analysis of this card's transaction history"
        + (f", and by {len(connected_card_ids)} other card(s) sharing the same device/region origin"
           if connected_card_ids else "") + ". "
        f"This filing is required under Policy Section 3a because "
        + ("exposure exceeds $1,000" if exposure > 1000 else
           "the activity connects to a shared device/region cluster or another customer's confirmed fraud"
           if connected_card_ids else "the pattern is coordinated or undocumented abuse (Rule R9)") + ". "
        f"Card {card_id} has been recommended for blocking and reissue; any connected cards have been placed "
        f"under monitoring pending further review."
    )

    return {
        "file": True,
        "reason": f"Policy 3a: fraud confirmed/strongly suspected and " +
                  ("exposure exceeds $1,000" if exposure > 1000 else
                   "activity connects to a shared device/region cluster" if connected_card_ids else
                   "pattern is coordinated/undocumented (R9)"),
        "narrative": narrative,
        "subjects": subjects,
        "total_amount_usd": round(exposure, 2),
        "activity_dates": [first_date, last_date],
    }


def _sar_no_file_reason(ctx: Ctx, exposure: float) -> str:
    if ctx.probability < 0.70:
        return "Policy 3a: fraud not confirmed or strongly suspected (probability below 0.70); no SAR trigger met."
    if exposure <= 1000 and not ctx.connected_card_ids:
        return (f"Policy 3a: fraud probability {ctx.probability:.2f} but exposure ${exposure:.2f} is at or below "
                f"$1,000 and the activity does not connect to a shared device/region cluster or another "
                f"customer's fraud -- no SAR trigger met.")
    return "Policy 3a: SAR trigger not met."
