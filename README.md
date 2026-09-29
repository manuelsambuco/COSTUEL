# COSTUEL 💪

Sfida dei 100 piegamenti al giorno tra amici. Web app Django installabile sul telefono (PWA)
con notifiche push ogni volta che qualcuno registra una serie.

**Fase 1 (fatta):** login/registrazione, pulsanti +10/+20/+25/+30, tetto all'obiettivo
(quelli oltre i 100 non contano), annulla ultima serie, progressi degli altri in tempo reale,
ultimi 7 giorni, notifiche push, installazione come app.

## Struttura

```
config/settings.py        impostazioni (tutto configurabile da variabili d'ambiente)
challenge/models.py       Profile (obiettivo), DailyLog (totale del giorno), PushupEntry (serie), PushSubscription
challenge/services.py     logica: aggiunta con tetto, annulla, classifica, ultimi giorni
challenge/push.py         invio notifiche
challenge/views.py        pagine e API
challenge/templates/      HTML, service worker (sw.js), manifest
challenge/static/         CSS, JS, icone
challenge/tests.py        test automatici
```

Punti pensati per le fasi successive:
- **Obiettivo modificabile:** `Profile.daily_goal` esiste già (oggi 100 per tutti). Ogni giorno
  salva una copia dell'obiettivo (`DailyLog.goal`), così cambiarlo non altera lo storico.
- **Amici e gruppi:** `services.challenge_members()` oggi restituisce tutti gli utenti. Basterà
  farle restituire amici e membri dei gruppi: notifiche e classifica si adatteranno da sole.

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
   Per entrare in /admin: dalla *Shell* di Render esegui `python manage.py createsuperuser`.

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
