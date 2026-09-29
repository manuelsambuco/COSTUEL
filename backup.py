"""Salva una copia dei dati online (database Neon) nella cartella backups/.

Uso, dalla cartella COSTUEL con l'ambiente Python attivo:

    python backup.py

Ti chiede la stringa di connessione di Neon e crea backups/backup-AAAA-MM-GG.json.
Il file contiene dati personali: non caricarlo su GitHub (è già escluso).
"""

import getpass
import os
import sys
from datetime import datetime
from pathlib import Path

EXCLUDE = ["contenttypes", "auth.permission", "sessions", "admin.logentry"]


def main():
    url = os.environ.get("DATABASE_URL") or getpass.getpass(
        "Incolla la stringa di connessione di Neon (non viene mostrata) e premi Invio: "
    ).strip().strip('"')
    if "://" not in url:
        sys.exit("Stringa non valida: deve iniziare con postgresql://")
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django
    from django.core.management import call_command

    django.setup()

    folder = Path(__file__).resolve().parent / "backups"
    folder.mkdir(exist_ok=True)
    output = folder / f"backup-{datetime.now():%Y-%m-%d_%H%M}.json"
    print("Scarico i dati…")
    # Sempre UTF-8: con la codifica predefinita di Windows lettere come "ì" renderebbero
    # il file impossibile da ripristinare
    with open(output, "w", encoding="utf-8") as stream:
        call_command("dumpdata", natural_foreign=True, exclude=EXCLUDE, indent=1, stdout=stream)
    print(f"Fatto! Backup salvato in: {output} ({output.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
