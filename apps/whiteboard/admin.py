"""Admin for whiteboards — read-mostly and defensive.

Operations are immutable history. Admins may view them for debugging and
auditing but must not modify them. Whiteboard metadata (title, status) may be
editable by staff for administrative purposes (e.g. archiving an abusive board).
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from .models import Whiteboard, WhiteboardOperation


class WhiteboardOperationInline(admin.TabularInline):  # type: ignore[type-arg]
    model = WhiteboardOperation
    extra = 0
    readonly_fields = (
        "operation_id",
        "sequence",
        "actor",
        "operation_type",
        "base_version",
        "resulting_version",
        "created_at",
    )
    fields = readonly_fields
    ordering = ("sequence",)
    show_change_link = False
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Whiteboard | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Whiteboard | None = None) -> bool:
        return False


@admin.register(Whiteboard)
class WhiteboardAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "public_id",
        "partnership_id",
        "title",
        "status",
        "version",
        "operation_count",
        "last_operation_at",
        "created_at",
    )
    list_select_related = ("partnership",)
    list_filter = ("status",)
    search_fields = ("public_id", "partnership__public_id", "title")
    ordering = ("-created_at",)
    readonly_fields = (
        "public_id",
        "partnership",
        "version",
        "created_at",
        "updated_at",
        "last_operation_at",
        "archived_at",
    )
    inlines = [WhiteboardOperationInline]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Whiteboard | None = None) -> bool:
        return True

    def has_delete_permission(self, request: HttpRequest, obj: Whiteboard | None = None) -> bool:
        return False

    @admin.display(description="Operations")
    def operation_count(self, obj: Whiteboard) -> int:
        return obj.operations.count()


@admin.register(WhiteboardOperation)
class WhiteboardOperationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "id",
        "whiteboard_id",
        "operation_type",
        "sequence",
        "actor_id",
        "resulting_version",
        "created_at",
    )
    list_filter = ("operation_type",)
    search_fields = ("operation_id",)
    ordering = ("-created_at",)
    readonly_fields = (
        "whiteboard",
        "operation_id",
        "sequence",
        "actor",
        "operation_type",
        "payload",
        "base_version",
        "resulting_version",
        "created_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: WhiteboardOperation | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: WhiteboardOperation | None = None
    ) -> bool:
        return False
