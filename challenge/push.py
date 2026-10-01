"""Invio delle notifiche Web Push (gratuite: passano dai server di Google/Apple/Mozilla)."""

import json
import logging
import random
import threading

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Count
from pywebpush import WebPushException, webpush

from .models import NotificationEvent, PushSubscription
from .services import display_name
from .social import challenge_members, group_members

logger = logging.getLogger(__name__)


def push_enabled():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def send_to_users(users, title, body, url="/", tag=None):
    """Manda una notifica a tutti i dispositivi degli utenti indicati.

    Ritorna quante notifiche sono state consegnate ai servizi push.
    """
    if not push_enabled():
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    sent = 0
    for sub in PushSubscription.objects.filter(user__in=users):
        try:
            webpush(
                subscription_info=sub.as_subscription_info(),
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_CONTACT},
                ttl=60 * 60 * 12,
                timeout=10,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                # Il dispositivo ha revocato il permesso o l'iscrizione è scaduta
                sub.delete()
            else:
                logger.warning("Push fallita per %s: %s", sub, exc)
        except Exception:  # mai far fallire una richiesta per colpa di una notifica
            logger.exception("Errore inatteso inviando push a %s", sub)
    return sent


def _send_in_background(*args, **kwargs):
    def run():
        try:
            send_to_users(*args, **kwargs)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()


_rng = random.SystemRandom()


def draw_delivery(prob):
    """Estrazione casuale: True = consegna, False = trattieni. Separata per poterla fissare nei test."""
    return _rng.random() < prob


def notify_progress(result):
    """Avvisa gli altri dopo che `result.log.user` ha registrato una serie.

    * Serie che contano per la sfida base (sotto 100): a tutti gli amici e ai membri dei gruppi.
    * Serie oltre la sfida base: solo ai membri dei gruppi il cui obiettivo non è ancora raggiunto.
    Per ogni destinatario si estrae a caso se consegnarla (NOTIFY_DELIVERY_PROB) e la decisione
    viene salvata in NotificationEvent, anche quando la notifica è trattenuta: è l'esperimento
    che misura se le notifiche funzionano davvero.
    Chi raggiunge l'obiettivo di un gruppo lo annuncia al gruppo (sempre consegnata).
    """
    if not result.added or not push_enabled():
        return
    log = result.log
    user = log.user
    name = display_name(user)
    if result.prev_total < log.goal:
        recipients = list(challenge_members(user))
        if result.just_completed:
            kind = NotificationEvent.COMPLETED
            title = f"🏆 {name} ha completato la sfida!"
            body = f"{log.goal}/{log.goal} piegamenti fatti oggi. Tocca a te!"
        else:
            kind = NotificationEvent.PROGRESS
            title = f"💪 {name}: +{result.added}"
            body = f"È a {log.total}/{log.goal} oggi (ne mancano {log.remaining})."
    else:
        seen, recipients = {user.pk}, []
        for group, _ in result.extra_groups:
            for member in group_members(group):
                if member.pk not in seen:
                    seen.add(member.pk)
                    recipients.append(member)
        kind = NotificationEvent.PROGRESS
        title = f"💪 {name}: +{result.added}"
        body = "È a {} oggi · {}.".format(
            log.total, ", ".join(f"{g.name} {min(log.total, goal)}/{goal}" for g, goal in result.extra_groups))
    if recipients:
        _record_and_send(user, recipients, kind, result.entry, title, body)

    for group, goal in result.groups_completed:
        _notify_later(
            group_members(group).exclude(pk=user.pk), f"🏅 {name} ha completato i {goal} di {group.name}!",
            "Obiettivo del gruppo raggiunto oggi.", url=f"/gruppi/{group.pk}/", tag=f"progress-{user.pk}",
        )


def _record_and_send(user, recipients, kind, entry, title, body):
    """Estrazione casuale per destinatario, registro delle decisioni, invio di quelle consegnate."""
    prob = settings.NOTIFY_DELIVERY_PROB
    devices = dict(
        PushSubscription.objects.filter(user__in=recipients)
        .values("user").annotate(n=Count("id")).values_list("user", "n")
    )
    events = NotificationEvent.objects.bulk_create([
        NotificationEvent(
            entry=entry, sender=user, recipient=r, kind=kind, delivery_prob=prob,
            delivered=draw_delivery(prob), devices=devices.get(r.pk, 0),
        )
        for r in recipients
    ])
    to_send = [e.recipient for e in events if e.delivered]
    if to_send:
        # Invia solo dopo il salvataggio definitivo, in un thread per non rallentare il tap
        transaction.on_commit(
            lambda: _send_in_background(to_send, title, body, tag=f"progress-{user.pk}")
        )


def _notify_later(users, title, body, url="/", tag=None):
    if not push_enabled():
        return
    users = list(users)
    if users:
        transaction.on_commit(lambda: _send_in_background(users, title, body, url=url, tag=tag))


def notify_friend_request(friendship):
    name = display_name(friendship.from_user)
    _notify_later(
        [friendship.to_user], f"👋 {name} vuole sfidarti", "Accetta la richiesta di amicizia su OneMore.",
        url="/amici/", tag=f"friend-{friendship.pk}",
    )


def notify_friend_accepted(friendship):
    name = display_name(friendship.to_user)
    _notify_later(
        [friendship.from_user], f"🤝 {name} ha accettato", "Ora vedete i progressi l'uno dell'altro.",
        url="/amici/", tag=f"friend-{friendship.pk}",
    )


def notify_group_join(group, user):
    others = group_members(group).exclude(pk=user.pk)
    _notify_later(
        others, f"🙌 {display_name(user)} è entrato in {group.name}", "Un avversario in più in classifica!",
        url=f"/gruppi/{group.pk}/", tag=f"group-{group.pk}",
    )
