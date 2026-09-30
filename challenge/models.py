import secrets

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


# --- Social ---


class Friendship(models.Model):
    """Richiesta di amicizia da `from_user` a `to_user`. Diventa amicizia quando è accettata."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    STATUS_CHOICES = [(PENDING, "In attesa"), (ACCEPTED, "Accettata")]

    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="friendships_sent")
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="friendships_received")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["from_user", "to_user"], name="unique_friendship_pair"),
            models.CheckConstraint(condition=~models.Q(from_user=models.F("to_user")), name="no_self_friendship"),
        ]

    def __str__(self):
        return f"{self.from_user} → {self.to_user} ({self.status})"


def new_invite_code():
    return secrets.token_urlsafe(6)


class Group(models.Model):
    name = models.CharField(max_length=40)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="groups_created"
    )
    # Chi ha questo codice (via link) può entrare nel gruppo
    invite_code = models.CharField(max_length=20, unique=True, default=new_invite_code)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class GroupGoalChange(models.Model):
    """Storico dell'obiettivo giornaliero di un gruppo.

    L'obiettivo valido in un giorno è l'ultima modifica con ``effective_from`` <= quel giorno
    (senza modifiche vale DEFAULT_DAILY_GOAL). Alla creazione vale da subito; le modifiche
    dell'admin valgono dal giorno dopo, così la classifica del giorno non cambia a metà.
    """

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="goal_changes")
    goal = models.PositiveIntegerField()
    effective_from = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "effective_from"], name="unique_group_goal_per_day")]
        ordering = ["group", "effective_from"]

    def __str__(self):
        return f"{self.group}: {self.goal} dal {self.effective_from}"


class GroupMembership(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="group_memberships")
    is_admin = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="unique_group_member")]
        ordering = ["joined_at", "pk"]

    def __str__(self):
        return f"{self.user} in {self.group}"


# --- Esperimento sulle notifiche ---


class NotificationEvent(models.Model):
    """Ogni notifica di progresso che l'app avrebbe mandato a un amico, con la decisione casuale
    di consegnarla o trattenerla (micro-randomized trial, vedi analysis/notebooks/04)."""

    PROGRESS = "progress"
    COMPLETED = "completed"
    KIND_CHOICES = [(PROGRESS, "Serie registrata"), (COMPLETED, "Sfida completata")]

    entry = models.ForeignKey(
        PushupEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="notification_events"
    )
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications_sent")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications_received"
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    # Probabilità di consegna in vigore in quel momento e risultato dell'estrazione
    delivery_prob = models.FloatField()
    delivered = models.BooleanField()
    # Dispositivi con notifiche attive del destinatario al momento della decisione:
    # con 0 la notifica non poteva arrivare comunque, l'analisi li esclude
    devices = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "created_at"])]

    def __str__(self):
        state = "consegnata" if self.delivered else "trattenuta"
        return f"{self.sender} → {self.recipient} ({self.kind}, {state})"
