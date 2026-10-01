import json
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import push, services, social, stats
from .models import DailyLog, Friendship, Group, GroupMembership, NotificationEvent, PushSubscription

User = get_user_model()


def make_friends(a, b):
    return Friendship.objects.create(from_user=a, to_user=b, status=Friendship.ACCEPTED)


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
        make_friends(self.user, self.friend)
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


@override_settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv", NOTIFY_DELIVERY_PROB=1.0)
class NotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("manuel", password="x", first_name="Manuel")
        self.friend = User.objects.create_user("luca", password="x")
        make_friends(self.user, self.friend)
        # uno sconosciuto con le notifiche attive non deve ricevere nulla
        stranger = User.objects.create_user("sconosciuto", password="x")
        PushSubscription.objects.create(user=stranger, endpoint="https://push.example/x", p256dh="k", auth="a")
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


class FriendshipTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("manuel", password="x")
        self.b = User.objects.create_user("luca", password="x")

    def test_request_accept_and_remove(self):
        f, created = social.send_friend_request(self.a, "Luca")  # username senza distinzione di maiuscole
        self.assertTrue(created)
        self.assertFalse(social.are_friends(self.a, self.b))
        self.assertEqual(list(social.incoming_requests(self.b)), [f])
        social.accept_request(self.b, f.pk)
        self.assertTrue(social.are_friends(self.a, self.b))
        self.assertEqual(list(social.friends_of(self.a)), [self.b])
        self.assertEqual(list(social.friends_of(self.b)), [self.a])
        social.remove_friend(self.b, self.a.pk)
        self.assertFalse(social.are_friends(self.a, self.b))

    def test_crossed_requests_become_friendship(self):
        social.send_friend_request(self.a, "luca")
        _, created = social.send_friend_request(self.b, "manuel")
        self.assertFalse(created)
        self.assertTrue(social.are_friends(self.a, self.b))
        self.assertEqual(Friendship.objects.count(), 1)

    def test_invalid_requests(self):
        for username in ("manuel", "nessuno"):
            with self.assertRaises(social.SocialError):
                social.send_friend_request(self.a, username)
        social.send_friend_request(self.a, "luca")
        with self.assertRaises(social.SocialError):
            social.send_friend_request(self.a, "luca")

    def test_only_recipient_can_accept(self):
        f, _ = social.send_friend_request(self.a, "luca")
        with self.assertRaises(social.SocialError):
            social.accept_request(self.a, f.pk)

    def test_decline(self):
        f, _ = social.send_friend_request(self.a, "luca")
        social.decline_request(self.b, f.pk)
        self.assertFalse(Friendship.objects.exists())

    def test_friends_page_flow(self):
        self.client.force_login(self.a)
        self.client.post(reverse("friend_request"), {"username": "luca"})
        self.client.force_login(self.b)
        response = self.client.get(reverse("friends"))
        self.assertContains(response, "Richieste ricevute")
        self.assertContains(response, 'class="tab-badge"')
        f = Friendship.objects.get()
        self.client.post(reverse("friend_accept", args=[f.pk]))
        services.add_pushups(self.a, 30)
        for periodo in ("oggi", "settimana", "mese"):
            response = self.client.get(reverse("friends"), {"periodo": periodo})
            self.assertContains(response, "Classifica amici")
            self.assertContains(response, "manuel")


class GroupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("manuel", password="x")
        self.member = User.objects.create_user("luca", password="x")
        self.stranger = User.objects.create_user("marco", password="x")
        self.group = social.create_group(self.admin, "Palestra")
        social.join_group(self.member, self.group.invite_code)

    def test_members_see_each_other_but_not_strangers(self):
        self.assertIn(self.member, social.challenge_members(self.admin))
        self.assertIn(self.admin, social.challenge_members(self.member))
        self.assertNotIn(self.stranger, social.challenge_members(self.admin))
        self.assertNotIn(self.admin, social.challenge_members(self.admin))

    def test_friend_and_group_member_listed_once(self):
        make_friends(self.admin, self.member)
        self.assertEqual(list(social.challenge_members(self.admin)), [self.member])

    def test_join_twice_is_harmless(self):
        _, joined = social.join_group(self.member, self.group.invite_code)
        self.assertFalse(joined)
        with self.assertRaises(social.SocialError):
            social.join_group(self.member, "codice-sbagliato")

    def test_admin_leaving_hands_over(self):
        social.leave_group(self.admin, self.group.pk)
        self.assertTrue(GroupMembership.objects.get(user=self.member).is_admin)
        social.leave_group(self.member, self.group.pk)
        self.assertFalse(Group.objects.exists())

    def test_only_admin_can_manage(self):
        with self.assertRaises(social.SocialError):
            social.remove_member(self.member, self.group.pk, self.admin.pk)
        with self.assertRaises(social.SocialError):
            social.delete_group(self.member, self.group.pk)
        social.remove_member(self.admin, self.group.pk, self.member.pk)
        self.assertEqual(self.group.memberships.count(), 1)

    def test_new_link_invalidates_old(self):
        old = self.group.invite_code
        social.regenerate_invite(self.admin, self.group.pk)
        with self.assertRaises(social.SocialError):
            social.join_group(self.stranger, old)

    def test_detail_hidden_from_non_members(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("group_detail", args=[self.group.pk])).status_code, 404)

    def test_group_pages(self):
        services.add_pushups(self.member, 100)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("groups"))
        self.assertContains(response, "Palestra")
        self.assertContains(response, "1/2")  # uno su due ha finito oggi
        response = self.client.get(reverse("group_detail", args=[self.group.pk]), {"periodo": "settimana"})
        self.assertContains(response, f"/g/{self.group.invite_code}/")
        self.assertContains(response, "🥇")

    def test_join_via_link(self):
        self.client.force_login(self.stranger)
        url = reverse("group_invite", args=[self.group.invite_code])
        self.assertContains(self.client.get(url), "Unisciti al gruppo")
        response = self.client.post(url)
        self.assertRedirects(response, reverse("group_detail", args=[self.group.pk]))
        self.assertTrue(social.get_membership(self.stranger, self.group.pk))

    def test_join_by_pasted_link(self):
        self.client.force_login(self.stranger)
        link = f"https://onemore.onrender.com/g/{self.group.invite_code}/"
        self.client.post(reverse("group_join_by_code"), {"code": link})
        self.assertTrue(social.get_membership(self.stranger, self.group.pk))

    @override_settings(SIGNUP_CODE="segreto")
    def test_signup_from_group_link_needs_no_code(self):
        url = reverse("group_invite", args=[self.group.invite_code])
        self.client.get(url)  # visita da non registrato
        response = self.client.post(reverse("signup"), {
            "username": "giulia", "first_name": "Giulia",
            "password1": "Piegamenti!2026", "password2": "Piegamenti!2026", "next": url,
        })
        self.assertRedirects(response, url)
        self.client.post(url)
        self.assertTrue(social.get_membership(User.objects.get(username="giulia"), self.group.pk))

    @override_settings(SIGNUP_CODE="segreto")
    def test_signup_without_group_link_still_needs_code(self):
        response = self.client.post(reverse("signup"), {
            "username": "giulia", "password1": "Piegamenti!2026", "password2": "Piegamenti!2026",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="giulia").exists())

    def test_signup_ignores_external_next(self):
        response = self.client.post(reverse("signup"), {
            "username": "giulia", "password1": "Piegamenti!2026", "password2": "Piegamenti!2026",
            "next": "https://sito-malevolo.example/",
        })
        self.assertRedirects(response, reverse("home"))


class StatsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("manuel", password="x")
        self.today = date(2026, 9, 30)  # mercoledì

    def log(self, days_ago, total, user=None):
        DailyLog.objects.create(user=user or self.user, day=self.today - timedelta(days=days_ago), goal=100, total=total)

    def test_periods(self):
        week = stats.get_period("settimana", self.today)
        self.assertEqual(week.start, date(2026, 9, 28))  # lunedì
        self.assertEqual(week.days_elapsed, 3)
        month = stats.get_period("mese", self.today)
        self.assertEqual((month.start, month.end, month.days_elapsed), (date(2026, 9, 1), date(2026, 9, 30), 30))
        self.assertEqual(stats.get_period("boh", self.today).key, "oggi")

    def test_streaks(self):
        for d in (0, 1, 2):
            self.log(d, 100)
        self.log(3, 50)
        for d in (4, 5, 6, 7):
            self.log(d, 100)
        self.assertEqual(stats.streaks(self.user, self.today), (3, 4))

    def test_streak_survives_unfinished_today(self):
        self.log(0, 40)
        self.log(1, 100)
        self.log(2, 100)
        self.assertEqual(stats.streaks(self.user, self.today)[0], 2)

    def test_leaderboard(self):
        other = User.objects.create_user("luca", password="x")
        self.log(0, 100)
        self.log(1, 60)
        self.log(0, 100, other)
        self.log(1, 100, other)
        rows = stats.leaderboard([self.user, other], stats.get_period("settimana", self.today), me=self.user)
        self.assertEqual([r["username"] for r in rows], ["luca", "manuel"])
        self.assertEqual((rows[0]["reps"], rows[0]["done"], rows[0]["percent"]), (200, 2, 67))
        self.assertEqual((rows[1]["reps"], rows[1]["done"], rows[1]["rank"]), (160, 1, 2))
        self.assertTrue(rows[1]["is_me"])

    def test_leaderboard_ties_share_rank(self):
        other = User.objects.create_user("luca", password="x")
        rows = stats.leaderboard([self.user, other], stats.get_period("oggi", self.today))
        self.assertEqual([r["rank"] for r in rows], [1, 1])

    def test_month_calendar(self):
        self.log(0, 100)
        self.log(1, 50)
        cal = stats.month_calendar(self.user, 2026, 9, self.today)
        self.assertEqual((cal["reps"], cal["done"], cal["days_elapsed"], cal["rate"]), (150, 1, 30, 3))
        self.assertEqual(len(cal["weeks"][0]), 7)
        self.assertIsNone(cal["weeks"][0][0])  # 1 settembre 2026 è martedì
        self.assertIsNone(cal["next"])
        self.assertEqual(cal["prev"], "2026-08")

    def test_stats_page(self):
        self.client.force_login(self.user)
        services.add_pushups(self.user, 30)
        response = self.client.get(reverse("stats"))
        self.assertContains(response, "Questa settimana")
        self.assertContains(response, "piegamenti totali")
        self.assertEqual(self.client.get(reverse("stats"), {"mese": "2020-02"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("stats"), {"mese": "boh"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("stats"), {"mese": "2099-01"}).status_code, 200)


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("manuel", password="Vecchia!Password1", first_name="Manuel")
        self.friend = User.objects.create_user("luca", password="x")
        make_friends(self.user, self.friend)
        self.client.force_login(self.user)

    def test_change_display_name_and_username(self):
        response = self.client.post(reverse("profile"), {"first_name": "Manu", "username": "manu88"})
        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.username), ("Manu", "manu88"))
        # amicizie e accesso con il nuovo username restano validi
        self.assertTrue(social.are_friends(self.user, self.friend))
        self.client.logout()
        self.assertTrue(self.client.login(username="manu88", password="Vecchia!Password1"))

    def test_username_taken_case_insensitive(self):
        response = self.client.post(reverse("profile"), {"first_name": "Altro", "username": "LUCA"})
        self.assertContains(response, "già usato")
        # in alto resta il nome salvato, non quello del form non valido
        self.assertContains(response, 'title="Profilo">Manuel</a>')
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "manuel")

    def test_keep_same_username(self):
        response = self.client.post(reverse("profile"), {"first_name": "Manuel S.", "username": "manuel"})
        self.assertRedirects(response, reverse("profile"))

    def test_invalid_username(self):
        response = self.client.post(reverse("profile"), {"first_name": "", "username": "con spazi!"})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "manuel")

    def test_change_password_keeps_session(self):
        response = self.client.post(reverse("password_change"), {
            "old_password": "Vecchia!Password1",
            "new_password1": "Nuova!Password2",
            "new_password2": "Nuova!Password2",
        })
        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)  # ancora connesso
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Nuova!Password2"))

    def test_wrong_old_password(self):
        response = self.client.post(reverse("password_change"), {
            "old_password": "sbagliata",
            "new_password1": "Nuova!Password2",
            "new_password2": "Nuova!Password2",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Vecchia!Password1"))

    def test_topbar_links_to_profile(self):
        self.assertContains(self.client.get(reverse("home")), 'href="/profilo/"')


class SetGoalCommandTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("manuel", password="x")
        self.b = User.objects.create_user("luca", password="x")
        yesterday = timezone.localdate() - timedelta(days=1)
        DailyLog.objects.create(user=self.a, day=yesterday, goal=100, total=100)
        services.add_pushups(self.a, 90)  # oggi già iniziato

    def run_cmd(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("set_goal", *args, stdout=out)
        return out.getvalue()

    def test_everyone_from_today(self):
        self.run_cmd("150")
        self.assertEqual(services.get_profile(self.b).daily_goal, 150)
        today = services.get_today_log(self.a)
        self.assertEqual(today.goal, 150)
        # si possono aggiungere altri piegamenti fino a 150
        self.assertEqual(services.add_pushups(self.a, 30).log.total, 120)
        # lo storico resta com'era
        self.assertTrue(DailyLog.objects.get(user=self.a, day=timezone.localdate() - timedelta(days=1)).completed)

    def test_single_user_from_tomorrow(self):
        self.run_cmd("50", "--utente", "LUCA", "--da-domani")
        self.assertEqual(services.get_profile(self.b).daily_goal, 50)
        self.assertEqual(services.get_profile(self.a).daily_goal, 100)
        self.assertEqual(services.get_today_log(self.a).goal, 100)

    def test_errors(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self.run_cmd("0")
        with self.assertRaises(CommandError):
            self.run_cmd("150", "--utente", "nessuno")

    @override_settings(DEFAULT_DAILY_GOAL=150)
    def test_texts_follow_default_goal(self):
        self.assertContains(self.client.get(reverse("login")), "150 piegamenti al giorno")


class ExportAnalysisDataTests(TestCase):
    def test_export_is_pseudonymized_and_complete(self):
        import csv
        import tempfile
        from io import StringIO
        from pathlib import Path

        from django.core.management import call_command

        a = User.objects.create_user("manuel", password="x", first_name="Manuel")
        b = User.objects.create_user("luca", password="x")
        c = User.objects.create_user("giulia", password="x")
        make_friends(a, b)
        group = social.create_group(a, "Palestra")
        social.join_group(c, group.invite_code)
        services.add_pushups(a, 30)
        services.add_pushups(a, 25)
        services.add_pushups(b, 20)

        with tempfile.TemporaryDirectory() as tmp:
            call_command("export_analysis_data", out=tmp, stdout=StringIO())
            read = lambda name: list(csv.DictReader(open(Path(tmp) / f"{name}.csv", encoding="utf-8")))  # noqa: E731
            users, edges, entries, daily = read("users"), read("edges"), read("entries"), read("daily")
            raw = "".join((Path(tmp) / f"{n}.csv").read_text(encoding="utf-8") for n in ("users", "edges", "entries", "daily"))

        self.assertEqual(len(users), 3)
        self.assertNotIn("manuel", raw.lower())  # no usernames or names anywhere
        self.assertNotIn("Manuel", raw)
        # a<->b friends, a<->c same group: 4 directed edges
        self.assertEqual(len(edges), 4)
        self.assertEqual(len(entries), 3)
        self.assertEqual(sorted(int(e["reps"]) for e in entries), [20, 25, 30])
        self.assertEqual(sorted(int(d["total"]) for d in daily), [20, 55])
        ids = {u["user_id"] for u in users}
        self.assertTrue(all(e["user_id"] in ids and e["friend_id"] in ids for e in edges))


@override_settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv", NOTIFY_DELIVERY_PROB=0.8)
class NotificationExperimentTests(TestCase):
    """Ogni notifica di progresso viene registrata, consegnata o trattenuta a caso."""

    def setUp(self):
        self.user = User.objects.create_user("manuel", password="x")
        self.luca = User.objects.create_user("luca", password="x")
        self.giulia = User.objects.create_user("giulia", password="x")
        self.stranger = User.objects.create_user("marco", password="x")
        make_friends(self.user, self.luca)
        make_friends(self.user, self.giulia)
        for u, name in ((self.luca, "luca-1"), (self.luca, "luca-2"), (self.giulia, "giulia")):
            PushSubscription.objects.create(user=u, endpoint=f"https://push.example/{name}", p256dh="k", auth="a")
        self.client.force_login(self.user)

    def _post(self, reps, draws):
        """Registra una serie con estrazioni fissate (una per destinatario, in ordine di username)."""
        with mock.patch("challenge.push.webpush") as webpush, \
             mock.patch("challenge.push._send_in_background", new=push.send_to_users), \
             mock.patch("challenge.push.draw_delivery", side_effect=draws):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("add"), {"reps": reps})
        return webpush

    def test_every_decision_is_logged_and_only_delivered_ones_are_sent(self):
        webpush = self._post(25, draws=[False, True])  # giulia trattenuta, luca consegnata
        events = {e.recipient.username: e for e in NotificationEvent.objects.all()}
        self.assertEqual(set(events), {"giulia", "luca"})  # lo sconosciuto non c'è
        self.assertFalse(events["giulia"].delivered)
        self.assertTrue(events["luca"].delivered)
        self.assertEqual(events["luca"].devices, 2)
        self.assertEqual(events["luca"].delivery_prob, 0.8)
        self.assertEqual(events["luca"].kind, NotificationEvent.PROGRESS)
        self.assertEqual(events["luca"].entry.reps, 25)
        endpoints = sorted(c.kwargs["subscription_info"]["endpoint"] for c in webpush.call_args_list)
        self.assertEqual(endpoints, ["https://push.example/luca-1", "https://push.example/luca-2"])

    def test_withheld_notifications_send_nothing(self):
        webpush = self._post(25, draws=[False, False])
        webpush.assert_not_called()
        self.assertEqual(NotificationEvent.objects.filter(delivered=False).count(), 2)

    def test_completion_is_logged_with_its_kind(self):
        services.add_pushups(self.user, 90)
        self._post(10, draws=[True, True])
        self.assertTrue(NotificationEvent.objects.filter(kind=NotificationEvent.COMPLETED).exists())

    def test_draw_uses_the_configured_probability(self):
        share = sum(push.draw_delivery(0.8) for _ in range(4000)) / 4000
        self.assertTrue(0.77 < share < 0.83)
        self.assertFalse(any(push.draw_delivery(0.0) for _ in range(100)))
        self.assertTrue(all(push.draw_delivery(1.0) for _ in range(100)))

    def test_undo_keeps_the_experiment_record(self):
        self._post(25, draws=[True, True])
        self.client.post(reverse("undo"))
        self.assertEqual(NotificationEvent.objects.count(), 2)
        self.assertIsNone(NotificationEvent.objects.first().entry)

    @override_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="")
    def test_nothing_logged_when_push_is_not_configured(self):
        self.client.post(reverse("add"), {"reps": 25})
        self.assertFalse(NotificationEvent.objects.exists())

    def test_export_includes_the_decisions(self):
        import csv
        import tempfile
        from io import StringIO
        from pathlib import Path

        from django.core.management import call_command

        self._post(25, draws=[True, False])
        with tempfile.TemporaryDirectory() as tmp:
            call_command("export_analysis_data", out=tmp, stdout=StringIO())
            with open(Path(tmp) / "notifications.csv", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["delivered"] for r in rows), ["False", "True"])
        self.assertNotIn("luca", "".join(str(r) for r in rows))


class ExperimentDefaultTests(TestCase):
    def test_experiment_is_off_unless_configured(self):
        """Senza NOTIFY_DELIVERY_PROB tutte le notifiche arrivano: l'esperimento si attiva solo da Render."""
        import os
        import unittest

        from django.conf import settings

        if "NOTIFY_DELIVERY_PROB" in os.environ:
            raise unittest.SkipTest("valore impostato nell'ambiente")
        self.assertEqual(settings.NOTIFY_DELIVERY_PROB, 1.0)
        self.assertTrue(all(push.draw_delivery(settings.NOTIFY_DELIVERY_PROB) for _ in range(200)))


class GroupGoalTests(TestCase):
    """Sfida base a 100 per tutti; i gruppi possono avere un obiettivo più alto."""

    def setUp(self):
        self.me = User.objects.create_user("manuel", password="x", first_name="Manuel")
        self.friend = User.objects.create_user("luca", password="x", first_name="Luca")
        self.mate = User.objects.create_user("sara", password="x", first_name="Sara")
        make_friends(self.me, self.friend)  # amico, non nel gruppo
        self.crossfit = social.create_group(self.me, "Crossfit", goal=200)
        social.join_group(self.mate, self.crossfit.invite_code)
        self.today = timezone.localdate()

    def fill(self, user, total):
        while services.get_today_log(user).total < total:
            services.add_pushups(user, min(30, total - services.get_today_log(user).total))

    # --- creazione e modifica ---

    def test_goal_validation(self):
        self.assertEqual(social.group_goal_on(social.create_group(self.me, "Base")), 100)
        for bad in (0, -5, 1001, "tanti"):
            with self.assertRaises(social.SocialError):
                social.create_group(self.me, "X", goal=bad)
        for ok in (1, 50, 1000):
            self.assertEqual(social.group_goal_on(social.create_group(self.me, f"G{ok}", goal=ok)), ok)
        self.assertEqual(social.group_goal_on(self.crossfit), 200)  # alla creazione vale subito

    def test_group_below_100_keeps_the_challenge_and_ranks_on_its_goal(self):
        easy = social.create_group(self.friend, "Principianti", goal=50)
        self.assertEqual(services.add_pushups(self.friend, 30).cap, 100)  # la sfida base resta 100
        result = services.add_pushups(self.friend, 30)
        self.assertEqual([(g.name, goal) for g, goal in result.groups_completed], [("Principianti", 50)])
        self.fill(self.friend, 100)
        self.assertEqual(services.add_pushups(self.friend, 10).added, 0)
        period = stats.get_period("oggi")
        goals = social.group_goals_by_day(easy, period.start, period.end)
        row = stats.leaderboard([self.friend], period, goals_by_day=goals)[0]
        self.assertEqual((row["reps"], row["goal"], row["completed"]), (50, 50, True))
        self.client.force_login(self.friend)
        self.assertNotIn("Sfide di gruppo", self.client.get(reverse("home")).content.decode())

    def test_group_goal_suggestions(self):
        self.client.force_login(self.me)
        html = self.client.get(reverse("groups")).content.decode()
        for n in (50, 100, 150, 200):
            self.assertIn(f'data-goal="{n}"', html)
        self.assertIn('min="1" max="1000"', html)
        self.assertIn("Alzate l'asticella insieme", html)
        self.assertNotIn("Da 1 a 1000", html)  # il limite non si mostra finché non lo si supera
        for bad in ("5000", "tanti"):
            response = self.client.post(reverse("group_create"), {"name": "Troppo", "goal": bad}, follow=True)
            self.assertFalse(Group.objects.filter(name="Troppo").exists())
        self.assertContains(response, "Obiettivo non valido")
        response = self.client.post(reverse("group_create"), {"name": "Troppo", "goal": "5000"}, follow=True)
        self.assertContains(response, "tra 1 e 1000")

    def test_admin_change_applies_from_tomorrow(self):
        with self.assertRaises(social.SocialError):
            social.set_group_goal(self.mate, self.crossfit.pk, 300)  # non admin
        change = social.set_group_goal(self.me, self.crossfit.pk, 300)
        tomorrow = self.today + timedelta(days=1)
        self.assertEqual(change.effective_from, tomorrow)
        self.assertEqual(social.group_goal_on(self.crossfit, self.today), 200)
        self.assertEqual(social.group_goal_on(self.crossfit, tomorrow), 300)
        self.assertEqual(social.pending_goal_change(self.crossfit).goal, 300)
        by_day = social.group_goals_by_day(self.crossfit, self.today, tomorrow)
        self.assertEqual(list(by_day.values()), [200, 300])

    # --- quanto si può fare ---

    def test_group_goal_raises_the_daily_cap_but_the_challenge_stays_at_100(self):
        result = services.add_pushups(self.me, 90)
        self.assertEqual(result.cap, 200)
        result = services.add_pushups(self.me, 30)
        self.assertTrue(result.just_completed)  # sfida base superata a 120
        self.assertEqual(result.log.total, 120)
        self.fill(self.me, 190)
        result = services.add_pushups(self.me, 30)
        self.assertEqual((result.added, result.log.total), (10, 200))
        self.assertEqual([(g.name, goal) for g, goal in result.groups_completed], [("Crossfit", 200)])
        self.assertEqual(services.add_pushups(self.me, 10).added, 0)
        self.assertTrue(DailyLog.objects.get(user=self.me, day=self.today).completed)
        self.assertEqual(stats.streaks(self.me)[0], 1)  # la serie di giorni resta sulla sfida base

    def test_without_higher_groups_the_cap_stays_100(self):
        self.fill(self.friend, 100)
        self.assertEqual(services.add_pushups(self.friend, 10).added, 0)

    def test_leaving_the_group_lowers_the_cap_but_keeps_what_was_done(self):
        self.fill(self.me, 150)
        social.leave_group(self.me, self.crossfit.pk)
        self.assertEqual(services.add_pushups(self.me, 10).added, 0)
        self.assertEqual(services.get_today_log(self.me).total, 150)

    # --- cosa si vede ---

    def test_home_shows_group_bars_and_keeps_buttons_active(self):
        self.client.force_login(self.me)
        self.fill(self.me, 150)
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("Sfide di gruppo", html)
        self.assertIn("150/200", html)
        self.assertIn("sfida 100 ✓", html)
        self.assertNotIn('value="10" class="btn btn-add"\n              disabled', html)
        self.assertNotIn("disabled>+10", html.replace("\n", "").replace(" ", ""))
        self.fill(self.me, 200)
        html = self.client.get(reverse("home")).content.decode().replace("\n", "").replace(" ", "")
        self.assertIn("disabled>+10", html)

    def test_home_without_higher_groups_has_no_group_bars(self):
        self.client.force_login(self.friend)
        self.assertNotIn("Sfide di gruppo", self.client.get(reverse("home")).content.decode())

    def test_friends_see_me_at_100_with_an_extra_badge(self):
        self.fill(self.me, 150)
        row = next(r for r in services.today_board(self.friend) if r["name"] == "Manuel")
        self.assertEqual((row["total"], row["extra"], row["completed"]), (100, 50, True))
        self.client.force_login(self.friend)
        self.assertContains(self.client.get(reverse("home")), '<span class="extra">+50</span>')

    def test_friends_leaderboard_caps_at_the_challenge(self):
        self.fill(self.me, 150)
        self.fill(self.friend, 100)
        rows = {r["username"]: r for r in stats.leaderboard([self.me, self.friend], stats.get_period("oggi"))}
        self.assertEqual((rows["manuel"]["reps"], rows["manuel"]["extra"]), (100, 50))
        self.assertEqual(rows["manuel"]["rank"], rows["luca"]["rank"])  # alla pari sulla sfida base

    def test_group_leaderboard_uses_the_group_goal(self):
        self.fill(self.me, 150)
        self.fill(self.mate, 200)
        period = stats.get_period("oggi")
        goals = social.group_goals_by_day(self.crossfit, period.start, period.end)
        rows = {r["username"]: r for r in stats.leaderboard([self.me, self.mate], period, goals_by_day=goals)}
        self.assertEqual((rows["sara"]["reps"], rows["sara"]["completed"]), (200, True))
        self.assertEqual((rows["manuel"]["reps"], rows["manuel"]["completed"], rows["manuel"]["goal"]), (150, False, 200))

    def test_group_pages(self):
        self.fill(self.me, 150)
        self.fill(self.mate, 200)
        self.client.force_login(self.me)
        html = self.client.get(reverse("groups")).content.decode()
        self.assertIn("obiettivo 200", html)
        self.assertIn("1/2", html)  # solo Sara ha fatto i 200 del gruppo
        detail = self.client.get(reverse("group_detail", args=[self.crossfit.pk]))
        self.assertContains(detail, "Te ne mancano <strong>50</strong>")
        self.assertContains(detail, 'id="ring-gradient"')  # senza, l'anello resta vuoto
        self.assertContains(detail, "Cambia da domani")  # admin
        response = self.client.post(reverse("group_set_goal", args=[self.crossfit.pk]), {"goal": 250}, follow=True)
        self.assertContains(response, "Da domani l&#x27;obiettivo del gruppo sarà 250")
        self.assertContains(response, "l'obiettivo sarà <strong>250</strong>")  # avviso della modifica in arrivo
        self.client.force_login(self.mate)
        self.assertNotContains(self.client.get(reverse("group_detail", args=[self.crossfit.pk])), "Cambia da domani")

    def test_create_group_form_accepts_a_goal(self):
        self.client.force_login(self.friend)
        self.client.post(reverse("group_create"), {"name": "Maratona", "goal": 300})
        group = Group.objects.get(name="Maratona")
        self.assertEqual(social.group_goal_on(group), 300)


@override_settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv", NOTIFY_DELIVERY_PROB=1.0)
class GroupGoalNotificationTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user("manuel", password="x", first_name="Manuel")
        self.friend = User.objects.create_user("luca", password="x")
        self.mate = User.objects.create_user("sara", password="x")
        make_friends(self.me, self.friend)
        group = social.create_group(self.me, "Crossfit", goal=200)
        social.join_group(self.mate, group.invite_code)
        for u in (self.friend, self.mate):
            PushSubscription.objects.create(user=u, endpoint=f"https://push.example/{u.username}", p256dh="k", auth="a")
        self.client.force_login(self.me)

    def _post(self, reps):
        with mock.patch("challenge.push.webpush") as webpush, \
             mock.patch("challenge.push._send_in_background", new=push.send_to_users):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("add"), {"reps": reps})
        return [(c.kwargs["subscription_info"]["endpoint"].rsplit("/", 1)[-1], json.loads(c.kwargs["data"])["title"])
                for c in webpush.call_args_list]

    def test_sets_for_the_challenge_reach_everyone(self):
        sent = self._post(30)
        self.assertEqual(sorted(u for u, _ in sent), ["luca", "sara"])

    def test_extra_sets_only_reach_group_mates(self):
        services.add_pushups(self.me, 100)
        sent = self._post(30)
        self.assertEqual([u for u, _ in sent], ["sara"])  # Luca non è nel gruppo da 200
        self.assertEqual(NotificationEvent.objects.get().recipient, self.mate)

    def test_reaching_the_group_goal_is_announced_to_the_group(self):
        services.add_pushups(self.me, 100)
        services.add_pushups(self.me, 80)
        sent = self._post(30)
        self.assertIn(("sara", "🏅 Manuel ha completato i 200 di Crossfit!"), sent)
        self.assertNotIn("luca", [u for u, _ in sent])


class PasswordRulesTests(TestCase):
    def test_password_may_resemble_username_or_name(self):
        """Tolto il controllo "troppo simile ai dati personali": restano lunghezza, comuni e solo numeri."""
        response = self.client.post(reverse("signup"), {
            "username": "manuel", "first_name": "Manuel",
            "password1": "manuel2026", "password2": "manuel2026",
        })
        self.assertRedirects(response, reverse("home"))

    def test_remaining_rules_still_apply(self):
        for weak in ("corta1", "12345678901", "password123"):
            response = self.client.post(reverse("signup"), {
                "username": f"u{len(weak)}", "password1": weak, "password2": weak,
            })
            self.assertEqual(response.status_code, 200, weak)
            self.assertFalse(User.objects.filter(username=f"u{len(weak)}").exists())


class ManyFriendsTests(TestCase):
    """Con tanti amici le liste restano corte: primi N + la tua riga, il resto a richiesta."""

    def setUp(self):
        self.me = User.objects.create_user("manuel", password="x", first_name="Manuel")
        self.client.force_login(self.me)

    def add_friends(self, n, start=0):
        people = []
        for i in range(start, start + n):
            friend = User.objects.create_user(f"amico{i:02d}", password="x", first_name=f"Amico {i:02d}")
            make_friends(self.me, friend)
            people.append(friend)
        return people

    def test_today_board_ranks_me_among_the_others(self):
        a, b = self.add_friends(2)
        services.add_pushups(a, 80)
        services.add_pushups(self.me, 50)
        rows = services.today_board(self.me)
        self.assertEqual([(r["name"], r["rank"], r["is_me"]) for r in rows],
                         [("Amico 00", 1, False), ("Manuel", 2, True), ("Amico 01", 3, False)])

    def test_today_board_queries_do_not_grow_with_friends(self):
        self.add_friends(2)
        services.today_board(self.me)  # crea i profili mancanti
        with CaptureQueriesContext(connection) as few:
            services.today_board(self.me)
        self.add_friends(10, start=2)
        services.today_board(self.me)
        with CaptureQueriesContext(connection) as many:
            services.today_board(self.me)
        self.assertEqual(len(few), len(many))

    def test_collapse_rows_keeps_top_and_me(self):
        rows = [{"is_me": i == 7} for i in range(12)]
        self.assertEqual(services.collapse_rows(rows, 5), 6)  # 12 - 5 in cima - la mia riga
        self.assertEqual([i for i, r in enumerate(rows) if not r["more"]], [0, 1, 2, 3, 4, 7])
        self.assertEqual([i for i, r in enumerate(rows) if r["gap"]], [7])
        rows = [{"is_me": i == 5} for i in range(6)]
        services.collapse_rows(rows, 5)
        self.assertFalse(rows[5]["gap"])  # subito dopo i primi: niente separatore

    def test_home_shows_top_five_and_my_row(self):
        for i, friend in enumerate(self.add_friends(8)):
            services.add_pushups(friend, 90 - i * 10)
        services.add_pushups(self.me, 5)
        html = self.client.get(reverse("home")).content.decode()
        self.assertEqual(html.count('class="other is-more"'), 3)  # 8 amici - 5 in cima
        self.assertIn('class="other is-me"', html)
        self.assertIn('class="list-gap"', html)
        self.assertIn("Vedi tutti (8)", html)

    def test_home_with_few_friends_has_no_toggle(self):
        self.add_friends(3)
        html = self.client.get(reverse("home")).content.decode()
        self.assertNotIn("Vedi tutti", html)
        self.assertNotIn("is-more", html)

    def test_home_alone_invites_to_add_friends(self):
        self.assertContains(self.client.get(reverse("home")), "Sei ancora da solo")

    def test_friends_page_ranking_and_folded_list(self):
        self.add_friends(12)
        html = self.client.get(reverse("friends")).content.decode()
        self.assertIn("Mostra tutta la classifica (13)", html)
        self.assertEqual(html.count("is-more"), 2)  # 13 in classifica: primi 10 + me (ultimo) visibili
        self.assertIn('<details class="fold">', html)
        self.assertIn('data-filter="friend-rows"', html)
        self.assertIn('data-search="amico 03 amico03"', html)

    def test_small_friend_list_has_no_search(self):
        self.add_friends(2)
        html = self.client.get(reverse("friends")).content.decode()
        self.assertNotIn('data-filter="friend-rows"', html)
        self.assertNotIn("Mostra tutta la classifica", html)

    def test_notification_card_lives_in_the_profile(self):
        self.assertNotContains(self.client.get(reverse("home")), 'id="push-card"')
        self.assertContains(self.client.get(reverse("profile")), 'id="push-card"')
