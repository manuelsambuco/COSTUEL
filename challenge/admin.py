from django.contrib import admin

from .models import DailyLog, Profile, PushSubscription, PushupEntry


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "daily_goal")


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
