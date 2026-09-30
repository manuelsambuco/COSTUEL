(() => {
  const cfg = window.COSTUEL;
  const board = document.getElementById("board");

  // --- Conferma prima delle azioni delicate (form con data-confirm) ---
  document.addEventListener("submit", (event) => {
    const message = event.target.dataset && event.target.dataset.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  // --- Scelta dell'obiettivo di gruppo: le scorciatoie compilano il campo numerico ---
  document.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-goal]");
    if (!chip) return;
    const picker = chip.closest("[data-goal-picker]");
    picker.querySelector('input[name="goal"]').value = chip.dataset.goal;
    picker.querySelectorAll("[data-goal]").forEach((c) => c.classList.toggle("active", c === chip));
  });
  document.addEventListener("input", (event) => {
    const picker = event.target.closest && event.target.closest("[data-goal-picker]");
    if (!picker) return;
    picker.querySelectorAll("[data-goal]").forEach((c) => c.classList.toggle("active", c.dataset.goal === event.target.value));
  });

  // --- Condividi / copia il link d'invito ---
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-share]");
    if (!button) return;
    const url = button.dataset.share;
    const text = button.dataset.shareText || "";
    if (navigator.share) {
      try { await navigator.share({ title: "COSTUEL", text, url }); } catch (e) { /* annullato */ }
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
    } catch (e) {
      const input = document.getElementById("invite-url");
      if (input) { input.select(); document.execCommand("copy"); }
    }
    const original = button.textContent;
    button.textContent = "Copiato ✓";
    setTimeout(() => { button.textContent = original; }, 2000);
  });

  // --- Pulsanti senza ricaricare la pagina ---
  // Funziona anche senza JavaScript: i form fanno un normale POST.

  let busy = false;

  async function refreshBoard(response) {
    const html = await response.text();
    board.innerHTML = html;
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.matches("[data-ajax]") || !board) return;
    event.preventDefault();
    if (busy) return;
    busy = true;
    board.classList.add("is-busy");

    const body = new FormData(form);
    if (event.submitter && event.submitter.name) {
      body.append(event.submitter.name, event.submitter.value);
    }
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body,
        headers: { "X-Requested-With": "fetch" },
        credentials: "same-origin",
      });
      if (response.redirected && new URL(response.url).pathname.startsWith("/login")) {
        window.location = response.url;
        return;
      }
      if (response.ok) {
        await refreshBoard(response);
        if (navigator.vibrate) navigator.vibrate(15);
      }
    } catch (e) {
      alert("Connessione assente: riprova.");
    } finally {
      busy = false;
      board.classList.remove("is-busy");
    }
  });

  // Aggiorna i progressi degli altri ogni 30 secondi (e quando si torna sull'app)
  async function poll() {
    if (!board || busy || document.hidden) return;
    try {
      const response = await fetch(cfg.urls.board, {
        headers: { "X-Requested-With": "fetch" },
        credentials: "same-origin",
      });
      if (response.ok && !response.redirected && !busy) await refreshBoard(response);
    } catch (e) { /* offline: riprova al prossimo giro */ }
  }
  if (board) {
    setInterval(poll, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  }

  // --- PWA e notifiche push ---

  const card = document.getElementById("push-card");
  const els = {
    title: document.getElementById("push-title"),
    desc: document.getElementById("push-desc"),
    enable: document.getElementById("push-enable"),
    test: document.getElementById("push-test"),
    disable: document.getElementById("push-disable"),
  };

  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isStandalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  let registration = null;

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(cfg.urls.sw, { scope: "/" })
      .then((reg) => { registration = reg; return updatePushCard(); })
      .catch((err) => console.warn("Service worker non registrato", err));
  }

  function show(title, desc, buttons = []) {
    if (!card) return;
    card.hidden = false;
    els.title.textContent = title;
    els.desc.textContent = desc;
    for (const name of ["enable", "test", "disable"]) els[name].hidden = !buttons.includes(name);
  }

  async function updatePushCard() {
    if (!card) return;
    if (!pushSupported) {
      if (isIOS && !isStandalone) {
        show("Installa l'app per le notifiche",
          "Su iPhone: tocca Condividi → \"Aggiungi alla schermata Home\", poi apri COSTUEL dall'icona.");
      } else {
        show("Notifiche non disponibili", "Questo browser non supporta le notifiche push. Prova con Chrome o Safari.");
      }
      return;
    }
    if (!cfg.vapidPublicKey) {
      show("Notifiche non configurate", "Il server non ha ancora le chiavi VAPID (vedi README).");
      return;
    }
    if (Notification.permission === "denied") {
      show("Notifiche bloccate", "Le hai bloccate: riattivale dalle impostazioni del browser per questo sito.");
      return;
    }
    const sub = registration && await registration.pushManager.getSubscription();
    if (sub && Notification.permission === "granted") {
      // Ri-sincronizza col server (es. dopo un cambio di dispositivo o database)
      postJSON(cfg.urls.subscribe, sub.toJSON()).catch(() => {});
      show("Notifiche attive ✓", "Ti avviseremo quando gli altri fanno piegamenti.", ["test", "disable"]);
    } else {
      show("Attiva le notifiche", "Ricevi un avviso ogni volta che qualcuno registra una serie.", ["enable"]);
    }
  }

  function postJSON(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": cfg.csrfToken },
      credentials: "same-origin",
      body: JSON.stringify(data),
    });
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  els.enable?.addEventListener("click", async () => {
    els.enable.disabled = true;
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") return updatePushCard();
      const reg = registration || await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(cfg.vapidPublicKey),
      });
      const response = await postJSON(cfg.urls.subscribe, sub.toJSON());
      if (!response.ok) throw new Error("Salvataggio fallito");
      await updatePushCard();
    } catch (err) {
      console.error(err);
      alert("Non sono riuscito ad attivare le notifiche: " + err.message);
    } finally {
      els.enable.disabled = false;
    }
  });

  els.test?.addEventListener("click", async () => {
    const response = await postJSON(cfg.urls.test, {});
    const data = await response.json().catch(() => ({}));
    if (!data.ok) alert(data.error || "Notifica non inviata: prova a disattivare e riattivare.");
  });

  els.disable?.addEventListener("click", async () => {
    const sub = registration && await registration.pushManager.getSubscription();
    if (sub) {
      await postJSON(cfg.urls.unsubscribe, { endpoint: sub.endpoint }).catch(() => {});
      await sub.unsubscribe();
    }
    updatePushCard();
  });
})();
