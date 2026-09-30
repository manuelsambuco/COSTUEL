# COSTUEL · data science

Behavioural analysis of a habit app (100 push-ups a day, with friends), built around a
**simulator with a known ground truth**: every method is shown to recover the effect planted
in the data before anything is claimed about it.

The real usage data is a handful of friends - too small and too private to publish - so the
notebooks run on simulated data that reproduces the app's mechanics. The same code runs on the
real, pseudonymised export of the app (`python manage.py export_analysis_data`).

## Notebooks

| | Question | Methods | Main result |
|---|---|---|---|
| [01](notebooks/01_simulation_and_eda.ipynb) | What does the data look like? | simulation design, EDA | Completion ~50% decaying to ~36%; strong weekly and daily rhythms; very heterogeneous users |
| [02](notebooks/02_streak_survival.ipynb) | How long do streaks last, and do they build habits? | Kaplan-Meier, Cox PH (clustered SEs), life tables | +10 pp of early commitment ≈ −16% daily risk of breaking. A falling hazard is **not** proof of habit: it also appears with no habit effect (selection / frailty) |
| [03](notebooks/03_completion_model.ipynb) | Will a user reach 100 today? | time-split validation, logistic regression, gradient boosting with monotonic constraints, calibration, permutation importance | ROC AUC 0.94 on future days (0.91 for a 2-feature baseline), well calibrated |
| [04](notebooks/04_notification_effect.ipynb) | Do friends' notifications cause more training? (now running live in the app) | confounding, micro-randomized trial, Poisson GLM with fixed effects, placebo checks | Naive estimate biased (+19% effect when the truth is zero); randomized design recovers the planted 1.5×. Diminishing returns: +25% for the first notification of the hour, +5% after two |

### Things worth noticing

* **Validated against the truth.** Each estimator is run in worlds where the answer is known,
  including placebo worlds with no effect at all.
* **Estimands, not just estimators.** Notebook 04 separates three correct but different
  answers ("rate while notified", "one more notification", "first notification of the hour")
  and shows why a hazard ratio is diluted when treated users leave the risk set
  (depletion of susceptibles).
* **Leakage-aware ML.** Features only use information available before each cutoff, the split
  is by time, and hand-made "what if" profiles are avoided because they fall where the model has
  no data.

## Layout

```
costuel_analysis/
  simulate.py   generative model with planted effects (SimConfig = the ground truth)
  features.py   panel, streak spells, per-cutoff snapshots (same code for real and simulated data)
  causal.py     slot-level hazard data, naive / randomized / proximal effect estimators
  plotting.py   shared chart style (CVD-validated palette)
  io.py         save / load the CSV tables
notebooks/      executed notebooks (outputs included)
tests/          pytest suite, including "the randomized estimator recovers the truth"
build_notebooks.py   regenerates and executes all notebooks
```

## Running it

```bash
python -m venv .venv-analysis
.venv-analysis\Scripts\pip install -r requirements.txt   # Windows; use bin/ on macOS/Linux
.venv-analysis\Scripts\python -m pytest
.venv-analysis\Scripts\python build_notebooks.py          # ~5 minutes
```

On real data, from the project root:

```bash
python manage.py export_analysis_data        # writes analysis/data/real/ (git-ignored)
```

then load it with `costuel_analysis.io.load_tables("data/real")` instead of `simulate()`.

### The live experiment

The app runs the micro-randomized trial of notebook 04 for real: every friend-progress
notification is delivered with probability `NOTIFY_DELIVERY_PROB` (default 0.8, i.e. 20% held
back at random) and every decision is logged (`NotificationEvent`: sender, recipient, time,
delivered, devices, probability). The export writes it to `notifications.csv`, which
`notification_outcomes` / `proximal_effect` accept unchanged; recipients without a subscribed
device are excluded automatically. One caveat the simulation does not have: users also see
friends' progress when they open the app, an exposure channel that is not randomized.

## References

* Hernán, M. A. (2010). The hazards of hazard ratios. *Epidemiology*, 21(1), 13-15.
* Boruvka, A., Almirall, D., Witkiewitz, K., & Murphy, S. A. (2018). Assessing time-varying
  causal effect moderation in mobile health. *JASA*, 113(523), 1112-1121.
* Klasnja, P. et al. (2015). Microrandomized trials: an experimental design for developing
  just-in-time adaptive interventions. *Health Psychology*, 34(S), 1220-1228.
