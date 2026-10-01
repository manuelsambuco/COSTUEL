"""Turn raw OneMore tables into analysis-ready datasets.

Works identically on simulated data and on the app export (``python manage.py
export_analysis_data``): both share the schema users / edges / entries / daily.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_CUTOFFS = (10, 13, 16, 19, 21)


def complete_panel(daily: pd.DataFrame, users: pd.DataFrame, default_goal: int = 100) -> pd.DataFrame:
    """One row per user and calendar day, filling days without any log with zeros.

    The app only stores a DailyLog when the user logs something, so "no row" means
    "did nothing that day".
    """
    daily = daily.copy()
    daily["day"] = pd.to_datetime(daily["day"])
    days = pd.date_range(daily["day"].min(), daily["day"].max(), freq="D")
    grid = pd.MultiIndex.from_product([users["user_id"].unique(), days], names=["user_id", "day"])
    panel = daily.set_index(["user_id", "day"]).reindex(grid).reset_index()
    panel["total"] = panel["total"].fillna(0).astype(int)
    panel["goal"] = panel.groupby("user_id")["goal"].transform(lambda g: g.ffill().bfill()).fillna(default_goal)
    panel["goal"] = panel["goal"].astype(int)
    panel["completed"] = panel["total"] >= panel["goal"]
    return panel


def _streak_before(completed: np.ndarray) -> np.ndarray:
    out = np.zeros(len(completed), dtype=int)
    run = 0
    for i, c in enumerate(completed):
        out[i] = run
        run = run + 1 if c else 0
    return out


def daily_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Add calendar features and lagged behaviour (nothing here looks at the same day)."""
    df = panel.sort_values(["user_id", "day"]).reset_index(drop=True)
    df["weekday"] = df["day"].dt.dayofweek
    df["is_weekend"] = df["weekday"] >= 5
    df["day_index"] = (df["day"] - df["day"].min()).dt.days
    by_user = df.groupby("user_id", sort=False)["completed"]
    df["streak_before"] = np.concatenate([_streak_before(g.to_numpy()) for _, g in by_user])
    df["rate_7d"] = by_user.transform(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).fillna(0.0)
    return df


def streak_spells(panel: pd.DataFrame, min_start_day: int = 0) -> pd.DataFrame:
    """Runs of consecutive completed days, for survival analysis.

    ``event`` is 1 when the streak was broken inside the observation window and 0 when it
    was still alive on the last observed day (right-censored).
    """
    df = panel.sort_values(["user_id", "day"])
    last_day = df["day"].max()
    rows = []
    for user_id, g in df.groupby("user_id", sort=False):
        days, done = g["day"].to_numpy(), g["completed"].to_numpy()
        i = 0
        while i < len(done):
            if not done[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(done) and done[j + 1]:
                j += 1
            rows.append((user_id, days[i], j - i + 1, int(days[j] < np.datetime64(last_day))))
            i = j + 1
    spells = pd.DataFrame(rows, columns=["user_id", "start", "length", "event"])
    start0 = df["day"].min()
    spells["start_day"] = (spells["start"] - start0).dt.days
    return spells[spells["start_day"] >= min_start_day].reset_index(drop=True)


def completion_times(entries: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """When (if ever) each user reached the goal on each day."""
    e = entries.copy()
    e["day"] = pd.to_datetime(e["day"])
    e = e.sort_values(["user_id", "day", "ts"])
    e["cum"] = e.groupby(["user_id", "day"])["reps"].cumsum()
    e = e.merge(panel[["user_id", "day", "goal"]], on=["user_id", "day"], how="left")
    done = e[e["cum"] >= e["goal"]].groupby(["user_id", "day"], as_index=False)["ts"].min()
    return done.rename(columns={"ts": "done_ts"})


def completion_snapshots(
    entries: pd.DataFrame,
    panel: pd.DataFrame,
    edges: pd.DataFrame,
    cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
) -> pd.DataFrame:
    """One row per (user, day, cutoff hour) for users who have not finished yet at the cutoff.

    Label: ``completed`` (did they reach the goal by the end of the day?). Every feature
    only uses information available *before* the cutoff.
    """
    feats = daily_features(panel)
    e = entries.copy()
    e["day"] = pd.to_datetime(e["day"])
    done = completion_times(entries, panel)

    # friends' activity: each friend's set / completion appears once per friend link
    friend_sets = edges.merge(e, left_on="friend_id", right_on="user_id", suffixes=("", "_f"))
    friend_sets = friend_sets[["user_id", "day", "ts"]]
    friend_done = edges.merge(done, left_on="friend_id", right_on="user_id", suffixes=("", "_f"))
    friend_done = friend_done[["user_id", "day", "done_ts"]]
    n_friends = edges.groupby("user_id").size().rename("n_friends")

    out = []
    for h in cutoffs:
        def before(df: pd.DataFrame, col: str) -> pd.Series:
            return df[col] < df["day"] + np.timedelta64(h, "h")

        own = e[before(e, "ts")].groupby(["user_id", "day"]).agg(
            reps_before=("reps", "sum"), sets_before=("reps", "size"), last_set=("ts", "max"))
        fs = friend_sets[before(friend_sets, "ts")].groupby(["user_id", "day"]).size().rename("friend_sets_before")
        fd = friend_done[before(friend_done, "done_ts")].groupby(["user_id", "day"]).size().rename("friends_done_before")
        snap = feats.join(own, on=["user_id", "day"]).join(fs, on=["user_id", "day"]).join(fd, on=["user_id", "day"])
        snap = snap.join(n_friends, on="user_id")
        snap["cutoff_hour"] = h
        out.append(snap)

    df = pd.concat(out, ignore_index=True)
    for col in ("reps_before", "sets_before", "friend_sets_before", "friends_done_before", "n_friends"):
        df[col] = df[col].fillna(0).astype(int)
    cutoff_ts = df["day"] + pd.to_timedelta(df["cutoff_hour"], unit="h")
    df["hours_since_last_set"] = ((cutoff_ts - df["last_set"]).dt.total_seconds() / 3600).fillna(24.0)
    df["remaining"] = (df["goal"] - df["reps_before"]).clip(lower=0)
    df["friends_done_share"] = np.where(df["n_friends"] > 0, df["friends_done_before"] / df["n_friends"].clip(lower=1), 0.0)
    df = df[df["reps_before"] < df["goal"]].drop(columns=["last_set"])
    return df.sort_values(["day", "user_id", "cutoff_hour"]).reset_index(drop=True)
