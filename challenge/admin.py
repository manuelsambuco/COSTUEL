from django.contrib import admin

from .models import (
    DailyLog, Friendship, Group, GroupMembership, NotificationEvent, Profile, PushSubscription, PushupEntry,
)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "daily_goal")
    list_editable = ("daily_goal",)  # obiettivo modificabile direttamente dall'elenco


class PushupEntryInline(admin.TabularInline):
    model = PushupEntry
    extra = 0


@admin.register(DailyLog)
class DailyLogAdmin(admin.ModelAdmin):
    list_display = ("user", "day", "total", "goal")
    list_filter = ("day", "user")
    inlines = [PushupEntryInline]


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "user_agent", "created_at")


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ("from_user", "to_user", "status", "created_at")
    list_filter = ("status",)


class GroupMembershipInline(admin.TabularInline):
    model = GroupMembership
    extra = 0


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    inlines = [GroupMembershipInline]


@admin.register(NotificationEvent)
class NotificationEventAdmin(admin.ModelAdmin):
    """Registro dell'esperimento: sola lettura, i dati non vanno modificati a mano."""

    list_display = ("created_at", "sender", "recipient", "kind", "delivered", "devices", "delivery_prob")
    list_filter = ("delivered", "kind")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
