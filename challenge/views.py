import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import services
from .forms import SignupForm
from .models import PushSubscription
from .push import notify_progress, push_enabled, send_to_users


def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "fetch"


def _board_context(request):
    user = request.user
    log = services.get_today_log(user)
    return {
        "log": log,
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
        messages.info(request, "Obiettivo di oggi già raggiunto: questi non vengono contati. 💪")
    elif result.added < result.requested:
        messages.success(
            request,
            f"Contati {result.added} su {result.requested}: hai raggiunto l'obiettivo di "
            f"{result.log.goal}! 🏆",
        )
    elif result.just_completed:
        messages.success(request, f"Obiettivo di {result.log.goal} raggiunto! 🏆")
    notify_progress(result)
    return _board_response(request)


@require_POST
@login_required
def undo(request):
    entry = services.undo_last(request.user)
    if entry:
        messages.info(request, f"Annullata la serie da {entry.reps}.")
    return _board_response(request)


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        services.get_profile(user)
        login(request, user)
        return redirect("home")
    return render(request, "registration/signup.html", {"form": form})


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
