"""Logica della sfida: separata dalle view così è facile da testare e riusare."""

from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import DailyLog, Profile, PushupEntry
from .social import challenge_members, user_group_goals

MAX_REPS_PER_ENTRY = 500


def get_profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def display_name(user):
    return user.first_name or user.username


def get_today_log(user, create=True):
    """Il DailyLog di oggi. Alla creazione copia l'obiettivo attuale del profilo."""
    today = timezone.localdate()
    if not create:
        return DailyLog.objects.filter(user=user, day=today).first()
    log, _ = DailyLog.objects.get_or_create(
        user=user, day=today, defaults={"goal": get_profile(user).daily_goal}
    )
    return log


def daily_cap(log, group_goals):
    """Fin dove contano i piegamenti oggi: la sfida base o l'obiettivo di gruppo più alto."""
    return max([log.goal, *(goal for _, goal in group_goals)])


@dataclass
class AddResult:
    log: DailyLog
    requested: int
    added: int  # quanti sono stati davvero contati
    just_completed: bool  # questa serie ha fatto raggiungere la sfida base (100)
    entry: PushupEntry | None = None  # la serie salvata (None se non è stato contato nulla)
    prev_total: int = 0
    cap: int = 0  # limite di oggi: max(sfida base, obiettivi dei gruppi)
    # [(gruppo, obiettivo)] con obiettivo diverso dalla sfida base raggiunti con questa serie
    groups_completed: list = field(default_factory=list)
    # [(gruppo, obiettivo)] sopra la sfida base per cui questa serie contava ancora
    extra_groups: list = field(default_factory=list)


def add_pushups(user, reps):
    """Aggiunge `reps` piegamenti a oggi.

    Contano fino alla sfida base (100) o, se l'utente è in gruppi con un obiettivo più alto,
    fino al più alto di questi. Quelli oltre non vengono contati.
    """
    if not 0 < reps <= MAX_REPS_PER_ENTRY:
        raise ValueError(f"Numero di piegamenti non valido: {reps}")

    with transaction.atomic():
        log = get_today_log(user)
        # Blocca la riga: due tap veloci non possono superare il limite
        log = DailyLog.objects.select_for_update().get(pk=log.pk)
        group_goals = user_group_goals(user)
        cap = daily_cap(log, group_goals)
        prev = log.total
        added = min(reps, max(cap - prev, 0))
        entry = None
        if added:
            entry = PushupEntry.objects.create(log=log, reps=added)
            log.total += added
            log.save(update_fields=["total"])
    above_base = [(g, goal) for g, goal in group_goals if goal > log.goal]
    return AddResult(
        log=log, requested=reps, added=added, entry=entry, prev_total=prev, cap=cap,
        just_completed=added > 0 and prev < log.goal <= log.total,
        groups_completed=[
            (g, goal) for g, goal in group_goals if goal != log.goal and added and prev < goal <= log.total
        ],
        extra_groups=[(g, goal) for g, goal in above_base if added and goal > prev],
    )


def group_progress(log, group_goals):
    """Le barre "Sfide di gruppo" della home: solo i gruppi con un obiettivo sopra la sfida base."""
    rows = []
    for group, goal in group_goals:
        if goal <= log.goal:
            continue
        counted = min(log.total, goal)
        rows.append({
            "pk": group.pk, "name": group.name, "goal": goal, "total": counted,
            "percent": min(round(counted * 100 / goal), 100), "completed": counted >= goal,
        })
    return rows


def undo_last(user):
    """Annulla l'ultima serie di oggi. Ritorna la serie annullata (o None)."""
    with transaction.atomic():
        log = get_today_log(user, create=False)
        if not log:
            return None
        log = DailyLog.objects.select_for_update().get(pk=log.pk)
        entry = log.entries.first()
        if not entry:
            return None
        log.total -= entry.reps
        log.save(update_fields=["total"])
        entry.delete()
    return entry


def today_board(user):
    """Classifica di oggi tra te, i tuoi amici e i membri dei tuoi gruppi (tu compreso)."""
    people = [user, *challenge_members(user)]
    ids = [p.pk for p in people]
    logs = {log.user_id: log for log in DailyLog.objects.filter(day=timezone.localdate(), user__in=ids)}
    # una sola query per gli obiettivi di chi oggi non ha ancora iniziato
    base_goals = dict(Profile.objects.filter(user__in=ids).values_list("user_id", "daily_goal"))
    rows = []
    for person in people:
        log = logs.get(person.pk)
        goal = log.goal if log else (base_goals.get(person.pk) or get_profile(person).daily_goal)
        total = log.total if log else 0
        counted = min(total, goal)  # qui tutti sono misurati sulla sfida base...
        rows.append({
            "name": display_name(person),
            "is_me": person.pk == user.pk,
            "total": counted,
            "extra": max(total - goal, 0),  # ...e quello in più è un badge "+50"
            "goal": goal,
            "percent": min(round(counted * 100 / goal), 100) if goal else 100,
            "completed": total >= goal,
        })
    # Chi ha fatto di più in cima; stessa percentuale = stessa posizione
    rows.sort(key=lambda r: (-r["percent"], not r["is_me"], r["name"].lower()))
    for i, row in enumerate(rows):
        row["rank"] = rows[i - 1]["rank"] if i and rows[i - 1]["percent"] == row["percent"] else i + 1
    return rows


def collapse_rows(rows, limit):
    """Liste lunghe: restano visibili le prime ``limit`` righe e la tua; le altre si aprono a richiesta.

    Imposta ``more`` (riga nascosta finché non si apre la lista) e ``gap`` (separatore prima
    della tua riga quando sei più in basso). Restituisce quante righe sono nascoste.
    """
    for i, row in enumerate(rows):
        me = bool(row.get("is_me"))
        row["more"] = i >= limit and not me
        row["gap"] = i > limit and me
    return sum(row["more"] for row in rows)


def last_days(user, days=7):
    """Gli ultimi `days` giorni (oggi incluso), dal più vecchio al più recente."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    logs = {log.day: log for log in DailyLog.objects.filter(user=user, day__gte=start)}
    goal = get_profile(user).daily_goal
    out = []
    for i in range(days):
        day = start + timedelta(days=i)
        log = logs.get(day)
        total = log.total if log else 0
        day_goal = log.goal if log else goal
        out.append({
            "day": day,
            "total": total,
            "goal": day_goal,
            "completed": total >= day_goal,
            "is_today": day == today,
        })
    return out
