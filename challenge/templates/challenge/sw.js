{% load static %}// Service worker di COSTUEL: riceve le notifiche push anche ad app chiusa.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "COSTUEL", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "COSTUEL";
  const options = {
    body: data.body || "",
    icon: "{% static 'challenge/icons/icon-192.png' %}",
    badge: "{% static 'challenge/icons/badge-72.png' %}",
    data: { url: data.url || "/" },
  };
  if (data.tag) {
    // Una notifica per persona: la nuova sostituisce la vecchia ma suona comunque
    options.tag = data.tag;
    options.renotify = true;
  }
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const win of windows) {
        if (win.url.startsWith(self.location.origin) && "focus" in win) {
          win.navigate(url);
          return win.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
