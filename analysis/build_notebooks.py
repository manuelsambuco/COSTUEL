"""Build and execute the analysis notebooks (so they render with outputs on GitHub).

    python build_notebooks.py            # all notebooks
    python build_notebooks.py 02 04      # only some

The notebooks are generated from the cell lists below to keep them reviewable as code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
OUT = HERE / "notebooks"

SETUP = """\
import sys, warnings
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))
warnings.filterwarnings("ignore", category=FutureWarning)

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from costuel_analysis import SimConfig, simulate
from costuel_analysis.plotting import use_style, subtitle, date_axis, SERIES, REFERENCE, MUTED, INK_SECONDARY, GRID

use_style()
pd.set_option("display.precision", 3)
pd.set_option("display.width", 120)"""


def md(text: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_markdown_cell(text.strip())


def code(text: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_code_cell(text.strip())


# ---------------------------------------------------------------------------------------
NB01 = [
    md("""
# 01 · Simulated data and exploratory analysis

**COSTUEL** is a small web app where friends challenge each other to do 100 push-ups a day:
quick buttons (+10, +20, +25, +30) log a *set*, anything beyond the daily goal is not counted,
and every set sends a push notification to the user's friends.

The real usage data belongs to a handful of friends: too small and too private to publish. So
this project starts from a **simulator with a known ground truth**. It reproduces the app's
mechanics and plants behavioural effects of a chosen size; every later notebook then has to
*recover* those effects from the observable tables alone. That turns the analysis from
"here is a number" into "here is a method, and here is proof it works".

| Planted effect | Parameter | Checked in |
|---|---|---|
| Users differ in motivation | `motivation_sd` | 01, 02 |
| Streaks build habits | `habit_strength` | 02 |
| Novelty wears off | `novelty_decay` | 01 |
| Weekends are harder | `weekend_effect` | 01 |
| Friends sometimes train together (a confounder) | `together_prob`, `together_boost` | 04 |
| **A friend's notification raises your set rate** | `notification_effect` | 04 |

The observable tables have exactly the schema of the app export
(`python manage.py export_analysis_data`), so every notebook also runs on real data.
"""),
    code(SETUP),
    md("## Ground truth\nThe configuration below *is* the truth the analyses try to recover."),
    code("""
cfg = SimConfig()
sim = simulate(cfg)
truth = pd.Series({k: v for k, v in sim.meta["config"].items() if k not in ("group_sizes", "start")}, name="value")
truth.to_frame()
"""),
    code("""
print(f"{cfg.n_users} users in {len(cfg.group_sizes)} friend groups, {cfg.n_days} days")
for name in ("users", "edges", "entries", "daily", "notifications"):
    print(f"{name:14s} {len(getattr(sim, name)):>8,} rows")
sim.entries.head()
"""),
    md("""
## How many people hit 100 each day?

The daily completion rate starts around one in two and slowly declines: the planted
`novelty_decay`. Weekly dips come from weekends.
"""),
    code("""
daily_rate = sim.daily.groupby("day")["completed"].mean()
rolling = daily_rate.rolling(7, center=True, min_periods=4).mean()

fig, ax = plt.subplots(figsize=(8, 3.4))
ax.plot(daily_rate.index, daily_rate.values, color=SERIES[0], alpha=0.25, linewidth=1)
ax.plot(rolling.index, rolling.values, color=SERIES[0])
ax.set_ylim(0, 1)
ax.yaxis.set_major_formatter(plt.matplotlib.ticker.PercentFormatter(1.0))
date_axis(ax)
ax.set_title("Share of users who reached 100 push-ups", pad=18)
subtitle(ax, "Daily value (light) and 7-day rolling mean")
ax.annotate(f"{rolling.dropna().iloc[0]:.0%}", (rolling.dropna().index[0], rolling.dropna().iloc[0]),
            textcoords="offset points", xytext=(0, 8), color=INK_SECONDARY, fontsize=9)
ax.annotate(f"{rolling.dropna().iloc[-1]:.0%}", (rolling.dropna().index[-1], rolling.dropna().iloc[-1]),
            textcoords="offset points", xytext=(-10, 8), color=INK_SECONDARY, fontsize=9)
plt.show()

weekly = sim.daily.assign(week=lambda d: d["day"].dt.isocalendar().week).groupby("week")["completed"].mean()
weekly.rename("completion rate").to_frame().T
"""),
    md("## When do people train?\nThree peaks (morning, lunch, evening), mixed per user."),
    code("""
by_hour = sim.entries["ts"].dt.hour.value_counts().reindex(range(24), fill_value=0)

fig, ax = plt.subplots(figsize=(8, 3.2))
ax.bar(by_hour.index, by_hour.values, width=0.8, color=SERIES[0])
ax.set_xticks(range(0, 24, 3))
ax.set_xlabel("hour of day")
ax.set_title("Sets logged by hour", pad=18)
subtitle(ax, f"{len(sim.entries):,} sets over {cfg.n_days} days")
plt.show()
by_hour[by_hour > 0].rename("sets").to_frame().T
"""),
    md("## Weekends are harder\nThe planted `weekend_effect` lowers engagement on Saturday and Sunday."),
    code("""
names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
by_wd = sim.daily.assign(wd=sim.daily["day"].dt.dayofweek).groupby("wd")["completed"].mean()

fig, ax = plt.subplots(figsize=(6, 3.2))
ax.bar(names, by_wd.values, width=0.7, color=SERIES[0])
ax.yaxis.set_major_formatter(plt.matplotlib.ticker.PercentFormatter(1.0))
ax.set_title("Completion rate by weekday", pad=18)
subtitle(ax, "Share of user-days that reached the goal")
plt.show()
by_wd.set_axis(names).rename("completion rate").to_frame().T
"""),
    md("""
## Users are very different

The per-user completion rate is spread from almost never to almost always. Because this is
a simulation we can check what drives it: the hidden `motivation` of each user.
"""),
    code("""
per_user = (sim.daily.groupby("user_id")["completed"].mean().rename("completion_rate")
            .to_frame().join(sim.truth_users.set_index("user_id")["motivation"]))
rho = per_user.corr(method="spearman").iloc[0, 1]

fig, ax = plt.subplots(figsize=(6, 4))
ax.scatter(per_user["motivation"], per_user["completion_rate"], s=36, color=SERIES[0],
           edgecolor="white", linewidth=1)
ax.yaxis.set_major_formatter(plt.matplotlib.ticker.PercentFormatter(1.0))
ax.set_xlabel("hidden motivation (simulation truth)")
ax.set_ylabel("observed completion rate")
ax.grid(axis="x")
ax.set_title("Observed behaviour tracks the hidden trait", pad=18)
subtitle(ax, f"One dot per user · Spearman rho = {rho:.2f}")
plt.show()
per_user.describe().T
"""),
    md("## Set sizes\nUsers mostly tap the four buttons; the last set of a day is often capped at the goal."),
    code("""
sizes = sim.entries["reps"].value_counts(normalize=True).sort_index()
sizes.rename("share of sets").to_frame().T
"""),
    md("""
## Takeaways

* The simulated data looks like a plausible habit app: ~50% daily completion at first,
  slowly decaying, strong weekly rhythm and very heterogeneous users.
* Heterogeneity matters for everything that follows: comparisons that pool users can be
  driven by *who* the users are rather than by what happened to them.

Next: [02 · How long do streaks last?](02_streak_survival.ipynb)
"""),
]

# ---------------------------------------------------------------------------------------
NB02 = [
    md("""
# 02 · How long do streaks last? (survival analysis)

The app shows a 🔥 streak: consecutive days at 100. Two questions:

1. How long does a typical streak survive, and who keeps it longer?
2. Do streaks **build habits** (the longer you go, the less likely you break), or do long
   streaks just belong to people who were always going to keep them?

Streaks are durations with **right-censoring** (some are still alive when data ends), so this is
a survival problem: Kaplan-Meier curves and a Cox model.
"""),
    code(SETUP + """
from lifelines import CoxPHFitter, KaplanMeierFitter
from costuel_analysis.features import complete_panel, streak_spells"""),
    code("""
cfg = SimConfig()
sim = simulate(cfg)
panel = complete_panel(sim.daily, sim.users)

# Baseline commitment = completion rate in the first 2 weeks. Only streaks that start
# afterwards are analysed, so the covariate is measured before the outcome.
BASELINE_DAYS = 14
first = panel[panel["day"] < panel["day"].min() + np.timedelta64(BASELINE_DAYS, "D")]
baseline = first.groupby("user_id")["completed"].mean().rename("baseline_rate")

# terciles of *users* (not of streaks, or users with many streaks would weigh more)
commitment = pd.qcut(baseline.rank(method="first"), 3, labels=["low", "medium", "high"]).rename("commitment")
spells = streak_spells(panel, min_start_day=BASELINE_DAYS).join(baseline, on="user_id").join(commitment, on="user_id")
spells = spells.join(sim.users.set_index("user_id")["group_id"], on="user_id")
spells["group_size"] = spells["group_id"].map(sim.users["group_id"].value_counts())
spells["weekend_start"] = spells["start"].dt.dayofweek >= 5
print(f"{len(spells):,} streaks · {spells['event'].mean():.0%} broken, {1 - spells['event'].mean():.0%} censored")
spells.head()
"""),
    md("## Kaplan-Meier: probability a streak is still alive after *n* days"),
    code("""
fig, ax = plt.subplots(figsize=(8, 4))
rows = []
for color, level in zip(SERIES, ["low", "medium", "high"]):
    s = spells[spells["commitment"] == level]
    km = KaplanMeierFitter().fit(s["length"], s["event"], label=f"{level} commitment")
    km.plot_survival_function(ax=ax, color=color, ci_alpha=0.12, linewidth=2)
    y = km.survival_function_at_times(2).iloc[0]  # direct label where the curves are well apart
    ax.annotate(level, (2, y), xytext=(8, 4), textcoords="offset points", color=INK_SECONDARY, va="bottom")
    rows.append({"commitment": level, "users": s["user_id"].nunique(), "streaks": len(s),
                 "median length (days)": km.median_survival_time_,
                 "P(alive after 3 days)": km.survival_function_at_times(3).iloc[0],
                 "P(alive after 7 days)": km.survival_function_at_times(7).iloc[0]})
ax.set_xlim(0, 15)
ax.set_ylim(0, 1)
ax.set_xlabel("streak length (days)")
ax.yaxis.set_major_formatter(plt.matplotlib.ticker.PercentFormatter(1.0))
ax.legend(loc="upper right")
ax.set_title("Streak survival by baseline commitment", pad=18)
subtitle(ax, "Kaplan-Meier estimate with 95% band · commitment = completion rate in the first 2 weeks")
plt.show()
pd.DataFrame(rows).set_index("commitment")
"""),
    md("""
## Cox proportional hazards

Hazard ratios below 1 mean a *lower* risk of breaking the streak. Standard errors are
clustered by user, since each user contributes many streaks.
"""),
    code("""
cox_df = spells[["length", "event", "baseline_rate", "group_size", "weekend_start", "user_id"]].copy()
cox_df["baseline_rate_10pp"] = cox_df.pop("baseline_rate") * 10  # per +10 percentage points
cox_df["weekend_start"] = cox_df["weekend_start"].astype(int)
cph = CoxPHFitter().fit(cox_df, duration_col="length", event_col="event", cluster_col="user_id")
cph.summary[["exp(coef)", "exp(coef) lower 95%", "exp(coef) upper 95%", "p"]].rename(columns={"exp(coef)": "hazard ratio"})
"""),
    md("""
## Habit or selection?

The daily risk of breaking a streak falls as the streak gets longer. That looks like
*habit formation*, but it could equally be **selection**: long streaks are over-represented
among highly motivated users, who break less on every day (unobserved heterogeneity, or
"frailty").

With real data you could not tell the two apart from this curve alone. With the simulator
we can: rerun the exact same world with `habit_strength = 0`.
"""),
    code("""
def break_hazard(spells, max_len=15, min_at_risk=30):
    \"\"\"Life table: P(break on day k | streak alive on day k), only where enough streaks remain.\"\"\"
    out = []
    for k in range(1, max_len + 1):
        at_risk = (spells["length"] >= k).sum()
        if at_risk < min_at_risk:
            break
        broke = ((spells["length"] == k) & (spells["event"] == 1)).sum()
        out.append((k, broke / at_risk, at_risk))
    return pd.DataFrame(out, columns=["day", "hazard", "at_risk"]).set_index("day")

# three independent runs per world, pooled, so the tail is not pure noise
worlds = {"with habit (truth: 0.6)": {}, "no habit (habit_strength = 0)": {"habit_strength": 0.0}}
hazards = {}
for name, kw in worlds.items():
    runs = []
    for seed in (7, 8, 9):
        s = simulate(SimConfig(seed=seed, **kw))
        runs.append(streak_spells(complete_panel(s.daily, s.users), min_start_day=BASELINE_DAYS))
    hazards[name] = break_hazard(pd.concat(runs, ignore_index=True))

fig, ax = plt.subplots(figsize=(8, 3.8))
for color, (name, h) in zip(SERIES, hazards.items()):
    ax.plot(h.index, h["hazard"], color=color, marker="o", markersize=5, label=name)
ax.set_ylim(0, None)
ax.yaxis.set_major_formatter(plt.matplotlib.ticker.PercentFormatter(1.0))
ax.set_xlabel("day of the streak")
ax.set_xticks(range(1, 16))
ax.legend()
ax.set_title("Risk of breaking the streak on day n", pad=18)
subtitle(ax, "Empirical hazard among streaks still alive on that day · 3 pooled runs, days with >= 30 streaks at risk")
plt.show()
pd.concat({k: v for k, v in hazards.items()}, axis=1).round(3)
"""),
    md("""
## Takeaways

* A typical streak is short, and baseline commitment is by far the strongest predictor of
  keeping it: the Cox model puts a large protective effect on each +10 pp of early completion.
* **The falling hazard is not proof of habit formation.** Even in the world with *no* habit
  effect the curve declines, because long streaks select motivated users. The gap between the
  two curves is the actual habit effect. On real data, separating them needs within-user
  variation (e.g. a frailty / mixed-effects survival model) or an experiment.

Next: [03 · Will they make it today?](03_completion_model.ipynb)
"""),
]

# ---------------------------------------------------------------------------------------
NB03 = [
    md("""
# 03 · Will they make it today? (predictive model)

A product question: at a given hour, which users who have **not** reached 100 yet are likely
to fail today? Those are the ones worth a gentle evening reminder, and nobody else.

**Setup.** One row per user, day and cutoff hour (10:00, 13:00, 16:00, 19:00, 21:00) for users
still below the goal. Features use only information available *before* the cutoff (own sets,
streak, last 7 days, friends' activity). Label: reached 100 by midnight.

**Validation.** A *time-based* split: train on the first 80 days, test on the last 40. A random
split would leak the future (the same user-week in train and test) and overstate performance.
"""),
    code(SETUP + """
from sklearn.calibration import calibration_curve
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from costuel_analysis.features import complete_panel, completion_snapshots"""),
    code("""
cfg = SimConfig()
sim = simulate(cfg)
panel = complete_panel(sim.daily, sim.users)
snaps = completion_snapshots(sim.entries, panel, sim.edges)

FEATURES = ["cutoff_hour", "remaining", "sets_before", "hours_since_last_set",
            "streak_before", "rate_7d", "is_weekend", "friend_sets_before", "friends_done_share"]
# Domain knowledge as monotonic constraints: more reps missing can only hurt, a better
# recent record can only help. Keeps the model sane where data is sparse.
MONOTONIC = {"remaining": -1, "streak_before": 1, "rate_7d": 1}
split_day = snaps["day"].min() + np.timedelta64(80, "D")
train, test = snaps[snaps["day"] < split_day], snaps[snaps["day"] >= split_day]
X_train, y_train = train[FEATURES].astype(float), train["completed"].astype(int)
X_test, y_test = test[FEATURES].astype(float), test["completed"].astype(int)
print(f"train {len(train):,} rows · test {len(test):,} rows · positive rate {y_train.mean():.1%} / {y_test.mean():.1%}")
"""),
    md("""
## Models

* **Baseline**: logistic regression on the obvious features only (hour, reps still missing).
* **Logistic regression** on all features (standardised).
* **Gradient boosting** (`HistGradientBoostingClassifier`), which can capture interactions
  such as "40 reps missing is easy at 13:00, hard at 21:00", with **monotonic constraints**
  on reps missing, streak and last-7-days rate.
"""),
    code("""
models = {
    "baseline (hour + remaining)": (make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)), ["cutoff_hour", "remaining"]),
    "logistic regression": (make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)), FEATURES),
    "gradient boosting": (HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                                         l2_regularization=1.0, random_state=0,
                                                         monotonic_cst=[MONOTONIC.get(f, 0) for f in FEATURES]), FEATURES),
}
preds, rows = {}, []
for name, (model, cols) in models.items():
    model.fit(X_train[cols], y_train)
    p = model.predict_proba(X_test[cols])[:, 1]
    preds[name] = p
    rows.append({"model": name, "ROC AUC": roc_auc_score(y_test, p),
                 "Brier score": brier_score_loss(y_test, p), "log loss": log_loss(y_test, p)})
pd.DataFrame(rows).set_index("model")
"""),
    md("Performance per cutoff hour: the later in the day, the easier the call."),
    code("""
by_hour = pd.DataFrame({
    name: test.assign(p=p).groupby("cutoff_hour").apply(lambda g: roc_auc_score(g["completed"], g["p"]), include_groups=False)
    for name, p in preds.items()
})
by_hour.index.name = "cutoff hour"
by_hour.round(3)
"""),
    md("""
## Are the probabilities trustworthy? (calibration)

For a reminder rule like "notify if P(complete) < 30%" the probabilities themselves must be
right, not just the ranking. Points on the diagonal mean "when the model says 30%, it
happens 30% of the time".
"""),
    code("""
fig, ax = plt.subplots(figsize=(5.2, 5))
ax.plot([0, 1], [0, 1], color=REFERENCE, linewidth=1, label="perfect calibration")
for color, name in zip(SERIES[:2], ["logistic regression", "gradient boosting"]):
    frac, mean_pred = calibration_curve(y_test, preds[name], n_bins=10, strategy="quantile")
    ax.plot(mean_pred, frac, color=color, marker="o", markersize=5, label=name)
ax.set_xlabel("predicted probability")
ax.set_ylabel("observed frequency")
ax.set_xlim(0, 1); ax.set_ylim(0, 1)
ax.grid(axis="x")
ax.legend(loc="upper left")
ax.set_title("Calibration on the test period", pad=18)
subtitle(ax, "Deciles of predicted probability")
plt.show()
"""),
    md("## What drives the prediction? (permutation importance on the test set)"),
    code("""
gb = models["gradient boosting"][0]
imp = permutation_importance(gb, X_test[FEATURES], y_test, scoring="roc_auc", n_repeats=5, random_state=0)
importance = pd.Series(imp.importances_mean, index=FEATURES).sort_values()

fig, ax = plt.subplots(figsize=(7, 3.8))
ax.barh(importance.index, importance.values, height=0.6, color=SERIES[0])
ax.grid(axis="x"); ax.grid(axis="y", visible=False)
ax.set_xlabel("drop in ROC AUC when the feature is shuffled")
ax.set_title("Feature importance (gradient boosting)", pad=18)
subtitle(ax, "Permutation importance, 5 repeats")
plt.show()
importance.sort_values(ascending=False).rename("AUC drop").to_frame().T
"""),
    md("""
## What the app would see at 19:00

Real rows from the test period (not hand-made profiles: invented combinations easily fall where
the model has almost no data), picked across the range of predicted probabilities, next to
what actually happened.
"""),
    code("""
at19 = test[test["cutoff_hour"] == 19].copy()
at19["P(reaches 100)"] = gb.predict_proba(at19[FEATURES].astype(float))[:, 1]
picks = pd.concat([at19.iloc[[int((at19["P(reaches 100)"] - q).abs().argmin())]] for q in (0.05, 0.25, 0.5, 0.75, 0.95)])
picks[["remaining", "sets_before", "hours_since_last_set", "streak_before", "rate_7d", "is_weekend",
       "P(reaches 100)", "completed"]].reset_index(drop=True)
"""),
    md("""
## Takeaways

* Beyond the obvious (time of day and reps missing), **recent behaviour** - hours since the
  last set, the last 7 days, the current streak - adds real signal, and friends' activity a bit more.
* The models are evaluated on *future* days only, and the logistic model is close to the
  boosted one: a simple, explainable model would be enough to drive an evening reminder.
* Predictive features are **not levers**: the model says who is likely to fail, not what would
  make them succeed. Whether a reminder actually helps is a causal question, see notebook 04.
* In production the rule would be "at 19:00, remind users with P(complete) below a
  threshold", with the threshold chosen by an experiment (reminding too much has a cost).

Next: [04 · Do notifications work?](04_notification_effect.ipynb)
"""),
]

# ---------------------------------------------------------------------------------------
NB04 = [
    md("""
# 04 · Do friends' notifications make you train? (causal inference)

Every set a user logs pushes a notification to their friends. Does receiving one make you
more likely to do a set soon after?

**The naive answer is biased.** Friends are often active at the same time for reasons that have
nothing to do with notifications: they train together, share a schedule, have a good day as a
group. Then "I got a notification" and "I did a set" happen together even if notifications do
nothing.

```
            together session / shared routine
               ↙                      ↘
   friend logs a set  ──notification──▶  you log a set
```

The simulator plants exactly this confounder (`together_prob`, `together_boost`) plus a true
effect: while a delivered notification is less than an hour old, your set rate is multiplied
by **1.5**.

**The fix is an experiment.** In a *micro-randomized trial* each notification is delivered with
probability 50%. Whether a particular notification reached you is then random, independent of
the together-session, so comparing delivered vs. not-delivered is unbiased.
"""),
    code(SETUP + """
from costuel_analysis.causal import build_slots, naive_effect, notification_outcomes, proximal_effect, randomized_effect"""),
    md("""
## Part 1 · Can we recover the planted effect?

Two designs (observational: every notification delivered; randomized: 50% delivered) crossed
with two truths: a real effect of **1.5**, and **no effect** at all (a placebo check).
The target is the planted parameter: the set-rate ratio *while notified vs. not notified*.

* **Naive**: 10-minute slots in the hour after a notification vs. all other slots, adjusting
  for user and hour of day (Poisson model, SEs clustered by user).
* **Randomized**: only slots where a friend *did* log a set in the last hour; the comparison is
  delivered vs. not delivered, stratified by how many friend sets there were.
"""),
    code("""
scenarios = {
    "observational · effect 1.5": SimConfig(),
    "observational · no effect": SimConfig(notification_effect=0.0),
    "randomized 50% · effect 1.5": SimConfig(delivery_prob=0.5),
    "randomized 50% · no effect": SimConfig(delivery_prob=0.5, notification_effect=0.0),
}
sims, rows = {}, []
for name, c in scenarios.items():
    r = sims[name] = simulate(c)
    slots = build_slots(r.entries, r.notifications, r.daily)
    est = {"naive": naive_effect(slots)}
    if c.delivery_prob < 1:
        est["randomized"] = randomized_effect(slots)
    for estimator, e in est.items():
        rows.append({"scenario": name, "estimator": estimator, "truth": c.true_hazard_ratio,
                     "estimate": e["hazard_ratio"], "ci_low": e["ci_low"], "ci_high": e["ci_high"]})
results = pd.DataFrame(rows)
results["truth inside CI"] = (results["ci_low"] <= results["truth"]) & (results["truth"] <= results["ci_high"])
results.set_index(["scenario", "estimator"]).round(3)
"""),
    code("""
order = list(scenarios)
fig, ax = plt.subplots(figsize=(8, 4))
for color, (k, est) in zip(SERIES, enumerate(["naive", "randomized"])):
    sub = results[results["estimator"] == est]
    y = np.array([order.index(s) for s in sub["scenario"]]) + (k - 0.5) * 0.24
    ax.errorbar(sub["estimate"], y, xerr=[sub["estimate"] - sub["ci_low"], sub["ci_high"] - sub["estimate"]],
                fmt="o", color=color, markersize=6, capsize=0, elinewidth=2, label=est)
for i, s in enumerate(order):
    t = scenarios[s].true_hazard_ratio
    ax.plot([t, t], [i - 0.4, i + 0.4], color=REFERENCE, linewidth=1.5)
ax.plot([], [], color=REFERENCE, linewidth=1.5, label="truth")
ax.set_yticks(range(len(order)), order)
ax.invert_yaxis()
ax.grid(axis="y", visible=False); ax.grid(axis="x")
ax.set_xlabel("set-rate ratio while notified (1 = no effect)")
ax.legend(loc="lower right")
ax.set_title("Estimated effect of a friend's notification", pad=18)
subtitle(ax, "Point estimate and 95% CI · grey ticks = simulation truth")
plt.show()
"""),
    md("""
### Reading the results

* **Naive** estimates are too high in every world, and in the placebo worlds they "find" an
  effect of 15-20% that does not exist: pure confounding by the together-sessions.
* The **randomized** estimate is centred on 1 in the placebo world and lands on the truth when
  the effect is real.

One subtlety: across repeated runs the randomized hazard ratio sits slightly *below* 1.5. This
is a known property of hazard ratios (Hernán, 2010, *The hazards of hazard ratios*): notified
users reach 100 sooner and leave the "still at risk" pool, so the exposed slots that remain are
enriched with slower days. Removing the goal (nobody ever leaves the pool) makes it disappear:
"""),
    code("""
rows = []
for goal in (100, 100_000):
    for seed in range(3):
        r = simulate(SimConfig(delivery_prob=0.5, goal=goal, seed=100 + seed, n_days=60))
        rows.append({"goal": "100 (people finish)" if goal == 100 else "unreachable (nobody leaves)",
                     "seed": seed, "randomized HR": randomized_effect(build_slots(r.entries, r.notifications, r.daily))["hazard_ratio"]})
depletion = pd.DataFrame(rows).groupby("goal")["randomized HR"].agg(["mean", "min", "max"])
depletion["truth"] = 1.5
depletion
"""),
    md("""
## Part 2 · What is one more notification worth?

The product question is slightly different: *if the app delivers this notification, how many
more sets happen in the next hour?* That is the **proximal (excursion) effect** that
micro-randomized trials target (Boruvka et al., 2018). The unit is a notification, and we only
condition on what was known *before* randomizing it (the recipient was still below 100).

It is **not** the 1.5 above, by design: friends log sets in bursts, so most notifications reach
someone who has already been notified in the last hour, and an extra one adds little.
"""),
    code("""
r = sims["randomized 50% · effect 1.5"]
outcomes = notification_outcomes(r.entries, r.notifications, r.daily)
available = outcomes[outcomes["available"]]
print(f"{len(available):,} notifications to users still below 100 · "
      f"{(available['prior_notifications'] > 0).mean():.0%} arrived within an hour of another one")

rows = []
for label, subset in [("all notifications", outcomes),
                      ("first in the hour", outcomes[outcomes["prior_notifications"] == 0]),
                      ("after 1 other", outcomes[outcomes["prior_notifications"] == 1]),
                      ("after 2+ others", outcomes[outcomes["prior_notifications"] >= 2])]:
    p = proximal_effect(subset)
    rows.append({"notifications": label, "n": p["n_notifications"], "sets next hour (delivered)": p["sets_next_hour_delivered"],
                 "sets next hour (not delivered)": p["sets_next_hour_not_delivered"],
                 "rate ratio": p["rate_ratio"], "ci_low": p["ci_low"], "ci_high": p["ci_high"]})
marginal = pd.DataFrame(rows).set_index("notifications")
placebo = proximal_effect(notification_outcomes(*(lambda s: (s.entries, s.notifications, s.daily))(sims["randomized 50% · no effect"])))
print(f"placebo world (no effect): rate ratio {placebo['rate_ratio']:.3f} [{placebo['ci_low']:.3f}, {placebo['ci_high']:.3f}]")
marginal.round(3)
"""),
    code("""
sub = marginal.iloc[1:]
fig, ax = plt.subplots(figsize=(7, 3))
y = np.arange(len(sub))
ax.errorbar(sub["rate ratio"], y, xerr=[sub["rate ratio"] - sub["ci_low"], sub["ci_high"] - sub["rate ratio"]],
            fmt="o", color=SERIES[0], markersize=6, capsize=0, elinewidth=2)
ax.axvline(1, color=REFERENCE, linewidth=1)
for yi, (_, row) in zip(y, sub.iterrows()):
    ax.annotate(f"+{row['rate ratio'] - 1:.0%}", (row["ci_high"], yi), xytext=(6, 0), textcoords="offset points",
                va="center", color=INK_SECONDARY)
ax.set_yticks(y, sub.index)
ax.invert_yaxis()
ax.grid(axis="y", visible=False); ax.grid(axis="x")
ax.set_xlabel("sets in the next hour, delivered / not delivered")
ax.set_title("Diminishing returns of friend notifications", pad=18)
subtitle(ax, "Proximal effect with 95% CI, by notifications already received in the past hour")
plt.show()
"""),
    md("""
## How long would the real experiment need to run?

Same randomized design, 60 users, different durations: width of the 95% CI of the proximal
effect of a notification (all notifications).
"""),
    code("""
rows = []
for days in (14, 30, 60, 120):
    r = simulate(SimConfig(delivery_prob=0.5, n_days=days, seed=21))
    p = proximal_effect(notification_outcomes(r.entries, r.notifications, r.daily))
    rows.append({"days": days, "notifications": p["n_notifications"], "effect": p["rate_ratio"],
                 "ci_low": p["ci_low"], "ci_high": p["ci_high"], "ci width": p["ci_high"] - p["ci_low"]})
pd.DataFrame(rows).set_index("days")
"""),
    md("""
## Takeaways and what to do in the real app

1. **Don't trust the observational comparison.** Here it overstates the effect and even
   invents one when there is none.
2. **Randomize delivery.** Holding back a random share of friend notifications is cheap and
   turns every notification into a small experiment; the placebo checks show the design is valid.
3. **Name the estimand.** "Rate while notified" (1.5), "one more notification" (about +10%) and
   "the first notification of the hour" (about +25%) are three different, all correct, answers.
4. **Throttle.** The first notification of the hour carries most of the value; later ones add
   little and cost attention. A per-recipient cap (e.g. one friend notification per hour) is the
   obvious product change - and it should itself be tested.
5. **Log the decision.** The app must store each notification with its `delivered` flag (today it
   only sends them). Then this notebook runs unchanged on real data (`export_analysis_data`).
"""),
]

NOTEBOOKS = {
    "01": ("01_simulation_and_eda.ipynb", NB01),
    "02": ("02_streak_survival.ipynb", NB02),
    "03": ("03_completion_model.ipynb", NB03),
    "04": ("04_notification_effect.ipynb", NB04),
}


def build(key: str) -> Path:
    filename, cells = NOTEBOOKS[key]
    nb = nbformat.v4.new_notebook(cells=cells)
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    OUT.mkdir(exist_ok=True)
    NotebookClient(nb, timeout=1800, kernel_name="python3", resources={"metadata": {"path": str(OUT)}}).execute()
    path = OUT / filename
    nbformat.write(nb, path)
    return path


if __name__ == "__main__":
    keys = sys.argv[1:] or list(NOTEBOOKS)
    for key in keys:
        print("built", build(key))
