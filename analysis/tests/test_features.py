import numpy as np
import pandas as pd

from costuel_analysis.features import complete_panel, completion_snapshots, daily_features, streak_spells

D = pd.Timestamp("2026-03-02")


def day(i):
    return D + np.timedelta64(i, "D")


def test_complete_panel_fills_missing_days():
    users = pd.DataFrame({"user_id": [1, 2]})
    daily = pd.DataFrame({"user_id": [1, 1], "day": [day(0), day(2)], "total": [100, 40], "goal": [100, 100]})
    panel = complete_panel(daily, users)
    assert len(panel) == 6  # 2 users x 3 days
    row = panel[(panel.user_id == 1) & (panel.day == day(1))].iloc[0]
    assert row.total == 0 and not row.completed
    assert panel[panel.user_id == 2]["goal"].eq(100).all()


def test_streaks_and_rolling_rate_use_only_the_past():
    users = pd.DataFrame({"user_id": [1]})
    totals = [100, 100, 0, 100, 100, 100]
    daily = pd.DataFrame({"user_id": 1, "day": [day(i) for i in range(6)], "total": totals, "goal": 100})
    f = daily_features(complete_panel(daily, users))
    assert f["streak_before"].tolist() == [0, 1, 2, 0, 1, 2]
    assert f["rate_7d"].tolist()[:3] == [0.0, 1.0, 1.0]  # day 0 has no history


def test_spells_are_censored_at_the_end():
    users = pd.DataFrame({"user_id": [1]})
    totals = [100, 100, 0, 100, 100, 100]
    daily = pd.DataFrame({"user_id": 1, "day": [day(i) for i in range(6)], "total": totals, "goal": 100})
    spells = streak_spells(complete_panel(daily, users))
    assert spells[["length", "event"]].values.tolist() == [[2, 1], [3, 0]]


def test_completion_snapshots():
    users = pd.DataFrame({"user_id": [1, 2]})
    edges = pd.DataFrame({"user_id": [1, 2], "friend_id": [2, 1]})
    daily = pd.DataFrame({"user_id": [1, 2], "day": [day(0)] * 2, "total": [100, 60], "goal": [100, 100]})
    entries = pd.DataFrame({
        "user_id": [1, 1, 2, 2],
        "day": [day(0)] * 4,
        "ts": [day(0) + np.timedelta64(h, "h") for h in (9, 12, 11, 20)],
        "reps": [50, 50, 30, 30],
    })
    snaps = completion_snapshots(entries, complete_panel(daily, users), edges, cutoffs=(10, 13))
    u1 = snaps[snaps.user_id == 1].set_index("cutoff_hour")
    assert list(u1.index) == [10]  # at 13:00 user 1 is already done -> no snapshot
    assert u1.loc[10, "reps_before"] == 50 and bool(u1.loc[10, "completed"])
    u2 = snaps[snaps.user_id == 2].set_index("cutoff_hour")
    assert u2.loc[10, "reps_before"] == 0 and u2.loc[13, "reps_before"] == 30
    assert u2.loc[10, "friend_sets_before"] == 1 and u2.loc[13, "friends_done_before"] == 1
    assert not bool(u2.loc[13, "completed"])
