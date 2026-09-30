"""Amicizie e gruppi: chi vede chi e chi riceve le notifiche di chi."""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import Friendship, Group, GroupGoalChange, GroupMembership, new_invite_code

MAX_GROUP_GOAL = 1000

User = get_user_model()


class SocialError(Exception):
    """Operazione non permessa: il messaggio è mostrato all'utente."""


# --- Amicizie ---


def friends_of(user):
    return User.objects.filter(
        Q(friendships_sent__to_user=user, friendships_sent__status=Friendship.ACCEPTED)
        | Q(friendships_received__from_user=user, friendships_received__status=Friendship.ACCEPTED)
    ).distinct()


def friendship_between(a, b):
    return Friendship.objects.filter(Q(from_user=a, to_user=b) | Q(from_user=b, to_user=a)).first()


def are_friends(a, b):
    f = friendship_between(a, b)
    return bool(f and f.status == Friendship.ACCEPTED)


def incoming_requests(user):
    return Friendship.objects.filter(to_user=user, status=Friendship.PENDING).select_related("from_user")


def outgoing_requests(user):
    return Friendship.objects.filter(from_user=user, status=Friendship.PENDING).select_related("to_user")


def send_friend_request(user, username):
    """Ritorna (friendship, created). Se l'altro ti aveva già chiesto l'amicizia, viene accettata."""
    username = (username or "").strip()
    target = User.objects.filter(username__iexact=username, is_active=True).first()
    if not target:
        raise SocialError(f"Nessun utente con username “{username}”.")
    if target == user:
        raise SocialError("Non puoi aggiungere te stesso 😄")
    existing = friendship_between(user, target)
    if existing:
        if existing.status == Friendship.ACCEPTED:
            raise SocialError(f"Tu e {target.username} siete già amici.")
        if existing.from_user == user:
            raise SocialError(f"Hai già mandato una richiesta a {target.username}.")
        accept_request(user, existing.pk)  # lui l'aveva già chiesta a te
        return existing, False
    return Friendship.objects.create(from_user=user, to_user=target), True


def accept_request(user, friendship_id):
    f = Friendship.objects.filter(pk=friendship_id, to_user=user, status=Friendship.PENDING).first()
    if not f:
        raise SocialError("Richiesta non trovata.")
    f.status = Friendship.ACCEPTED
    f.accepted_at = timezone.now()
    f.save(update_fields=["status", "accepted_at"])
    return f


def decline_request(user, friendship_id):
    """Rifiuta una richiesta ricevuta o annulla una inviata."""
    deleted, _ = Friendship.objects.filter(
        Q(to_user=user) | Q(from_user=user), pk=friendship_id, status=Friendship.PENDING
    ).delete()
    if not deleted:
        raise SocialError("Richiesta non trovata.")


def remove_friend(user, friend_id):
    deleted, _ = Friendship.objects.filter(
        Q(from_user=user, to_user_id=friend_id) | Q(from_user_id=friend_id, to_user=user),
        status=Friendship.ACCEPTED,
    ).delete()
    if not deleted:
        raise SocialError("Amicizia non trovata.")


# --- Gruppi ---


def groups_of(user):
    # Filtro con una sottoquery: filtrare direttamente su memberships__user limiterebbe
    # anche i conteggi successivi alla sola iscrizione dell'utente.
    my_group_ids = GroupMembership.objects.filter(user=user).values("group_id")
    return (
        Group.objects.filter(pk__in=my_group_ids)
        .annotate(member_count=Count("memberships", distinct=True))
        .order_by("name")
    )


def get_membership(user, group_id):
    return GroupMembership.objects.select_related("group").filter(user=user, group_id=group_id).first()


def group_members(group):
    return User.objects.filter(group_memberships__group=group).order_by("group_memberships__joined_at")


def _clean_goal(goal):
    """Obiettivo di gruppo valido: almeno la sfida base (100), al massimo MAX_GROUP_GOAL."""
    minimum = settings.DEFAULT_DAILY_GOAL
    if goal in (None, ""):
        return minimum
    try:
        goal = int(goal)
    except (TypeError, ValueError):
        raise SocialError("Obiettivo non valido.")
    if goal < minimum:
        raise SocialError(f"L'obiettivo di un gruppo è almeno {minimum}: la sfida base resta per tutti.")
    if goal > MAX_GROUP_GOAL:
        raise SocialError(f"Obiettivo troppo alto (massimo {MAX_GROUP_GOAL}).")
    return goal


def create_group(user, name, goal=None):
    name = (name or "").strip()
    if not name:
        raise SocialError("Dai un nome al gruppo.")
    if len(name) > 40:
        raise SocialError("Nome troppo lungo (massimo 40 caratteri).")
    goal = _clean_goal(goal)
    with transaction.atomic():
        group = Group.objects.create(name=name, created_by=user)
        GroupMembership.objects.create(group=group, user=user, is_admin=True)
        # alla creazione l'obiettivo vale da subito
        GroupGoalChange.objects.create(group=group, goal=goal, effective_from=timezone.localdate())
    return group


# --- Obiettivi dei gruppi ---


def group_goals_on(group_ids, day=None):
    """{group_id: obiettivo valido quel giorno} per più gruppi con una sola query."""
    day = day or timezone.localdate()
    goals = {gid: settings.DEFAULT_DAILY_GOAL for gid in group_ids}
    changes = GroupGoalChange.objects.filter(group_id__in=list(goals), effective_from__lte=day).order_by("effective_from")
    for change in changes:  # in ordine di data: l'ultima valida vince
        goals[change.group_id] = change.goal
    return goals


def group_goal_on(group, day=None):
    return group_goals_on([group.pk], day)[group.pk]


def group_goals_by_day(group, start, end):
    """{giorno: obiettivo} per ogni giorno tra start ed end (inclusi), per le classifiche di periodo."""
    changes = list(GroupGoalChange.objects.filter(group=group, effective_from__lte=end).order_by("effective_from"))
    out, goal, i = {}, settings.DEFAULT_DAILY_GOAL, 0
    day = start
    while day <= end:
        while i < len(changes) and changes[i].effective_from <= day:
            goal = changes[i].goal
            i += 1
        out[day] = goal
        day += timedelta(days=1)
    return out


def pending_goal_change(group):
    """La modifica già decisa che partirà nei prossimi giorni, se c'è."""
    return group.goal_changes.filter(effective_from__gt=timezone.localdate()).order_by("effective_from").first()


def set_group_goal(admin, group_id, goal):
    """Cambia l'obiettivo del gruppo a partire da domani. Ritorna la modifica salvata."""
    if not GroupMembership.objects.filter(user=admin, group_id=group_id, is_admin=True).exists():
        raise SocialError("Solo gli admin possono cambiare l'obiettivo.")
    goal = _clean_goal(goal)
    tomorrow = timezone.localdate() + timedelta(days=1)
    change, _ = GroupGoalChange.objects.update_or_create(
        group_id=group_id, effective_from=tomorrow, defaults={"goal": goal}
    )
    return change


def user_group_goals(user, day=None):
    """[(gruppo, obiettivo del giorno)] per i gruppi dell'utente, obiettivo più alto per primo."""
    groups = list(Group.objects.filter(memberships__user=user))
    goals = group_goals_on([g.pk for g in groups], day)
    return sorted(((g, goals[g.pk]) for g in groups), key=lambda x: (-x[1], x[0].name.lower()))


def join_group(user, invite_code):
    """Ritorna (group, joined). `joined` è False se l'utente era già dentro."""
    group = Group.objects.filter(invite_code=(invite_code or "").strip()).first()
    if not group:
        raise SocialError("Codice d'invito non valido.")
    _, created = GroupMembership.objects.get_or_create(group=group, user=user)
    return group, created


def leave_group(user, group_id):
    """Esci dal gruppo. Se esce l'ultimo admin passa il ruolo al più anziano; se resti solo, il gruppo sparisce."""
    with transaction.atomic():
        membership = GroupMembership.objects.select_for_update().filter(user=user, group_id=group_id).first()
        if not membership:
            raise SocialError("Non fai parte di questo gruppo.")
        group = membership.group
        membership.delete()
        remaining = group.memberships.all()
        if not remaining.exists():
            group.delete()
        elif not remaining.filter(is_admin=True).exists():
            heir = remaining.first()
            heir.is_admin = True
            heir.save(update_fields=["is_admin"])


def remove_member(admin, group_id, user_id):
    if not GroupMembership.objects.filter(user=admin, group_id=group_id, is_admin=True).exists():
        raise SocialError("Solo gli admin possono rimuovere membri.")
    if admin.pk == user_id:
        raise SocialError("Per uscire usa “Esci dal gruppo”.")
    deleted, _ = GroupMembership.objects.filter(group_id=group_id, user_id=user_id).delete()
    if not deleted:
        raise SocialError("Membro non trovato.")


def delete_group(admin, group_id):
    if not GroupMembership.objects.filter(user=admin, group_id=group_id, is_admin=True).exists():
        raise SocialError("Solo gli admin possono eliminare il gruppo.")
    Group.objects.filter(pk=group_id).delete()


def regenerate_invite(admin, group_id):
    """Nuovo link d'invito: quello vecchio smette di funzionare."""
    membership = GroupMembership.objects.select_related("group").filter(
        user=admin, group_id=group_id, is_admin=True
    ).first()
    if not membership:
        raise SocialError("Solo gli admin possono cambiare il link.")
    group = membership.group
    group.invite_code = new_invite_code()
    group.save(update_fields=["invite_code"])
    return group


# --- Chi partecipa alla sfida con chi ---


def challenge_members(user):
    """Amici + membri dei gruppi dell'utente (lui escluso).

    È la cerchia che vede i suoi progressi e riceve le sue notifiche.
    """
    group_ids = GroupMembership.objects.filter(user=user).values("group_id")
    return (
        User.objects.filter(is_active=True)
        .filter(
            Q(pk__in=friends_of(user).values("pk"))
            | Q(group_memberships__group_id__in=group_ids)
        )
        .exclude(pk=user.pk)
        .distinct()
        .order_by("username")
    )
