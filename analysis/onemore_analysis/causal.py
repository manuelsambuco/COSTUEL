"""Does a friend's notification make you do a set sooner?

Discrete-time hazard analysis on 10-minute slots. For every user, day and slot we know
whether the user was still "at risk" (below the goal), whether they logged a set, and
which notifications they had received in the previous hour.

* ``naive_effect``: compare slots after a notification with all other slots, adjusting for
  user and hour of day. Biased when friends are active together for other reasons.
* ``randomized_effect``: in a micro-randomized trial each notification is delivered with
  a fixed probability. Among slots where a friend *did* log a set in the last hour, whether
  it was delivered is random, so the comparison is unbiased (stratifying on how many friend
  sets there were, because more sets mean more chances that at least one was delivered).

Both return a hazard ratio estimated with a Poisson GLM on aggregated cells (events /
slots at risk), with standard errors clustered by user.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf


@dataclass
class SlotData:
    users: np.ndarray
    days: np.ndarray
    slot_minutes: int
    sets: np.ndarray  # (U, D, S) number of sets logged in the slot
    at_risk: np.ndarray  # below the goal at the start of the slot
    active_day: np.ndarray  # (U, D) logged at least one set that day (engagement proxy)
    friend_recent: np.ndarray  # friend sets notified in the previous window (delivered or not)
    delivered_recent: np.ndarray  # delivered notifications in the previous window


def _recent(counts: np.ndarray, window: int) -> np.ndarray:
    """Events in slots [s - window, s - 1] for every slot s (i.e. strictly before s)."""
    padded = np.concatenate([np.zeros(counts.shape[:-1] + (1,), dtype=np.int64), np.cumsum(counts, axis=-1)], axis=-1)
    s = np.arange(counts.shape[-1])
    return padded[..., s] - padded[..., np.maximum(s - window, 0)]


def build_slots(
    entries: pd.DataFrame,
    notifications: pd.DataFrame,
    daily: pd.DataFrame,
    slot_minutes: int = 10,
    window_minutes: int = 60,
) -> SlotData:
    users = np.sort(daily["user_id"].unique())
    days = np.sort(pd.to_datetime(daily["day"]).unique())
    uix = {u: i for i, u in enumerate(users)}
    dix = {d: i for i, d in enumerate(days)}
    spd = 24 * 60 // slot_minutes
    shape = (len(users), len(days), spd)

    def index(df: pd.DataFrame, user_col: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        ts = pd.to_datetime(df["ts"])
        return (
            df[user_col].map(uix).to_numpy(),
            ts.dt.normalize().map(dix).to_numpy(),
            ((ts.dt.hour * 60 + ts.dt.minute) // slot_minutes).to_numpy(),
        )

    sets = np.zeros(shape, dtype=np.int64)
    reps = np.zeros(shape, dtype=np.int64)
    u, d, s = index(entries, "user_id")
    np.add.at(sets, (u, d, s), 1)
    np.add.at(reps, (u, d, s), entries["reps"].to_numpy())

    notif_all = np.zeros(shape, dtype=np.int64)
    notif_del = np.zeros(shape, dtype=np.int64)
    if len(notifications):
        u, d, s = index(notifications, "recipient_id")
        np.add.at(notif_all, (u, d, s), 1)
        np.add.at(notif_del, (u, d, s), notifications["delivered"].astype(int).to_numpy())

    goal = np.full(shape[:2], 100, dtype=np.int64)
    dd = daily.assign(day=pd.to_datetime(daily["day"]))
    goal[dd["user_id"].map(uix).to_numpy(), dd["day"].map(dix).to_numpy()] = dd["goal"].to_numpy()

    window = window_minutes // slot_minutes
    cum_before = np.cumsum(reps, axis=2) - reps
    return SlotData(
        users=users, days=days, slot_minutes=slot_minutes, sets=sets,
        at_risk=cum_before < goal[:, :, None],
        active_day=sets.sum(axis=2) > 0,
        friend_recent=_recent(notif_all, window),
        delivered_recent=_recent(notif_del, window),
    )


def _cells(data: SlotData, mask: np.ndarray, extra: dict[str, np.ndarray]) -> pd.DataFrame:
    """Aggregate slots into (user, hour, ...) cells with events and exposure."""
    U, D, S = data.sets.shape
    hour = np.broadcast_to((np.arange(S) * data.slot_minutes // 60)[None, None, :], (U, D, S))
    user = np.broadcast_to(np.arange(U)[:, None, None], (U, D, S))
    frame = pd.DataFrame({
        "user": user[mask], "hour": hour[mask], "n_sets": data.sets[mask],
        **{k: v[mask] for k, v in extra.items()},
    })
    keys = ["user", "hour", *extra.keys()]
    # events = sets (not "slots with a set"): the Poisson rate ratio is then the intensity ratio
    return frame.groupby(keys, as_index=False).agg(events=("n_sets", "sum"), slots=("n_sets", "size"))


def _fit(cells: pd.DataFrame, formula: str, term: str = "exposed") -> dict:
    model = smf.glm(formula, data=cells, family=sm.families.Poisson(), offset=np.log(cells["slots"]))
    res = model.fit(cov_type="cluster", cov_kwds={"groups": cells["user"].to_numpy()})
    coef, se = res.params[term], res.bse[term]
    exposed = cells[cells[term] == 1]
    return {
        "hazard_ratio": float(np.exp(coef)),
        "ci_low": float(np.exp(coef - 1.96 * se)),
        "ci_high": float(np.exp(coef + 1.96 * se)),
        "exposed_slots": int(exposed["slots"].sum()),
        "exposed_events": int(exposed["events"].sum()),
        "total_slots": int(cells["slots"].sum()),
    }


def base_mask(data: SlotData, start_hour: int = 6) -> np.ndarray:
    """Waking slots in which the user is still below the goal.

    Deliberately *not* restricted to "days with at least one set": a notification can be
    what triggers the first set of the day, so that filter would condition on an outcome
    of the treatment (selection bias).
    """
    S = data.sets.shape[2]
    awake = (np.arange(S) * data.slot_minutes // 60) >= start_hour
    return data.at_risk & awake[None, None, :]


def naive_effect(data: SlotData, start_hour: int = 6) -> dict:
    """Slots after a delivered notification vs. all other slots (user + hour fixed effects)."""
    mask = base_mask(data, start_hour)
    cells = _cells(data, mask, {"exposed": (data.delivered_recent > 0).astype(int)})
    return _fit(cells, "events ~ exposed + C(user) + C(hour)")


def randomized_effect(data: SlotData, start_hour: int = 6, max_stratum: int = 4) -> dict:
    """Micro-randomized trial: delivered vs. not delivered, among slots with recent friend activity.

    Unbiased under the null, but the hazard ratio is *attenuated* when the effect is real:
    notified users reach the goal sooner and leave the risk set, so the exposed slots that
    remain are enriched with slower user-days ("depletion of susceptibles", see Hernán 2010,
    *The hazards of hazard ratios*). ``proximal_effect`` avoids this.
    """
    mask = base_mask(data, start_hour) & (data.friend_recent > 0)
    cells = _cells(data, mask, {
        "exposed": (data.delivered_recent > 0).astype(int),
        "stratum": np.minimum(data.friend_recent, max_stratum),
    })
    return _fit(cells, "events ~ exposed + C(stratum) + C(user) + C(hour)")


def notification_outcomes(
    entries: pd.DataFrame, notifications: pd.DataFrame, daily: pd.DataFrame, window_minutes: int = 60
) -> pd.DataFrame:
    """One row per notification: was the recipient still below the goal, and how many sets
    did they log in the following ``window_minutes``?"""
    e = entries.assign(day=pd.to_datetime(entries["day"])).sort_values("ts")
    goal = daily.assign(day=pd.to_datetime(daily["day"])).set_index(["user_id", "day"])["goal"]
    n = notifications.copy()
    n["ts"] = pd.to_datetime(n["ts"])
    n["day"] = n["ts"].dt.normalize()
    n["reps_before"] = 0
    n["sets_next"] = 0
    window = np.timedelta64(window_minutes, "m")
    by_user_day = {k: g for k, g in e.groupby(["user_id", "day"])}
    for (user, day), idx in n.groupby(["recipient_id", "day"]).groups.items():
        g = by_user_day.get((user, day))
        if g is None:
            continue
        times = g["ts"].to_numpy()
        cum = np.concatenate([[0], np.cumsum(g["reps"].to_numpy())])
        t = n.loc[idx, "ts"].to_numpy()
        n.loc[idx, "reps_before"] = cum[np.searchsorted(times, t, side="left")]
        n.loc[idx, "sets_next"] = np.searchsorted(times, t + window, side="right") - np.searchsorted(times, t, side="right")
    n["goal"] = goal.reindex(pd.MultiIndex.from_arrays([n["recipient_id"], n["day"]])).fillna(100).to_numpy()
    n["available"] = n["reps_before"] < n["goal"]
    if "devices" in n.columns:
        # Real data: without a subscribed device a notification could not arrive either way.
        # Known before the draw, so excluding these keeps the comparison randomized.
        n["available"] &= n["devices"] > 0
    n["hour"] = n["ts"].dt.hour
    # other notifications the recipient got in the previous window (decided before this one)
    n = n.sort_values(["recipient_id", "ts"]).reset_index(drop=True)
    n["prior_notifications"] = 0
    for _, idx in n.groupby("recipient_id").groups.items():
        t = n.loc[idx, "ts"].to_numpy()
        n.loc[idx, "prior_notifications"] = np.arange(len(t)) - np.searchsorted(t, t - window, side="right")
    return n


def proximal_effect(outcomes: pd.DataFrame, first_in_window: bool = False) -> dict:
    """Effect of delivering one notification on the recipient's sets in the next hour.

    Availability (still below the goal) is measured *before* the randomization, so there
    is no post-treatment selection: this is the excursion effect estimated in
    micro-randomized trials (Boruvka et al., 2018). Rate ratio from a Poisson GLM with
    recipient and hour fixed effects, SEs clustered by recipient.

    It is the *marginal* effect of one more notification. With ``first_in_window`` only
    notifications with no other notification in the previous window are used (a condition
    fixed before randomization, so still unbiased): the effect of being notified at all.
    """
    df = outcomes[outcomes["available"]]
    if first_in_window:
        df = df[df["prior_notifications"] == 0]
    df = df.assign(delivered=lambda d: d["delivered"].astype(int))
    res = smf.glm("sets_next ~ delivered + C(recipient_id) + C(hour)", data=df,
                  family=sm.families.Poisson()).fit(cov_type="cluster", cov_kwds={"groups": df["recipient_id"].to_numpy()})
    coef, se = res.params["delivered"], res.bse["delivered"]
    means = df.groupby("delivered")["sets_next"].mean()
    return {
        "rate_ratio": float(np.exp(coef)),
        "ci_low": float(np.exp(coef - 1.96 * se)),
        "ci_high": float(np.exp(coef + 1.96 * se)),
        "sets_next_hour_delivered": float(means.get(1, np.nan)),
        "sets_next_hour_not_delivered": float(means.get(0, np.nan)),
        "n_notifications": int(len(df)),
    }
