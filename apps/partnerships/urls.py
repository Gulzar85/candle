from django.urls import path

from . import views

app_name = "partnerships"

urlpatterns = [
    path("partnership/", views.partnership_home, name="partnership_home"),
    path("partnership/invite/", views.invite, name="invite"),
    path("partnership/invitations/", views.invitation_list, name="invitation_list"),
    path(
        "partnership/invitations/<uuid:public_id>/",
        views.invitation_detail,
        name="invitation_detail",
    ),
    path(
        "partnership/invitations/<uuid:public_id>/accept/",
        views.invitation_accept,
        name="invitation_accept",
    ),
    path(
        "partnership/invitations/<uuid:public_id>/reject/",
        views.invitation_reject,
        name="invitation_reject",
    ),
    path(
        "partnership/invitations/<uuid:public_id>/revoke/",
        views.invitation_revoke,
        name="invitation_revoke",
    ),
    path(
        "partnership/invitations/<uuid:public_id>/resend/",
        views.invitation_resend,
        name="invitation_resend",
    ),
    path("partnership/end/", views.partnership_end, name="partnership_end"),
    path("invite/<str:token>/", views.invite_landing, name="invite_accept"),
]
