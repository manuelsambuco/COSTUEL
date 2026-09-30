import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import services, social
from .forms import ProfileForm, SignupForm
from .models import Group, PushSubscription
from .push import notify_progress, push_enabled, send_to_users
from .views_social import GROUP_INVITE_SESSION_KEY


def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "fetch"


def _board_context(request):
    user = request.user
    log = services.get_today_log(user)
    group_goals = social.user_group_goals(user)
    cap = services.daily_cap(log, group_goals)
    return {
        "log": log,
        "cap": cap,
        "at_cap": log.total >= cap,  # pulsanti attivi fino all'obiettivo di gruppo più alto
        "extra": max(log.total - log.goal, 0),
        "group_bars": services.group_progress(log, group_goals),
        "entries": log.entries.all()[:10],
        "buttons": settings.QUICK_ADD_BUTTONS,
        "others": services.today_board(user),
        "week": services.last_days(user, 7),
    }


def _board_response(request):
    """Risposta dopo un'azione: solo il pannello (fetch) oppure redirect (senza JS)."""
    if _is_ajax(request):
        return render(request, "challenge/_board.html", _board_context(request))
    return redirect("home")


@never_cache
@login_required
def home(request):
    return render(request, "challenge/home.html", _board_context(request))


@never_cache
@login_required
def board(request):
    """Solo il pannello dei progressi: la home lo ricarica ogni 30 secondi."""
    return render(request, "challenge/_board.html", _board_context(request))


@require_POST
@login_required
def add(request):
    try:
        reps = int(request.POST.get("reps", ""))
        result = services.add_pushups(request.user, reps)
    except ValueError:
        return HttpResponseBadRequest("Numero non valido")

    if result.added == 0:
        messages.info(request, "Hai già raggiunto tutti gli obiettivi di oggi: questi non vengono contati. 💪")
    else:
        if result.just_completed:
            text = f"Sfida di {result.log.goal} completata! 🏆"
            if result.cap > result.log.total:
                text += f" Puoi continuare per i gruppi fino a {result.cap}."
            messages.success(request, text)
        for group, goal in result.groups_completed:
            messages.success(request, f"Obiettivo di {group.name} ({goal}) raggiunto! 🏅")
        if result.added < result.requested:
            messages.success(
                request,
                f"Contati {result.added} su {result.requested}: hai raggiunto il massimo di oggi ({result.cap}).",
            )
    notify_progress(result)
    return _board_response(request)


@require_POST
@login_required
def undo(request):
    entry = services.undo_last(request.user)
    if entry:
        messages.info(request, f"Annullata la serie da {entry.reps}.")
    return _board_response(request)


def _safe_next(request):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                       require_https=request.is_secure()):
        return next_url
    return ""


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    # Chi arriva dal link d'invito di un gruppo esistente non deve inserire il codice
    invite = request.session.get(GROUP_INVITE_SESSION_KEY)
    has_group_invite = bool(invite and Group.objects.filter(invite_code=invite).exists())
    form = SignupForm(request.POST or None, require_code=not has_group_invite)
    next_url = _safe_next(request)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        services.get_profile(user)
        login(request, user)
        return redirect(next_url or "home")
    return render(request, "registration/signup.html", {"form": form, "next": next_url})


@login_required
def profile(request):
    # Copia separata: se il form non è valido, request.user (mostrato in alto) resta com'era
    form = ProfileForm(request.POST or None, instance=get_user_model().objects.get(pk=request.user.pk))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profilo aggiornato ✓")
        return redirect("profile")
    return render(request, "challenge/profile.html", {"form": form})


class PasswordChangeView(SuccessMessageMixin, auth_views.PasswordChangeView):
    """Cambio password: resti connesso anche dopo il cambio."""

    template_name = "challenge/password_change.html"
    success_url = reverse_lazy("profile")
    success_message = "Password cambiata ✓"


# --- Notifiche push ---


@require_POST
@login_required
def push_subscribe(request):
    try:
        data = json.loads(request.body)
        endpoint = data["endpoint"]
        keys = data["keys"]
        p256dh, auth = keys["p256dh"], keys["auth"]
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("Iscrizione non valida")
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.headers.get("User-Agent", "")[:300],
        },
    )
    return JsonResponse({"ok": True})


@require_POST
@login_required
def push_unsubscribe(request):
    try:
        endpoint = json.loads(request.body)["endpoint"]
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest("Richiesta non valida")
    PushSubscription.objects.filter(endpoint=endpoint, user=request.user).delete()
    return JsonResponse({"ok": True})


@require_POST
@login_required
def push_test(request):
    if not push_enabled():
        return JsonResponse({"ok": False, "error": "Chiavi VAPID non configurate sul server"}, status=503)
    sent = send_to_users(
        [request.user], "🔔 Notifiche attive", "Riceverai un avviso quando gli altri fanno piegamenti."
    )
    return JsonResponse({"ok": sent > 0, "sent": sent})


# --- PWA ---


def service_worker(request):
    # Servito dalla radice del sito così può gestire tutte le pagine
    response = render(request, "challenge/sw.js", content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    return response


def manifest(request):
    return render(request, "challenge/manifest.webmanifest", content_type="application/manifest+json")
