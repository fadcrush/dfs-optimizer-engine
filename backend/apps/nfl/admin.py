from django.contrib import admin
from .models import NflSlate


@admin.register(NflSlate)
class NflSlateAdmin(admin.ModelAdmin):
    list_display = ("season", "week", "site", "created_at")
    list_filter = ("site", "season", "week")
