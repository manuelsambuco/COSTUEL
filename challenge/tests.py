import json
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import push, services, social, stats
from .models import DailyLog, Friendship, Group, GroupMembership, PushSubscription

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


@override_settings(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
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
        link = f"https://costuel.onrender.com/g/{self.group.invite_code}/"
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
