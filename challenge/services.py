"""Logica della sfida: separata dalle view così è facile da testare e riusare."""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import DailyLog, Profile, PushupEntry
from .social import challenge_members

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


@dataclass
class AddResult:
    log: DailyLog
    requested: int
    added: int  # quanti sono stati davvero contati
    just_completed: bool  # questa serie ha fatto raggiungere l'obiettivo
    entry: PushupEntry | None = None  # la serie salvata (None se non è stato contato nulla)


def add_pushups(user, reps):
    """Aggiunge `reps` piegamenti a oggi. Quelli oltre l'obiettivo non vengono contati."""
    if not 0 < reps <= MAX_REPS_PER_ENTRY:
        raise ValueError(f"Numero di piegamenti non valido: {reps}")

    with transaction.atomic():
        log = get_today_log(user)
        # Blocca la riga: due tap veloci non possono superare l'obiettivo
        log = DailyLog.objects.select_for_update().get(pk=log.pk)
        added = min(reps, log.remaining)
        entry = None
        if added:
            entry = PushupEntry.objects.create(log=log, reps=added)
            log.total += added
            log.save(update_fields=["total"])
    return AddResult(
        log=log, requested=reps, added=added, just_completed=added > 0 and log.completed, entry=entry
    )


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
    """Progressi di oggi di amici e membri dei gruppi dell'utente."""
    others = list(challenge_members(user))
    logs = {
        log.user_id: log
        for log in DailyLog.objects.filter(day=timezone.localdate(), user__in=[user, *others])
    }
    rows = []
    for other in others:
        log = logs.get(other.pk)
        goal = log.goal if log else get_profile(other).daily_goal
        total = log.total if log else 0
        rows.append({
            "name": display_name(other),
            "total": total,
            "goal": goal,
            "percent": min(round(total * 100 / goal), 100) if goal else 100,
            "completed": total >= goal,
        })
    # Chi ha fatto di più in cima
    rows.sort(key=lambda r: (-r["percent"], r["name"].lower()))
    return rows


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
