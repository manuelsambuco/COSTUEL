from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import social, stats
from .models import DailyLog, Group, GroupMembership
from .push import notify_friend_accepted, notify_friend_request, notify_group_join
from .services import display_name

GROUP_INVITE_SESSION_KEY = "group_invite"
GOAL_CHOICES = (100, 150, 200, 300)  # scorciatoie nel form; si può scrivere qualsiasi numero


def _period(request):
    return stats.get_period(request.GET.get("periodo", "oggi"))


def _period_tabs(period):
    return [{"key": key, "label": label, "active": key == period.key} for key, label in stats.PERIODS]


def _run(request, action, *args, success=None):
    """Esegue un'azione social mostrando l'eventuale errore come messaggio."""
    try:
        result = action(*args)
    except social.SocialError as exc:
        messages.error(request, str(exc))
        return None, False
    if success:
        messages.success(request, success)
    return result, True


# --- Amici ---


@login_required
def friends(request):
    user = request.user
    period = _period(request)
    friend_list = list(social.friends_of(user).order_by("first_name", "username"))
    return render(request, "challenge/friends.html", {
        "period": period,
        "tabs": _period_tabs(period),
        "ranking": stats.leaderboard([user, *friend_list], period, me=user) if friend_list else [],
        "friends": friend_list,
        "incoming": social.incoming_requests(user),
        "outgoing": social.outgoing_requests(user),
    })


@require_POST
@login_required
def friend_request(request):
    result, ok = _run(request, social.send_friend_request, request.user, request.POST.get("username"))
    if ok:
        friendship, created = result
        if created:
            messages.success(request, f"Richiesta inviata a {display_name(friendship.to_user)}.")
            notify_friend_request(friendship)
        else:
            messages.success(request, f"Ora tu e {display_name(friendship.from_user)} siete amici! 🤝")
            notify_friend_accepted(friendship)
    return redirect("friends")


@require_POST
@login_required
def friend_accept(request, pk):
    friendship, ok = _run(request, social.accept_request, request.user, pk)
    if ok:
        messages.success(request, f"Ora tu e {display_name(friendship.from_user)} siete amici! 🤝")
        notify_friend_accepted(friendship)
    return redirect("friends")


@require_POST
@login_required
def friend_decline(request, pk):
    _run(request, social.decline_request, request.user, pk, success="Richiesta eliminata.")
    return redirect("friends")


@require_POST
@login_required
def friend_remove(request, user_id):
    _run(request, social.remove_friend, request.user, user_id, success="Amico rimosso.")
    return redirect("friends")


# --- Gruppi ---


@login_required
def groups(request):
    today = timezone.localdate()
    group_list = list(social.groups_of(request.user))
    goals = social.group_goals_on([g.pk for g in group_list], today)
    members = {}
    for group_id, user_id in GroupMembership.objects.filter(group__in=group_list).values_list("group_id", "user_id"):
        members.setdefault(group_id, []).append(user_id)
    all_ids = {uid for ids in members.values() for uid in ids}
    totals = dict(DailyLog.objects.filter(day=today, user_id__in=all_ids).values_list("user_id", "total"))
    for g in group_list:
        # "finito oggi" si misura sull'obiettivo del gruppo, non sulla sfida base
        g.goal_today = goals[g.pk]
        g.done_today = sum(totals.get(uid, 0) >= g.goal_today for uid in members.get(g.pk, []))
    return render(request, "challenge/groups.html", {
        "groups": group_list,
        "goal_choices": GOAL_CHOICES,
        "min_goal": settings.DEFAULT_DAILY_GOAL,
        "max_goal": social.MAX_GROUP_GOAL,
    })


@require_POST
@login_required
def group_create(request):
    group, ok = _run(request, social.create_group, request.user, request.POST.get("name"), request.POST.get("goal"))
    if ok:
        messages.success(request, f"Gruppo “{group.name}” creato! Condividi il link per invitare gli amici.")
        return redirect("group_detail", pk=group.pk)
    return redirect("groups")


def _extract_code(value):
    """Accetta sia il codice sia il link intero (…/g/<codice>/)."""
    value = (value or "").strip().rstrip("/")
    return value.rsplit("/", 1)[-1]


@require_POST
@login_required
def group_join_by_code(request):
    result, ok = _run(request, social.join_group, request.user, _extract_code(request.POST.get("code")))
    if not ok:
        return redirect("groups")
    group, joined = result
    if joined:
        messages.success(request, f"Sei entrato in “{group.name}”! 💪")
        notify_group_join(group, request.user)
    return redirect("group_detail", pk=group.pk)


@login_required
def group_detail(request, pk):
    membership = social.get_membership(request.user, pk)
    if not membership:
        raise Http404
    group = membership.group
    period = _period(request)
    members = list(social.group_members(group))
    admins = set(group.memberships.filter(is_admin=True).values_list("user_id", flat=True))
    # nel gruppo conta solo il suo obiettivo, giorno per giorno (può essere cambiato nel tempo)
    goals_by_day = social.group_goals_by_day(group, period.start, period.end)
    goal_today = goals_by_day.get(period.today) or social.group_goal_on(group)
    my_log = DailyLog.objects.filter(user=request.user, day=period.today).first()
    my_total = min(my_log.total if my_log else 0, goal_today)
    return render(request, "challenge/group_detail.html", {
        "group": group,
        "is_admin": membership.is_admin,
        "goal_today": goal_today,
        "my_total": my_total,
        "my_percent": min(round(my_total * 100 / goal_today), 100),
        "my_done": my_total >= goal_today,
        "my_remaining": max(goal_today - my_total, 0),
        "pending_goal": social.pending_goal_change(group),
        "goal_choices": GOAL_CHOICES,
        "min_goal": settings.DEFAULT_DAILY_GOAL,
        "max_goal": social.MAX_GROUP_GOAL,
        "period": period,
        "tabs": _period_tabs(period),
        "ranking": stats.leaderboard(members, period, me=request.user, goals_by_day=goals_by_day),
        "members": [{"user": m, "is_admin": m.pk in admins} for m in members],
        "invite_url": request.build_absolute_uri(reverse("group_invite", args=[group.invite_code])),
    })


@require_POST
@login_required
def group_set_goal(request, pk):
    change, ok = _run(request, social.set_group_goal, request.user, pk, request.POST.get("goal"))
    if ok:
        messages.success(request, f"Da domani l'obiettivo del gruppo sarà {change.goal}. Oggi resta quello attuale.")
    return redirect("group_detail", pk=pk)


@require_POST
@login_required
def group_leave(request, pk):
    _, ok = _run(request, social.leave_group, request.user, pk, success="Sei uscito dal gruppo.")
    return redirect("groups") if ok else redirect("group_detail", pk=pk)


@require_POST
@login_required
def group_remove_member(request, pk, user_id):
    _run(request, social.remove_member, request.user, pk, user_id, success="Membro rimosso.")
    return redirect("group_detail", pk=pk)


@require_POST
@login_required
def group_delete(request, pk):
    _, ok = _run(request, social.delete_group, request.user, pk, success="Gruppo eliminato.")
    return redirect("groups") if ok else redirect("group_detail", pk=pk)


@require_POST
@login_required
def group_new_link(request, pk):
    _run(request, social.regenerate_invite, request.user, pk,
         success="Nuovo link creato: quello vecchio non funziona più.")
    return redirect("group_detail", pk=pk)


def group_invite(request, code):
    """Pagina aperta dal link d'invito. Chi non ha un account può registrarsi senza codice."""
    group = get_object_or_404(Group, invite_code=code)
    if request.user.is_authenticated:
        if request.method == "POST":
            _, joined = social.join_group(request.user, code)
            if joined:
                messages.success(request, f"Sei entrato in “{group.name}”! 💪")
                notify_group_join(group, request.user)
            return redirect("group_detail", pk=group.pk)
        if social.get_membership(request.user, group.pk):
            return redirect("group_detail", pk=group.pk)
    else:
        request.session[GROUP_INVITE_SESSION_KEY] = code
    return render(request, "challenge/group_invite.html", {
        "group": group,
        "member_count": group.memberships.count(),
    })


# --- Statistiche personali ---


@login_required
def my_stats(request):
    user = request.user
    today = timezone.localdate()
    try:
        year, month = map(int, request.GET.get("mese", "").split("-"))
        date(year, month, 1)
    except ValueError:
        year, month = today.year, today.month
    if (year, month) > (today.year, today.month):
        year, month = today.year, today.month
    current, best = stats.streaks(user, today)
    week = stats.week_chart(user, today)
    week_period = stats.get_period("settimana", today)
    week_reps = sum(d["total"] for d in week)
    week_done = sum(1 for d in week if d["completed"])
    return render(request, "challenge/stats.html", {
        "streak": current,
        "best_streak": best,
        "lifetime": stats.lifetime_total(user),
        "week": week,
        "week_reps": week_reps,
        "week_done": week_done,
        "week_days": week_period.days_elapsed,
        "month": stats.month_calendar(user, year, month, today),
    })
