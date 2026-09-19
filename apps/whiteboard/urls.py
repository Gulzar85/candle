from django.urls import path

from . import views
from .api import (
    OperationListView,
    OperationSubmitView,
    WhiteboardHistoryView,
    WhiteboardImportView,
    WhiteboardRenameView,
    WhiteboardRestoreView,
    WhiteboardStateView,
)

app_name = "whiteboard"

urlpatterns = [
    # Page view.
    path(
        "partnership/<uuid:public_id>/whiteboard/",
        views.whiteboard_home,
        name="whiteboard_home",
    ),
    # Archive / restore (metadata; server-rendered form target, not JSON API).
    path(
        "partnership/<uuid:public_id>/whiteboard/archive/",
        views.whiteboard_archive_toggle,
        name="whiteboard_archive_toggle",
    ),
    # API: submit operations.
    path(
        "api/whiteboards/<uuid:public_id>/operations/",
        OperationSubmitView.as_view(),
        name="api_operation_submit",
    ),
    # API: load reconstructed state.
    path(
        "api/whiteboards/<uuid:public_id>/",
        WhiteboardStateView.as_view(),
        name="api_whiteboard_state",
    ),
    # API: rename board (metadata; not an operation).
    path(
        "api/whiteboards/<uuid:public_id>/rename/",
        WhiteboardRenameView.as_view(),
        name="api_whiteboard_rename",
    ),
    # API: load operations (paginated, version-range).
    path(
        "api/whiteboards/<uuid:public_id>/operations/list/",
        OperationListView.as_view(),
        name="api_operation_list",
    ),
    # API: restore to an earlier version (creates a new restore_version op).
    path(
        "api/whiteboards/<uuid:public_id>/restore/",
        WhiteboardRestoreView.as_view(),
        name="api_whiteboard_restore",
    ),
    # API: human-readable history (newest-first, paginated).
    path(
        "api/whiteboards/<uuid:public_id>/history/",
        WhiteboardHistoryView.as_view(),
        name="api_whiteboard_history",
    ),
    # API: import a previously-exported JSON board.
    path(
        "api/whiteboards/<uuid:public_id>/import/",
        WhiteboardImportView.as_view(),
        name="api_whiteboard_import",
    ),
]
