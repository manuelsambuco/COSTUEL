# COSTUEL · guida operativa

Istruzioni pratiche per far girare, pubblicare e gestire l'app (in italiano).
La presentazione del progetto, in inglese, è nel [README](../README.md); l'analisi dei dati
in [`analysis/`](../analysis/README.md).

**Cosa fa l'app:** sfida dei 100 piegamenti al giorno tra amici. Pulsanti +10/+20/+25/+30 con
tetto all'obiettivo (quelli oltre i 100 non contano), annulla ultima serie, amicizie e gruppi con
link d'invito, classifiche oggi/settimana/mese, statistiche personali, notifiche push, installabile
sul telefono (PWA). Vedi e ricevi notifiche solo da amici e membri dei tuoi gruppi.

## Struttura

```
config/settings.py        impostazioni (tutto configurabile da variabili d'ambiente)
challenge/models.py       Profile (obiettivo), DailyLog (totale del giorno), PushupEntry (serie), PushSubscription
                          + Friendship, Group, GroupMembership, NotificationEvent (esperimento notifiche)
challenge/services.py     logica: aggiunta con tetto, annulla, progressi di oggi, ultimi giorni
challenge/social.py       amicizie, gruppi, challenge_members() (chi vede chi)
challenge/stats.py        periodi, classifiche, giorni di fila, calendario mensile
challenge/push.py         invio notifiche (con consegna casuale e registro delle decisioni)
challenge/views.py        pagina Oggi, registrazione, profilo, notifiche, PWA
challenge/views_social.py pagine Amici, Gruppi, Statistiche
challenge/management/     comandi: genvapid, set_goal, export_analysis_data
challenge/templates/      HTML, service worker (sw.js), manifest
challenge/static/         CSS, JS, icone
challenge/tests.py        test automatici
analysis/                 analisi dei dati (vedi analysis/README.md)
backup.py, backup.bat     backup del database online con un doppio clic
```

Punti chiave:
- **Obiettivo modificabile:** `Profile.daily_goal` (oggi 100 per tutti). Ogni giorno salva una
  copia dell'obiettivo (`DailyLog.goal`), così cambiarlo non altera lo storico.
- **Cerchia della sfida:** `social.challenge_members()` decide chi vede i tuoi progressi e riceve
  le tue notifiche (amici + membri dei tuoi gruppi).

## Avvio in locale (Windows)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py genvapid          # copia le due righe VAPID_... nel file .env
python manage.py migrate
python manage.py createsuperuser   # facoltativo: accesso a /admin
python manage.py runserver
```

Apri http://localhost:8000 e registrati. Test: `python manage.py test challenge`.

## Pubblicazione online gratis (Render + Neon)

Servono 3 account gratuiti: **GitHub**, **Neon** (database), **Render** (server).

1. **GitHub:** crea un repository (anche privato) e carica il progetto:
   ```powershell
   git init
   git add .
   git commit -m "COSTUEL - fase 1"
   git branch -M main
   git remote add origin https://github.com/TUO-UTENTE/costuel.git
   git push -u origin main
   ```
   Il file `.env` NON viene caricato (è in `.gitignore`): le chiavi restano sul tuo PC.

2. **Neon** (https://neon.com): crea un progetto, regione Europa (Frankfurt).
   Copia la *connection string* (`postgresql://...?sslmode=require`).

3. **Render** (https://render.com): *New → Blueprint*, collega il repository.
   Render legge `render.yaml` e chiede i valori mancanti:
   - `DATABASE_URL` → la stringa di Neon
   - `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` → le stesse del tuo `.env`
   - `VAPID_CONTACT` → `mailto:tuaemail@...`
   - `SIGNUP_CODE` → un codice a tua scelta da dare solo agli amici

   Dopo il deploy l'app è su `https://costuel-xxxx.onrender.com`.
   Per entrare in /admin (facoltativo): la Shell di Render non c'è nel piano gratuito, quindi
   crea l'amministratore dal tuo PC puntando al database di Neon:
   ```powershell
   $env:DATABASE_URL="<stringa di Neon>"; python manage.py createsuperuser; Remove-Item Env:DATABASE_URL
   ```

Ogni `git push` successivo ripubblica l'app in automatico.

### Limiti del piano gratuito
- Render "addormenta" il server dopo 15 minuti senza visite: la prima apertura dopo una pausa
  richiede circa un minuto. Le notifiche non ne risentono (partono quando qualcuno registra una serie,
  quindi il server è sveglio).
- Neon gratis: 0,5 GB, più che sufficienti per anni di piegamenti.
- Non usare il database gratuito di Render: scade dopo 30 giorni. Per questo usiamo Neon.

## Installare l'app sul telefono e attivare le notifiche

**Android (Chrome):** apri il sito → menu ⋮ → *Installa app* (o *Aggiungi a schermata Home*).
Apri l'app → *Attiva notifiche*.

**iPhone (iOS 16.4 o successivo):** apri il sito **in Safari** → pulsante *Condividi* →
*Aggiungi alla schermata Home*. Apri l'app **dall'icona** (non da Safari) → *Attiva notifiche*.
Su iPhone le notifiche web funzionano solo così.

Usa il pulsante *Prova* per ricevere una notifica di test.

## Gestione nel tempo

### Dove stanno le cose

| Cosa | Dove | Note |
|---|---|---|
| Codice | questa cartella + GitHub | GitHub è la copia di riferimento: Render pubblica da lì |
| Dati veri (utenti, piegamenti, gruppi) | database Neon | né sul PC né su GitHub |
| Chiavi segrete (`.env`) | solo sul PC | su Render ci sono le stesse, in *Environment* |
| `db.sqlite3` | solo sul PC | database di prova locale, si può cancellare |

### Fare una modifica

```powershell
python manage.py test challenge   # tutto verde?
git add -A
git commit -m "Descrizione della modifica"
git push                          # Render ripubblica in 3-5 minuti
```

Se una modifica rompe qualcosa online: su Render → *Events* → *Rollback* alla versione precedente.

### Cambiare l'obiettivo giornaliero

Il comando si esegue dal PC ma agisce sul database online, passando la stringa di Neon:

```powershell
$env:DATABASE_URL="<stringa di Neon>"
python manage.py set_goal 150                     # tutti, da oggi
python manage.py set_goal 150 --da-domani         # tutti, da domani
python manage.py set_goal 80 --utente luca        # solo un utente
Remove-Item Env:DATABASE_URL
```

- I giorni passati mantengono l'obiettivo che avevano: statistiche e serie restano corrette.
- Per i **nuovi iscritti** e per i testi dell'app ("150 piegamenti al giorno") imposta anche
  `DEFAULT_DAILY_GOAL=150` su Render → *Environment*.
- In alternativa, dal pannello `/admin` → *Profiles* puoi modificare l'obiettivo di ognuno.

### Backup dei dati

Neon gratis conserva la cronologia solo per 6 ore. Ogni tanto (es. una volta a settimana) salva una copia:
**doppio clic su `backup.bat`** nella cartella del progetto.

La prima volta si apre una finestra: su Neon premi *Connect* (senza pooling) → *Copy*, poi nella finestra
premi **Incolla** e **Fai il backup**. Con *Ricorda su questo PC* la stringa viene salvata nel file `.env`
(chiave `NEON_DATABASE_URL`) e dalle volte successive il backup parte da solo. Se cambi la password su Neon,
lo script se ne accorge, dimentica quella vecchia e te la richiede. Il backup viene salvato in
`backups/backup-AAAA-MM-GG_HHMM.json`. La cartella `backups/` non va su GitHub perché contiene dati
personali (e le password cifrate), ma essendo sul Desktop viene salvata anche su OneDrive.

Per ripristinare un backup in un database **vuoto** (es. un nuovo progetto Neon):

```powershell
$env:DATABASE_URL="<stringa del database vuoto>"
python manage.py migrate
python manage.py loaddata backups\backup-AAAA-MM-GG_HHMM.json
Remove-Item Env:DATABASE_URL
```


### Esperimento sulle notifiche

L'app registra ogni notifica di progresso inviata agli amici (`/admin` → *Notification events*).
Di default arrivano tutte. Per attivare l'esperimento (una parte trattenuta a caso) aggiungi su
Render → *Environment* `NOTIFY_DELIVERY_PROB=0.8` (20% trattenuto); per spegnerlo rimettila a `1`.
Esportazione per l'analisi: `python manage.py export_analysis_data` (cartella `analysis/data/`,
esclusa da git).

### Obiettivi dei gruppi

La sfida base resta 100 per tutti. Un gruppo può avere un obiettivo più alto (da 100 a 1000),
scelto alla creazione; l'admin lo cambia dalla pagina del gruppo e il nuovo valore **vale da
domani** (il giorno in corso non cambia). Regole:

- il cerchio della home, i giorni di fila, il calendario e le statistiche restano sui 100;
- i pulsanti restano attivi fino all'obiettivo di gruppo più alto; oltre non si conta;
- sotto il cerchio, "Sfide di gruppo" mostra una barra per ogni gruppo sopra i 100;
- tra amici tutti sono misurati su 100 (gli extra sono un badge "+50"); nella classifica di un
  gruppo conta l'obiettivo del gruppo, giorno per giorno;
- dopo i 100 le notifiche delle serie arrivano solo ai membri dei gruppi in cui contano ancora;
  chi raggiunge l'obiettivo di un gruppo lo annuncia al gruppo.
