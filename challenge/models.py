from django.conf import settings
from django.db import models


def default_goal():
    return settings.DEFAULT_DAILY_GOAL


class Profile(models.Model):
    """Dati extra dell'utente. Creato in automatico al primo accesso."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    # Oggi fisso a 100 per tutti; il campo esiste già per poterlo rendere modificabile in futuro.
    daily_goal = models.PositiveIntegerField(default=default_goal)

    def __str__(self):
        return f"Profilo di {self.user}"


class DailyLog(models.Model):
    """Il totale di un utente in un giorno. Una riga per (utente, giorno)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_logs")
    day = models.DateField()
    # Copia dell'obiettivo valido quel giorno: se l'obiettivo cambia, lo storico resta corretto.
    goal = models.PositiveIntegerField()
    # Piegamenti contati (mai oltre `goal`).
    total = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "day"], name="unique_log_per_user_day")]
        ordering = ["-day"]

    def __str__(self):
        return f"{self.user} {self.day}: {self.total}/{self.goal}"

    @property
    def remaining(self):
        return max(self.goal - self.total, 0)

    @property
    def completed(self):
        return self.total >= self.goal

    @property
    def percent(self):
        return min(round(self.total * 100 / self.goal), 100) if self.goal else 100


class PushupEntry(models.Model):
    """Una singola serie registrata (es. +25 alle 8:10)."""

    log = models.ForeignKey(DailyLog, on_delete=models.CASCADE, related_name="entries")
    # Piegamenti effettivamente contati (già tagliati all'obiettivo).
    reps = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "pushup entries"

    def __str__(self):
        return f"+{self.reps} ({self.log.user}, {self.created_at:%d/%m %H:%M})"


class PushSubscription(models.Model):
    """Un dispositivo (telefono/browser) su cui l'utente ha attivato le notifiche."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} — {self.user_agent[:40]}"

    def as_subscription_info(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}
