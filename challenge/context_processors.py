from django.conf import settings


def push(request):
    return {"VAPID_PUBLIC_KEY": settings.VAPID_PUBLIC_KEY}
