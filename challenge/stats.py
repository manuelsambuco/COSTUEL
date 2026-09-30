"""Statistiche: classifiche per periodo, serie di giorni consecutivi, calendario mensile."""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import F, Q, Sum
from django.utils import timezone

from .models import DailyLog, Profile
from .services import display_name, get_profile

MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
          "agosto", "settembre", "ottobre", "novembre", "dicembre"]
WEEKDAYS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


@dataclass
class Period:
    key: str
    label: str
    start: date
    end: date  # incluso
    today: date

    @property
    def days_elapsed(self):
        """Giorni già iniziati del periodo (oggi compreso)."""
        return max((min(self.end, self.today) - self.start).days + 1, 0)

    @property
    def days_total(self):
        return (self.end - self.start).days + 1


PERIODS = [("oggi", "Oggi"), ("settimana", "Settimana"), ("mese", "Mese")]


def get_period(key, today=None):
    today = today or timezone.localdate()
    if key == "settimana":
        start = today - timedelta(days=today.weekday())  # lunedì
        return Period(key, "Questa settimana", start, start + timedelta(days=6), today)
    if key == "mese":
        start = today.replace(day=1)
        end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return Period(key, f"{MONTHS[today.month - 1].capitalize()} {today.year}", start, end, today)
    return Period("oggi", "Oggi", today, today, today)


def completed_q():
    return Q(total__gte=F("goal"))


def leaderboard(users, period, me=None, goals_by_day=None):
    """Classifica per il periodo: piegamenti contati, giorni completati, % sul massimo possibile.

    Ogni giorno conta fino a un obiettivo:
    * amici (``goals_by_day`` assente): la sfida base di ciascuno (100); quello in più finisce
      in ``extra`` e si mostra come badge "+50", così il confronto resta alla pari;
    * gruppo: l'obiettivo del gruppo in vigore quel giorno (``goals_by_day[giorno]``).
    """
    users = list(users)
    base_goals = dict(Profile.objects.filter(user__in=users).values_list("user_id", "daily_goal"))
    agg = {u.pk: {"reps": 0, "done": 0, "extra": 0, "today_total": 0} for u in users}
    logs = DailyLog.objects.filter(user__in=users, day__gte=period.start, day__lte=period.end)
    for user_id, day, total, day_goal in logs.values_list("user_id", "day", "total", "goal"):
        goal = goals_by_day[day] if goals_by_day else day_goal
        a = agg[user_id]
        a["reps"] += min(total, goal)
        a["extra"] += max(total - goal, 0)
        a["done"] += total >= goal
        if day == period.today:
            a["today_total"] = total

    elapsed = [period.start + timedelta(days=i) for i in range(period.days_elapsed)]
    rows = []
    for user in users:
        a = agg[user.pk]
        if goals_by_day:
            goal = goals_by_day.get(period.today, 0)
            possible = sum(goals_by_day[d] for d in elapsed)
        else:
            goal = base_goals.get(user.pk) or get_profile(user).daily_goal
            possible = goal * len(elapsed)
        rows.append({
            "user_id": user.pk,
            "name": display_name(user),
            "username": user.username,
            "is_me": me is not None and user.pk == me.pk,
            "reps": a["reps"],
            "done": a["done"],
            "extra": 0 if goals_by_day else a["extra"],
            "goal": goal,
            "possible": possible,
            "percent": min(round(a["reps"] * 100 / possible), 100) if possible else 0,
            "completed": period.key == "oggi" and a["today_total"] >= goal,
        })
    rows.sort(key=lambda r: (-r["reps"], -r["done"], r["name"].lower()))
    # Posizioni con ex aequo (stessi piegamenti = stessa posizione)
    for i, row in enumerate(rows):
        same = i and rows[i - 1]["reps"] == row["reps"] and rows[i - 1]["done"] == row["done"]
        row["rank"] = rows[i - 1]["rank"] if same else i + 1
    return rows


def streaks(user, today=None):
    """(serie attuale, record) di giorni consecutivi con obiettivo raggiunto.

    La serie attuale conta anche se oggi non hai ancora finito: si interrompe solo
    se ieri non era completo.
    """
    today = today or timezone.localdate()
    days = list(
        DailyLog.objects.filter(completed_q(), user=user, day__lte=today)
        .order_by("day")
        .values_list("day", flat=True)
    )
    best = run = 0
    prev = None
    for d in days:
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    done = set(days)
    current = 0
    cursor = today if today in done else today - timedelta(days=1)
    while cursor in done:
        current += 1
        cursor -= timedelta(days=1)
    return current, best


def week_chart(user, today=None):
    """I 7 giorni della settimana corrente (lun-dom) con altezza della barra in %."""
    period = get_period("settimana", today)
    logs = {log.day: log for log in DailyLog.objects.filter(user=user, day__gte=period.start, day__lte=period.end)}
    goal = get_profile(user).daily_goal
    out = []
    for i in range(7):
        day = period.start + timedelta(days=i)
        log = logs.get(day)
        total = log.total if log else 0
        day_goal = log.goal if log else goal
        out.append({
            "label": WEEKDAYS[i],
            "day": day,
            "total": total,
            "goal": day_goal,
            "percent": min(round(total * 100 / day_goal), 100) if day_goal else 0,
            "completed": total >= day_goal,
            "is_today": day == period.today,
            "future": day > period.today,
        })
    return out


def month_calendar(user, year, month, today=None):
    """Griglia del mese (settimane da lunedì) più i totali del mese."""
    today = today or timezone.localdate()
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    logs = {log.day: log for log in DailyLog.objects.filter(user=user, day__gte=first, day__lte=last)}
    goal = get_profile(user).daily_goal

    cells = [None] * first.weekday()  # caselle vuote prima del giorno 1
    for n in range(1, last.day + 1):
        day = date(year, month, n)
        log = logs.get(day)
        total = log.total if log else 0
        day_goal = log.goal if log else goal
        cells.append({
            "day": day,
            "total": total,
            "percent": min(round(total * 100 / day_goal), 100) if day_goal else 0,
            "completed": total >= day_goal,
            "is_today": day == today,
            "future": day > today,
        })
    while len(cells) % 7:
        cells.append(None)

    elapsed = [c for c in cells if c and not c["future"]]
    reps = sum(c["total"] for c in elapsed)
    done = sum(1 for c in elapsed if c["completed"])
    return {
        "title": f"{MONTHS[month - 1].capitalize()} {year}",
        "weeks": [cells[i:i + 7] for i in range(0, len(cells), 7)],
        "weekdays": WEEKDAYS,
        "reps": reps,
        "done": done,
        "days_elapsed": len(elapsed),
        "rate": round(done * 100 / len(elapsed)) if elapsed else 0,
        "prev": (first - timedelta(days=1)).strftime("%Y-%m"),
        "next": (last + timedelta(days=1)).strftime("%Y-%m") if last < today else None,
    }


def lifetime_total(user):
    return DailyLog.objects.filter(user=user).aggregate(s=Sum("total"))["s"] or 0
