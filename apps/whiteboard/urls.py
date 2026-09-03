from django.urls import path

from . import views
from .api import (
    OperationListView,
    OperationSubmitView,
    WhiteboardRenameView,
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
]
