from cryptography.hazmat.primitives import serialization
from django.core.management.base import BaseCommand
from py_vapid import Vapid02
from py_vapid.utils import b64urlencode


class Command(BaseCommand):
    help = "Genera una coppia di chiavi VAPID per le notifiche push (da fare una sola volta)."

    def handle(self, *args, **options):
        vapid = Vapid02()
        vapid.generate_keys()
        private = b64urlencode(vapid.private_key.private_numbers().private_value.to_bytes(32, "big"))
        public = b64urlencode(
            vapid.public_key.public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )
        self.stdout.write("Copia queste righe nel file .env (e nelle variabili d'ambiente online):\n")
        self.stdout.write(f"VAPID_PUBLIC_KEY={public}")
        self.stdout.write(f"VAPID_PRIVATE_KEY={private}")
        self.stdout.write(
            self.style.WARNING("\nNon cambiarle più: se le cambi tutti dovranno riattivare le notifiche.")
        )
