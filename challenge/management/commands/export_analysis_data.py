"""Esporta i dati dell'app per l'analisi (cartella analysis/), in forma pseudonimizzata.

    python manage.py export_analysis_data                  # -> analysis/data/real/
    python manage.py export_analysis_data --out altra/cartella

Stesso formato del simulatore (users, edges, entries, daily, notifications): i notebook girano uguali.
Niente nomi né username: ogni utente diventa un codice derivato da SECRET_KEY, stabile tra
un'esportazione e l'altra ma non riconducibile alla persona senza la chiave.
I file contengono comunque dati personali: la cartella è esclusa da git.
"""

import csv
import hashlib
import hmac
from itertools import permutations
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from challenge.models import DailyLog, Friendship, GroupMembership, NotificationEvent, PushupEntry


def pseudonym(user_id: int) -> str:
    digest = hmac.new(settings.SECRET_KEY.encode(), f"user:{user_id}".encode(), hashlib.sha256)
    return digest.hexdigest()[:10]


class Command(BaseCommand):
    help = "Esporta utenti, legami, serie e giornate (pseudonimizzati) per i notebook di analisi."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(settings.BASE_DIR) / "analysis" / "data" / "real"))

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        users = list(get_user_model().objects.filter(is_active=True).values_list("pk", flat=True))
        ids = {pk: pseudonym(pk) for pk in users}

        # Chi riceve le notifiche di chi: amicizie accettate + membri degli stessi gruppi
        edges = set()
        for a, b in Friendship.objects.filter(status=Friendship.ACCEPTED).values_list("from_user_id", "to_user_id"):
            edges.update({(a, b), (b, a)})
        by_group = {}
        for group_id, user_id in GroupMembership.objects.values_list("group_id", "user_id"):
            by_group.setdefault(group_id, []).append(user_id)
        for members in by_group.values():
            edges.update(permutations(members, 2))
        edges = sorted((ids[a], ids[b]) for a, b in edges if a in ids and b in ids)

        def write(name, header, rows):
            with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(header)
                w.writerows(rows)
            return len(rows)

        counts = {
            "users": write("users", ["user_id"], [[ids[pk]] for pk in users]),
            "edges": write("edges", ["user_id", "friend_id"], edges),
            "entries": write("entries", ["user_id", "day", "ts", "reps"], [
                [ids[e.log.user_id], e.log.day.isoformat(),
                 timezone.localtime(e.created_at).replace(tzinfo=None).isoformat(sep=" "), e.reps]
                for e in PushupEntry.objects.select_related("log").filter(log__user_id__in=users).order_by("created_at")
            ]),
            "daily": write("daily", ["user_id", "day", "total", "goal"], [
                [ids[d.user_id], d.day.isoformat(), d.total, d.goal]
                for d in DailyLog.objects.filter(user_id__in=users).order_by("day", "user_id")
            ]),
            # Decisioni dell'esperimento: una riga per notifica, consegnata o trattenuta a caso
            "notifications": write("notifications", [
                "sender_id", "recipient_id", "ts", "delivered", "kind", "devices", "delivery_prob",
            ], [
                [ids[n.sender_id], ids[n.recipient_id],
                 timezone.localtime(n.created_at).replace(tzinfo=None).isoformat(sep=" "),
                 n.delivered, n.kind, n.devices, n.delivery_prob]
                for n in NotificationEvent.objects.filter(sender_id__in=users, recipient_id__in=users).order_by("created_at")
            ]),
        }
        summary = ", ".join(f"{n} {name}" for name, n in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Esportati in {out}: {summary}."))
