from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Registration & verification
    path("register/", views.register, name="register"),
    path("verify-email/<str:uidb64>/<str:token>/", views.verify_email, name="verify_email"),
    path("resend-verification/", views.resend_verification, name="resend_verification"),
    # Login / logout
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    # Password reset
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path(
        "password-reset/done/",
        views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # Password change
    path("password-change/", views.PasswordChangeView.as_view(), name="password_change"),
    path(
        "password-change/done/",
        views.PasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    # Authenticated account area
    path("dashboard/", views.dashboard, name="dashboard"),
    path("profile/", views.profile, name="profile"),
    path("profile/avatar/", views.avatar_upload, name="avatar_upload"),
    path("profile/email-change/request/", views.email_change_request, name="email_change_request"),
    path("settings/", views.settings, name="settings"),
    path("settings/logout-all-devices/", views.logout_all_devices, name="logout_all_devices"),
]
