import numpy as np
import pytest

from costuel_analysis import SimConfig, simulate
from costuel_analysis.causal import (
    _recent,
    build_slots,
    naive_effect,
    notification_outcomes,
    proximal_effect,
    randomized_effect,
)


def test_recent_counts_strictly_previous_window():
    counts = np.array([[0, 1, 0, 0, 2, 0, 0, 0]])
    # window of 3 slots: slot s sees events in [s-3, s-1]
    assert _recent(counts, 3).tolist() == [[0, 0, 1, 1, 1, 2, 2, 2]]


def test_notification_outcomes_on_a_toy_day():
    import pandas as pd

    day = pd.Timestamp("2026-03-02")
    at = lambda h, m=0: day + np.timedelta64(h * 60 + m, "m")  # noqa: E731
    entries = pd.DataFrame({"user_id": [2, 2, 2], "day": [day] * 3, "ts": [at(9), at(10, 30), at(20)], "reps": [60, 30, 10]})
    daily = pd.DataFrame({"user_id": [2], "day": [day], "total": [100], "goal": [100]})
    notif = pd.DataFrame({"sender_id": [1, 1, 3], "recipient_id": [2, 2, 2],
                          "ts": [at(10), at(10, 20), at(21)], "delivered": [True, False, True]})
    out = notification_outcomes(entries, notif, daily).set_index("ts")
    assert out.loc[at(10), "sets_next"] == 1 and out.loc[at(10), "reps_before"] == 60
    assert out.loc[at(10, 20), "prior_notifications"] == 1  # one other in the previous hour
    assert out.loc[at(10), "prior_notifications"] == 0
    assert not out.loc[at(21), "available"]  # already at 100 when notified


def _sim(**kw):
    cfg = SimConfig(n_days=60, seed=5, **kw)
    return simulate(cfg), cfg


def _slots(res):
    return build_slots(res.entries, res.notifications, res.daily)


def test_randomized_design_is_unbiased_under_the_null():
    res, _ = _sim(notification_effect=0.0, delivery_prob=0.5)
    est = randomized_effect(_slots(res))
    assert est["ci_low"] <= 1.0 <= est["ci_high"]
    prox = proximal_effect(notification_outcomes(res.entries, res.notifications, res.daily))
    assert prox["ci_low"] <= 1.0 <= prox["ci_high"]


def test_randomized_design_recovers_the_structural_effect_without_depletion():
    # with an unreachable goal nobody leaves the risk set, so the hazard ratio is exact
    res, cfg = _sim(delivery_prob=0.5, goal=100_000)
    est = randomized_effect(_slots(res))
    assert est["ci_low"] <= cfg.true_hazard_ratio <= est["ci_high"]


def test_depletion_attenuates_but_keeps_the_sign():
    res, cfg = _sim(delivery_prob=0.5)
    est = randomized_effect(_slots(res))
    assert 1.0 < est["ci_low"] and est["hazard_ratio"] < cfg.true_hazard_ratio
    prox = proximal_effect(notification_outcomes(res.entries, res.notifications, res.daily))
    assert prox["ci_low"] > 1.0


def test_naive_estimate_is_confounded():
    # no real effect at all, yet friends training together makes notifications "look" effective
    res, _ = _sim(notification_effect=0.0, delivery_prob=1.0)
    assert naive_effect(_slots(res))["ci_low"] > 1.0
