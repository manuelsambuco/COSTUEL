from django.conf import settings

from .social import incoming_requests


def push(request):
    return {"VAPID_PUBLIC_KEY": settings.VAPID_PUBLIC_KEY, "DEFAULT_DAILY_GOAL": settings.DEFAULT_DAILY_GOAL}


def navigation(request):
    """Sezione attiva della barra in basso e numero di richieste d'amicizia da vedere."""
    if not request.user.is_authenticated:
        return {}
    match = request.resolver_match
    name = match.url_name if match else ""
    if name in ("friends",):
        section = "friends"
    elif name in ("groups", "group_detail", "group_invite"):
        section = "groups"
    elif name in ("profile", "password_change"):
        section = "profile"
    elif name == "stats":
        section = "stats"
    else:
        section = "home"
    return {"nav_section": section, "pending_requests": incoming_requests(request.user).count()}
