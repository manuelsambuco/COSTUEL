import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import push, services
from .models import DailyLog, PushSubscription

User = get_user_model()


class AddPushupsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("manuel", password="x")

    def test_adds_up(self):
        services.add_pushups(self.user, 25)
        result = services.add_pushups(self.user, 30)
        self.assertEqual(result.log.total, 55)
        self.assertEqual(result.added, 30)
        self.assertFalse(result.just_completed)

    def test_excess_over_goal_is_not_counted(self):
        for _ in range(3):
            services.add_pushups(self.user, 30)  # 90
        result = services.add_pushups(self.user, 25)
        self.assertEqual(result.added, 10)
        self.assertEqual(result.log.total, 100)
        self.assertTrue(result.just_completed)
        self.assertEqual(result.log.entries.first().reps, 10)

    def test_nothing_added_after_goal(self):
        for _ in range(4):
            services.add_pushups(self.user, 25)
        result = services.add_pushups(self.user, 10)
        self.assertEqual(result.added, 0)
        self.assertFalse(result.just_completed)
        self.assertEqual(result.log.total, 100)
        self.assertEqual(result.log.entries.count(), 4)

    def test_invalid_values_rejected(self):
        for bad in (0, -5, 10_000):
            with self.assertRaises(ValueError):
                services.add_pushups(self.user, bad)

    def test_goal_comes_from_profile_and_is_snapshotted(self):
        profile = services.get_profile(self.user)
        profile.daily_goal = 50
        profile.save()
        result = services.add_pushups(self.user, 30)
        result = services.add_pushups(self.user, 30)
        self.assertEqual(result.log.total, 50)
        self.assertEqual(result.log.goal, 50)
        # Cambiare l'obiettivo dopo non altera il giorno già iniziato
        profile.daily_goal = 200
        profile.save()
        self.assertEqual(DailyLog.objects.get(pk=result.log.pk).goal, 50)

    def test_days_are_separate(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        DailyLog.objects.create(user=self.user, day=yesterday, goal=100, total=100)
        result = services.add_pushups(self.user, 20)
        self.assertEqual(result.log.total, 20)
        week = services.last_days(self.user, 7)
        self.assertTrue(week[-2]["completed"])
        self.assertEqual(week[-1]["total"], 20)

    def test_undo_last(self):
        services.add_pushups(self.user, 20)
        services.add_pushups(self.user, 30)
        entry = services.undo_last(self.user)
        self.assertEqual(entry.reps, 30)
        self.assertEqual(services.get_today_log(self.user).total, 20)
        services.undo_last(self.user)
        self.assertIsNone(services.undo_last(self.user))
        self.assertEqual(services.get_today_log(self.user).total, 0)


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("manuel", password="x", first_name="Manuel")
        self.friend = User.objects.create_user("luca", password="x", first_name="Luca")
        self.client.force_login(self.user)

    def test_home_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, "/login/?next=/")

    def test_home_shows_buttons_and_friend(self):
        services.add_pushups(self.friend, 40)
        response = self.client.get(reverse("home"))
        for n in (10, 20, 25, 30):
            self.assertContains(response, f'value="{n}"')
        self.assertContains(response, "Luca")
        self.assertContains(response, "40/100")

    def test_add_via_fetch_returns_board(self):
        response = self.client.post(reverse("add"), {"reps": 25}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="ring-total">25<')
        self.assertTemplateUsed(response, "challenge/_board.html")

    def test_add_without_js_redirects(self):
        response = self.client.post(reverse("add"), {"reps": 10})
        self.assertRedirects(response, reverse("home"))

    def test_add_capped_message(self):
        for _ in range(3):
            self.client.post(reverse("add"), {"reps": 30})
        response = self.client.post(reverse("add"), {"reps": 30}, headers={"X-Requested-With": "fetch"})
        self.assertContains(response, "Contati 10 su 30")
        self.assertContains(response, "disabled")

    def test_add_rejects_garbage(self):
        response = self.client.post(reverse("add"), {"reps": "abc"})
        self.assertEqual(response.status_code, 400)

    def test_undo(self):
        self.client.post(reverse("add"), {"reps": 20})
        self.client.post(reverse("undo"))
        self.assertEqual(services.get_today_log(self.user).total, 0)

    def test_pwa_files(self):
        self.assertEqual(self.client.get("/sw.js")["Content-Type"], "application/javascript")
        manifest = json.loads(self.client.get("/manifest.webmanifest").content)
        self.assertEqual(manifest["display"], "standalone")

    def test_push_subscribe_and_unsubscribe(self):
        sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
        response = self.client.post(reverse("push_subscribe"), json.dumps(sub), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushSubscription.objects.get().user, self.user)
        self.client.post(reverse("push_unsubscribe"), json.dumps({"endpoint": sub["endpoint"]}),
                         content_type="application/json")
        self.assertFalse(PushSubscription.objects.exists())


class SignupTests(TestCase):
    @override_settings(SIGNUP_CODE="sfida100")
    def test_signup_requires_invite_code(self):
        data = {"username": "luca", "first_name": "Luca", "password1": "Piegamenti!2026",
                "password2": "Piegamenti!2026", "invite_code": "sbagliato"}
        response = self.client.post(reverse("signup"), data)
        self.assertContains(response, "Codice d&#x27;invito non valido")
        data["invite_code"] = "sfida100"
        response = self.client.post(reverse("signup"), data)
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(User.objects.get(username="luca").profile.daily_goal, 100)


@override_settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
class NotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("manuel", password="x", first_name="Manuel")
        self.friend = User.objects.create_user("luca", password="x")
        PushSubscription.objects.create(user=self.friend, endpoint="https://push.example/luca", p256dh="k", auth="a")
        PushSubscription.objects.create(user=self.user, endpoint="https://push.example/me", p256dh="k", auth="a")
        self.client.force_login(self.user)

    def _post(self, reps):
        # Invio sincrono (niente thread) per poter controllare le chiamate
        with mock.patch("challenge.push.webpush") as webpush, \
             mock.patch("challenge.push._send_in_background", new=push.send_to_users):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("add"), {"reps": reps})
        return webpush

    def test_friend_is_notified_not_me(self):
        webpush = self._post(25)
        self.assertEqual(webpush.call_count, 1)
        kwargs = webpush.call_args.kwargs
        self.assertEqual(kwargs["subscription_info"]["endpoint"], "https://push.example/luca")
        payload = json.loads(kwargs["data"])
        self.assertEqual(payload["title"], "💪 Manuel: +25")
        self.assertIn("25/100", payload["body"])

    def test_completion_notification(self):
        services.add_pushups(self.user, 90)
        webpush = self._post(30)
        payload = json.loads(webpush.call_args.kwargs["data"])
        self.assertIn("completato", payload["title"])

    def test_no_notification_when_nothing_counted(self):
        services.add_pushups(self.user, 100)
        webpush = self._post(10)
        webpush.assert_not_called()

    def test_expired_subscription_is_removed(self):
        from pywebpush import WebPushException

        gone = mock.Mock(status_code=410)
        with mock.patch("challenge.push.webpush", side_effect=WebPushException("gone", response=gone)):
            push.send_to_users([self.friend], "t", "b")
        self.assertFalse(PushSubscription.objects.filter(user=self.friend).exists())


class VapidKeyTests(TestCase):
    def test_generated_keys_work_with_pywebpush(self):
        """Le chiavi generate da `genvapid` devono essere accettate da pywebpush."""
        from io import StringIO

        from django.core.management import call_command
        from py_vapid import Vapid

        out = StringIO()
        call_command("genvapid", stdout=out)
        lines = dict(l.split("=", 1) for l in out.getvalue().splitlines() if l.startswith("VAPID_"))
        vapid = Vapid.from_string(private_key=lines["VAPID_PRIVATE_KEY"])
        headers = vapid.sign({"sub": "mailto:a@b.c", "aud": "https://fcm.googleapis.com"})
        self.assertIn("vapid", headers["Authorization"])
        self.assertEqual(len(lines["VAPID_PUBLIC_KEY"]), 87)  # 65 byte in base64url
