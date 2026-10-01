"""Salva una copia dei dati online (database Neon) nella cartella backups/.

Il modo più semplice: doppio clic su backup.bat.
Si apre una finestra dove incollare la stringa di connessione di Neon; spuntando
"Ricorda su questo PC" viene salvata nel file .env (che non va mai su GitHub)
e dalla volta dopo il backup parte senza chiedere nulla.

Il file di backup contiene dati personali: non caricarlo su GitHub (è già escluso).
"""

import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
ENV_FILE = PROJECT / ".env"
ENV_KEY = "NEON_DATABASE_URL"
EXCLUDE = ["contenttypes", "auth.permission", "sessions", "admin.logentry"]


def clean(url):
    return (url or "").strip().strip('"').strip("'")


def read_saved_url():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{ENV_KEY}="):
                return clean(line.split("=", 1)[1])
    return ""


def save_url(url):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [line for line in lines if not line.startswith(f"{ENV_KEY}=")]
    lines.append(f"{ENV_KEY}={url}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def forget_url():
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        ENV_FILE.write_text("\n".join(l for l in lines if not l.startswith(f"{ENV_KEY}=")) + "\n", encoding="utf-8")


def build_dialog():
    """Finestra per incollare la stringa. Ritorna (root, widgets, result)."""
    import tkinter as tk
    from tkinter import messagebox

    result = {"url": "", "remember": False}
    root = tk.Tk()
    root.title("OneMore · Backup dei dati")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    frame = tk.Frame(root, padx=18, pady=16)
    frame.pack()

    tk.Label(frame, text="Stringa di connessione di Neon", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    tk.Label(
        frame, justify="left", fg="#555",
        text="Su Neon premi Connect (senza pooling), poi il pulsante Copy.\n"
             "Poi premi «Incolla» qui sotto. La password resta nascosta.",
    ).pack(anchor="w", pady=(2, 8))

    entry = tk.Entry(frame, show="•", width=52, font=("Segoe UI", 10))
    entry.pack(fill="x")

    def paste():
        try:
            text = root.clipboard_get()
        except tk.TclError:
            text = ""
        entry.delete(0, "end")
        entry.insert(0, clean(text))

    remember = tk.BooleanVar(value=True)

    def submit():
        url = clean(entry.get())
        if not url.startswith(("postgresql://", "postgres://")):
            messagebox.showerror(
                "Stringa non valida",
                "La stringa deve iniziare con postgresql://\nCopiala di nuovo da Neon e premi «Incolla».",
                parent=root,
            )
            return
        result["url"] = url
        result["remember"] = remember.get()
        root.destroy()

    buttons = tk.Frame(frame, pady=10)
    buttons.pack(fill="x")
    tk.Button(buttons, text="📋 Incolla", command=paste, padx=10).pack(side="left")
    tk.Checkbutton(buttons, text="Ricorda su questo PC", variable=remember).pack(side="left", padx=10)
    tk.Button(buttons, text="Fai il backup", command=submit, padx=10, default="active").pack(side="right")
    root.bind("<Return>", lambda _e: submit())
    entry.focus_set()
    return root, {"entry": entry, "paste": paste, "submit": submit, "remember": remember}, result


def ask_url():
    root, _widgets, result = build_dialog()
    root.mainloop()
    return result["url"], result["remember"]


def notify(title, message, error=False):
    print(message)
    if os.environ.get("ONEMORE_NO_GUI"):
        return
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        (messagebox.showerror if error else messagebox.showinfo)(title, message, parent=root)
        root.destroy()
    except Exception:
        pass


def run_backup(url):
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(PROJECT))

    import django
    from django.core.management import call_command

    django.setup()
    folder = PROJECT / "backups"
    folder.mkdir(exist_ok=True)
    output = folder / f"backup-{datetime.now():%Y-%m-%d_%H%M}.json"
    print("Scarico i dati…")
    # Sempre UTF-8: con la codifica predefinita di Windows lettere come "ì" renderebbero
    # il file impossibile da ripristinare
    try:
        with open(output, "w", encoding="utf-8") as stream:
            call_command("dumpdata", natural_foreign=True, exclude=EXCLUDE, indent=1, stdout=stream)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output


VENV_PYTHON = Path(r"C:\venvs\onemore\Scripts\python.exe")


def ensure_project_python():
    """Se Django non è installato in questo Python, riavvia lo script con quello del progetto.

    Ritorna None se si può proseguire, altrimenti il codice di uscita.
    """
    try:
        import django  # noqa: F401
        return None
    except ImportError:
        pass
    if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        import subprocess

        return subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    notify(
        "Ambiente Python mancante",
        "Django non è installato. Crea l'ambiente del progetto con:\n\n"
        "python -m venv C:\\venvs\\onemore\n"
        "C:\\venvs\\onemore\\Scripts\\pip install -r requirements.txt",
        error=True,
    )
    return 1


def main():
    code = ensure_project_python()
    if code is not None:
        return code
    url = clean(os.environ.get(ENV_KEY)) or read_saved_url()
    from_saved = bool(url)
    remember = False
    if not url:
        url, remember = ask_url()
        if not url:
            print("Backup annullato.")
            return 1
    try:
        output = run_backup(url)
    except Exception as exc:
        if from_saved:
            forget_url()
            hint = ("La stringa salvata non funziona più (forse hai cambiato la password su Neon): "
                    "l'ho dimenticata. Riapri backup.bat e incolla quella nuova.")
        else:
            hint = "Controlla di aver copiato la stringa giusta da Neon e di essere connesso a internet."
        notify("Backup non riuscito", f"{hint}\n\nDettaglio: {type(exc).__name__}: {str(exc)[:200]}", error=True)
        return 1
    if remember:
        save_url(url)
    size = max(output.stat().st_size // 1024, 1)
    notify("Backup completato", f"Backup salvato in:\n{output}\n({size} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
