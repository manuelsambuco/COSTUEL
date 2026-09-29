# COSTUEL 💪

Sfida dei 100 piegamenti al giorno tra amici. Web app Django installabile sul telefono (PWA)
con notifiche push ogni volta che qualcuno registra una serie.

**Fase 1 (fatta):** login/registrazione, pulsanti +10/+20/+25/+30, tetto all'obiettivo
(quelli oltre i 100 non contano), annulla ultima serie, progressi degli altri in tempo reale,
ultimi 7 giorni, notifiche push, installazione come app.

**Fase 2 (fatta):** amicizie (richiesta per username, accetta/rifiuta), gruppi con link d'invito
condivisibile (chi apre il link si registra senza codice), classifiche oggi/settimana/mese per
amici e gruppi, statistiche personali (giorni di fila, record, grafico settimanale, calendario
mensile), barra di navigazione in basso. Vedi e ricevi notifiche solo da amici e membri dei tuoi gruppi.

## Struttura

```
config/settings.py        impostazioni (tutto configurabile da variabili d'ambiente)
challenge/models.py       Profile (obiettivo), DailyLog (totale del giorno), PushupEntry (serie), PushSubscription
                          + Friendship, Group, GroupMembership
challenge/services.py     logica: aggiunta con tetto, annulla, progressi di oggi, ultimi giorni
challenge/social.py       amicizie, gruppi, challenge_members() (chi vede chi)
challenge/stats.py        periodi, classifiche, giorni di fila, calendario mensile
challenge/push.py         invio notifiche
challenge/views.py        pagina Oggi, registrazione, notifiche, PWA
challenge/views_social.py pagine Amici, Gruppi, Statistiche
challenge/templates/      HTML, service worker (sw.js), manifest
challenge/static/         CSS, JS, icone
challenge/tests.py        test automatici
```

Punti pensati per le fasi successive:
- **Obiettivo modificabile:** `Profile.daily_goal` esiste già (oggi 100 per tutti). Ogni giorno
  salva una copia dell'obiettivo (`DailyLog.goal`), così cambiarlo non altera lo storico.
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

Neon gratis conserva la cronologia solo per 6 ore. Ogni tanto salva una copia:

```powershell
$env:DATABASE_URL="<stringa di Neon>"
python manage.py dumpdata --natural-foreign --exclude contenttypes --exclude auth.permission --exclude sessions -o backup.json
Remove-Item Env:DATABASE_URL
```

`backup.json` contiene dati personali (e le password cifrate): tienilo fuori da GitHub.
