import numpy as np
import pandas as pd
import pytest

from costuel_analysis import SimConfig, simulate
from costuel_analysis.simulate import SET_SIZES

SMALL = SimConfig(n_days=21, group_sizes=(3, 4, 5), seed=3)


@pytest.fixture(scope="module")
def sim():
    return simulate(SMALL)


def test_reproducible(sim):
    again = simulate(SMALL)
    pd.testing.assert_frame_equal(sim.entries, again.entries)
    pd.testing.assert_frame_equal(sim.notifications, again.notifications)


def test_daily_is_consistent_with_entries(sim):
    assert len(sim.daily) == SMALL.n_users * SMALL.n_days
    sums = sim.entries.groupby(["user_id", "day"])["reps"].sum()
    merged = sim.daily.set_index(["user_id", "day"])["total"]
    assert (merged.loc[sums.index] == sums).all()
    assert (sim.daily["total"] <= SMALL.goal).all()  # sets beyond the goal are never counted
    assert (sim.daily["completed"] == (sim.daily["total"] >= SMALL.goal)).all()


def test_sets_use_app_buttons_except_the_capped_last_one(sim):
    e = sim.entries.sort_values("ts")
    last = e.groupby(["user_id", "day"])["ts"].transform("max") == e["ts"]
    assert e.loc[~last, "reps"].isin(SET_SIZES).all()
    assert (e["reps"] > 0).all()


def test_notifications_go_to_group_mates_only(sim):
    group = sim.users.set_index("user_id")["group_id"]
    n = sim.notifications
    assert (group.loc[n["sender_id"]].to_numpy() == group.loc[n["recipient_id"]].to_numpy()).all()
    assert (n["sender_id"] != n["recipient_id"]).all()
    assert n["delivered"].all()  # delivery_prob defaults to 1


def test_randomized_delivery():
    res = simulate(SimConfig(n_days=14, group_sizes=(4, 4), delivery_prob=0.5, seed=1))
    share = res.notifications["delivered"].mean()
    assert 0.4 < share < 0.6


def test_habit_and_novelty_show_up():
    res = simulate(SimConfig(n_days=60, seed=11))
    rate = res.daily.groupby("day")["completed"].mean().to_numpy()
    assert rate[:10].mean() > rate[-10:].mean()  # novelty wears off
    assert np.isclose(res.meta["true_hazard_ratio"], 1.5)
