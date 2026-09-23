import pytest

from agent.policy_engine import (
    BLOCK_CARD_L1_MAX_EXPOSURE,
    route_for,
    sar_required,
)


def test_auto_actions_route_auto():
    for action in ("ALLOW_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER",
                   "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "GENERATE_REPORT", "CREATE_CASE",
                   "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"):
        assert route_for(action) == "auto"


def test_decline_transaction_is_l1():
    assert route_for("DECLINE_TRANSACTION") == "L1"


def test_block_card_route_depends_on_exposure():
    assert route_for("BLOCK_CARD", exposure_usd=BLOCK_CARD_L1_MAX_EXPOSURE) == "L1"
    assert route_for("BLOCK_CARD", exposure_usd=BLOCK_CARD_L1_MAX_EXPOSURE + 0.01) == "L2"


def test_block_all_cards_and_file_report_are_always_l2():
    assert route_for("BLOCK_ALL_CARDS", exposure_usd=1) == "L2"
    assert route_for("FILE_REPORT", exposure_usd=1) == "L2"


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        route_for("DO_SOMETHING_ELSE")


def test_sar_required_needs_confirmed_or_strong_suspicion():
    assert sar_required(False, 5000, True, True) is False


def test_sar_required_any_trigger_condition_suffices():
    assert sar_required(True, 1500, False, False) is True   # exposure > $1,000
    assert sar_required(True, 10, True, False) is True       # shared origin
    assert sar_required(True, 10, False, True) is True       # coordinated/undocumented
    assert sar_required(True, 10, False, False) is False     # none of the three triggers
