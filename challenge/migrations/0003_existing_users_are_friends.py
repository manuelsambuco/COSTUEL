"""Prima della parte social tutti vedevano tutti: per non perdere nessuno,
gli utenti già registrati diventano amici tra loro."""

from itertools import combinations

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def make_friends(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Friendship = apps.get_model("challenge", "Friendship")
    users = list(User.objects.filter(is_active=True).order_by("pk"))
    now = timezone.now()
    Friendship.objects.bulk_create(
        [Friendship(from_user=a, to_user=b, status="accepted", accepted_at=now) for a, b in combinations(users, 2)],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("challenge", "0002_social"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(make_friends, migrations.RunPython.noop)]
