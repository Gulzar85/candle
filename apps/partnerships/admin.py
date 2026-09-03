"""Django admin for partnerships, members, invitations, events and notifications."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from .models import (
    Notification,
    Partnership,
    PartnershipEvent,
    PartnershipInvitation,
    PartnershipMember,
)


class PartnershipMemberInline(admin.TabularInline):  # type: ignore[type-arg]
    model = PartnershipMember
    extra = 0
    readonly_fields = ("joined_at", "left_at", "created_at", "updated_at")
    autocomplete_fields = ("user",)


class PartnershipInvitationInline(admin.TabularInline):  # type: ignore[type-arg]
    model = PartnershipInvitation
    extra = 0
    readonly_fields = (
        "token_hash",
        "expires_at",
        "accepted_at",
        "rejected_at",
        "revoked_at",
        "created_at",
        "updated_at",
    )
    # Never expose the raw token; only an opaque digest is shown and it is
    # read-only so it cannot be edited into something else. ``expires_at`` is
    # also read-only: invitations are only ever created through the secure
    # service flow that sets a valid expiry, and making it editable here could
    # save an invitation with a NULL/garbage expiry (NOT NULL column).
    autocomplete_fields = ("inviter", "invitee")


@admin.register(Partnership)
class PartnershipAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "public_id",
        "status",
        "member_count",
        "created_at",
        "accepted_at",
        "ended_at",
    )
    list_filter = ("status", "created_at", "accepted_at", "ended_at")
    search_fields = ("public_id", "members__user__email")
    readonly_fields = ("public_id", "created_at", "updated_at", "accepted_at", "ended_at")
    inlines = [PartnershipMemberInline, PartnershipInvitationInline]
    date_hierarchy = "created_at"

    @admin.display(description="Active members")
    def member_count(self, obj: Partnership) -> str:
        active = obj.members.filter(status="active").count()
        return str(active)


@admin.register(PartnershipInvitation)
class PartnershipInvitationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "inviter",
        "invitee_email",
        "status",
        "created_at",
        "expires_at",
        "accepted_at",
    )
    list_filter = ("status", "created_at", "expires_at")
    search_fields = ("inviter__email", "invitee_email")
    autocomplete_fields = ("inviter", "invitee")
    readonly_fields = (
        "public_id",
        "token_hash",
        "expires_at",
        "accepted_at",
        "rejected_at",
        "revoked_at",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Invitations are always created through the secure service flow that
        # emails out the raw token; admin creation would produce a token nobody
        # can see, so disallow it.
        return False


@admin.register(PartnershipEvent)
class PartnershipEventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("event_type", "partner", "actor", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("partner__public_id", "actor__email")
    readonly_fields = ("partner", "actor", "event_type", "data", "created_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("recipient", "event_type", "is_read", "created_at")
    list_filter = ("event_type", "is_read", "created_at")
    search_fields = ("recipient__email", "text")
    readonly_fields = ("recipient", "event_type", "text", "link", "created_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
