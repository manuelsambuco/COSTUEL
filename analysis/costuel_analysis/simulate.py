"""Synthetic COSTUEL data with a known ground truth.

The simulator reproduces the app's mechanics (daily goal, capped sets, notifications to
friends) and plants effects whose true size is known, so every analysis in the notebooks
can be checked against the truth instead of being taken on faith.

Generative story, per user i and day d:

1. **Engagement** (does the user try today?) ~ Bernoulli(sigmoid(eta)), with
   eta = base + motivation_i + habit * log1p(streak) - novelty_decay * d / n_days
         + weekend_effect * weekend_d + group_shock_{g(i), d}
2. **Sets during the day**: an engaged user who has not reached the goal logs sets as a
   Poisson process whose intensity in 10-minute slot s is
   h = rate_i * profile_i(s) * together_boost^{1[group trains together in this hour]}
         * (1 + notification_effect)^{1[a delivered notification in the last hour]}
3. Every set notifies the user's friends (same group). With ``delivery_prob < 1`` each
   notification is delivered at random: a micro-randomized trial.

``together`` sessions are the confounder: friends are active at the same time for a reason
that has nothing to do with notifications, which biases naive observational estimates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

SET_SIZES = np.array([10, 20, 25, 30])  # the app's quick-add buttons


@dataclass(frozen=True)
class SimConfig:
    n_days: int = 120
    group_sizes: tuple[int, ...] = (4, 5, 5, 6, 6, 7, 8, 9, 10)
    start: date = date(2026, 1, 5)  # a Monday
    goal: int = 100
    slot_minutes: int = 10
    # daily engagement (logit scale)
    base_logit: float = 1.0
    motivation_sd: float = 1.0
    habit_strength: float = 0.6  # per log1p(current streak)
    novelty_decay: float = 1.2  # total logit drop from first to last day
    weekend_effect: float = -0.6
    group_day_sd: float = 0.4
    # intra-day behaviour
    mean_sets_per_day: float = 5.5  # expected sets for an average engaged user, ignoring the cap
    together_prob: float = 0.25  # P(a group trains together on a given day)
    together_boost: float = 4.0  # hazard multiplier during the together hour
    # the causal effect we want to recover
    notification_effect: float = 0.5  # true hazard ratio = 1 + notification_effect
    effect_window_minutes: int = 60
    delivery_prob: float = 1.0  # < 1: notifications are delivered at random (MRT)
    seed: int = 7

    @property
    def n_users(self) -> int:
        return sum(self.group_sizes)

    @property
    def slots_per_day(self) -> int:
        return 24 * 60 // self.slot_minutes

    @property
    def true_hazard_ratio(self) -> float:
        return 1.0 + self.notification_effect


@dataclass
class SimResult:
    """Observable tables (same schema as the app export) plus the hidden truth."""

    config: SimConfig
    users: pd.DataFrame  # user_id, group_id
    edges: pd.DataFrame  # user_id, friend_id (who gets notified by whom)
    entries: pd.DataFrame  # user_id, day, ts, reps
    daily: pd.DataFrame  # user_id, day, total, goal, completed
    notifications: pd.DataFrame  # sender_id, recipient_id, ts, delivered
    truth_users: pd.DataFrame  # latent per-user parameters
    truth_days: pd.DataFrame  # engagement and together sessions per user-day
    meta: dict = field(default_factory=dict)


def _hour_profiles(rng: np.random.Generator, n: int, slots_per_day: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-user distribution of activity over the day's slots (rows sum to 1)."""
    hours = (np.arange(slots_per_day) + 0.5) * 24 / slots_per_day
    peaks = np.array([7.5, 13.0, 20.0])  # morning, lunch, evening
    widths = np.array([1.0, 1.0, 1.6])
    weights = rng.dirichlet([1.2, 0.8, 2.0], size=n)  # evening people are the most common
    bumps = np.exp(-0.5 * ((hours[None, :] - peaks[:, None]) / widths[:, None]) ** 2)  # (3, slots)
    profile = weights @ bumps + 0.05  # a little activity at any waking hour
    profile[:, hours < 6.5] = 0.0  # nobody trains at night
    profile /= profile.sum(axis=1, keepdims=True)
    return profile, weights


def simulate(config: SimConfig | None = None) -> SimResult:
    cfg = config or SimConfig()
    rng = np.random.default_rng(cfg.seed)
    n, spd = cfg.n_users, cfg.slots_per_day
    window = cfg.effect_window_minutes // cfg.slot_minutes

    group = np.repeat(np.arange(len(cfg.group_sizes)), cfg.group_sizes)
    members = [np.flatnonzero(group == g) for g in range(len(cfg.group_sizes))]
    motivation = rng.normal(0.0, cfg.motivation_sd, n)
    rate = cfg.mean_sets_per_day * np.exp(0.3 * motivation)  # motivated users also train more often
    profile, chrono = _hour_profiles(rng, n, spd)
    size_pref = rng.dirichlet([2.0, 2.0, 2.0, 2.0], size=n)

    entries, notifications, truth_days, daily = [], [], [], []
    streak = np.zeros(n, dtype=int)

    for d in range(cfg.n_days):
        day = cfg.start + timedelta(days=d)
        midnight = datetime.combine(day, datetime.min.time())
        weekend = day.weekday() >= 5
        shock = rng.normal(0.0, cfg.group_day_sd, len(members))
        eta = (
            cfg.base_logit + motivation + cfg.habit_strength * np.log1p(streak)
            - cfg.novelty_decay * d / max(cfg.n_days - 1, 1)
            + cfg.weekend_effect * weekend + shock[group]
        )
        engaged = rng.random(n) < 1.0 / (1.0 + np.exp(-eta))
        remaining = np.where(engaged, cfg.goal, 0)
        total = np.zeros(n, dtype=int)

        together_hour = np.where(rng.random(len(members)) < cfg.together_prob,
                                 rng.integers(18, 22, len(members)), -1)
        notified_until = np.full(n, -1)

        for s in range(spd):
            active = remaining > 0
            if not active.any():
                break
            hour = s * cfg.slot_minutes // 60
            hazard = rate * profile[:, s]
            hazard = hazard * np.where(together_hour[group] == hour, cfg.together_boost, 1.0)
            hazard = hazard * np.where(notified_until >= s, 1.0 + cfg.notification_effect, 1.0)
            # Sets arrive as a Poisson process: the hazard is an intensity (sets per slot),
            # so a notification multiplies the *rate*, exactly what the analysis estimates.
            counts = np.where(active, rng.poisson(hazard), 0)
            for i in np.flatnonzero(counts):
                offsets = np.sort(rng.uniform(0, cfg.slot_minutes, counts[i]))
                for offset in offsets:
                    if remaining[i] <= 0:
                        break
                    reps = int(min(rng.choice(SET_SIZES, p=size_pref[i]), remaining[i]))
                    ts = midnight + timedelta(minutes=s * cfg.slot_minutes + float(offset))
                    remaining[i] -= reps
                    total[i] += reps
                    entries.append((i, day, ts, reps))
                    for k in members[group[i]]:
                        if k == i:
                            continue
                        delivered = bool(rng.random() < cfg.delivery_prob)
                        notifications.append((i, k, ts, delivered))
                        if delivered:  # the effect starts from the next slot
                            notified_until[k] = max(notified_until[k], s + window)

        completed = total >= cfg.goal
        for i in range(n):
            daily.append((i, day, int(total[i]), cfg.goal, bool(completed[i])))
            truth_days.append((i, day, bool(engaged[i]), int(together_hour[group[i]]), float(eta[i])))
        streak = np.where(completed, streak + 1, 0)

    users = pd.DataFrame({"user_id": np.arange(n), "group_id": group})
    edges = pd.DataFrame(
        [(i, k) for m in members for i in m for k in m if i != k], columns=["user_id", "friend_id"]
    )

    def frame(rows: list, columns: list[str]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=columns)
        if "day" in df.columns:  # same dtype as the app export: datetime64 at midnight
            df["day"] = pd.to_datetime(df["day"])
        return df

    return SimResult(
        config=cfg,
        users=users,
        edges=edges,
        entries=frame(entries, ["user_id", "day", "ts", "reps"]),
        daily=frame(daily, ["user_id", "day", "total", "goal", "completed"]),
        notifications=frame(notifications, ["sender_id", "recipient_id", "ts", "delivered"]),
        truth_users=pd.DataFrame({
            "user_id": np.arange(n), "group_id": group, "motivation": motivation, "set_rate": rate,
            "w_morning": chrono[:, 0], "w_lunch": chrono[:, 1], "w_evening": chrono[:, 2],
        }),
        truth_days=frame(truth_days, ["user_id", "day", "engaged", "together_hour", "eta"]),
        meta={"config": asdict(cfg), "true_hazard_ratio": cfg.true_hazard_ratio},
    )
