from django.contrib import admin

from .models import DailyLog, Friendship, Group, GroupMembership, Profile, PushSubscription, PushupEntry


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
