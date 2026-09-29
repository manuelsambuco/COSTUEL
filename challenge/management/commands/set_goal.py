from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from challenge.models import DailyLog, Profile


class Command(BaseCommand):
    help = (
        "Cambia l'obiettivo giornaliero di tutti gli utenti o di uno solo. "
        "Vale da oggi: i giorni passati restano con l'obiettivo che avevano."
    )

    def add_arguments(self, parser):
        parser.add_argument("obiettivo", type=int, help="Nuovo obiettivo giornaliero, es. 150")
        parser.add_argument("--utente", help="Username di un solo utente (senza: tutti)")
        parser.add_argument(
            "--da-domani", action="store_true",
            help="Non toccare la giornata di oggi (chi ha già iniziato continua con il vecchio obiettivo)",
        )

    def handle(self, *args, **options):
        goal = options["obiettivo"]
        if not 1 <= goal <= 10000:
            raise CommandError("L'obiettivo deve essere tra 1 e 10000.")

        users = get_user_model().objects.filter(is_active=True)
        if options["utente"]:
            users = users.filter(username__iexact=options["utente"])
            if not users.exists():
                raise CommandError(f"Nessun utente con username “{options['utente']}”.")

        with transaction.atomic():
            for user in users:
                Profile.objects.update_or_create(user=user, defaults={"daily_goal": goal})
            today_updated = 0
            if not options["da_domani"]:
                today_updated = DailyLog.objects.filter(user__in=users, day=timezone.localdate()).update(goal=goal)

        count = users.count()
        self.stdout.write(self.style.SUCCESS(
            f"Obiettivo impostato a {goal} per {count} utent{'e' if count == 1 else 'i'}."
        ))
        if options["da_domani"]:
            self.stdout.write("La giornata di oggi resta con il vecchio obiettivo: il nuovo vale da domani.")
        else:
            self.stdout.write(f"Aggiornata anche la giornata di oggi ({today_updated} già iniziate).")
        self.stdout.write(
            "Ricorda: i nuovi iscritti partono da DEFAULT_DAILY_GOAL (variabile d'ambiente, oggi "
            "100 se non impostata). Cambiala anche su Render se vuoi che valga per loro."
        )
