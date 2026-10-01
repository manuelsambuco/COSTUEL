"""Save / load the four observable tables as CSV (same layout for simulated and real data)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

TABLES = ("users", "edges", "entries", "daily", "notifications")


def save_tables(tables: dict[str, pd.DataFrame], folder: str | Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(folder / f"{name}.csv", index=False)
    return folder


def load_tables(folder: str | Path) -> dict[str, pd.DataFrame]:
    """Load whatever tables exist. The app export has no ``notifications`` table (yet)."""
    folder = Path(folder)
    out = {}
    for name in TABLES:
        path = folder / f"{name}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        for col in ("day", "ts"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        if "day" in df.columns:
            df["day"] = df["day"].dt.normalize()
        out[name] = df
    return out
