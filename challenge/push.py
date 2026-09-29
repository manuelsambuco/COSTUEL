"""Invio delle notifiche Web Push (gratuite: passano dai server di Google/Apple/Mozilla)."""

import json
import logging
import threading

from django.conf import settings
from django.db import close_old_connections, transaction
from pywebpush import WebPushException, webpush

from .models import PushSubscription
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


def notify_progress(result):
    """Avvisa gli altri partecipanti dopo che `result.log.user` ha registrato una serie."""
    if not result.added or not push_enabled():
        return
    log = result.log
    user = log.user
    name = display_name(user)
    if result.just_completed:
        title = f"🏆 {name} ha completato la sfida!"
        body = f"{log.goal}/{log.goal} piegamenti fatti oggi. Tocca a te!"
    else:
        title = f"💪 {name}: +{result.added}"
        body = f"È a {log.total}/{log.goal} oggi (ne mancano {log.remaining})."
    recipients = list(challenge_members(user))
    # Invia solo dopo il salvataggio definitivo, in un thread per non rallentare il tap
    transaction.on_commit(
        lambda: _send_in_background(recipients, title, body, tag=f"progress-{user.pk}")
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
        [friendship.to_user], f"👋 {name} vuole sfidarti", "Accetta la richiesta di amicizia su COSTUEL.",
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
