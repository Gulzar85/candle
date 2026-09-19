"""Account views.

Authentication follows Django's own framework (LoginView, LogoutView,
PasswordResetView, PasswordChangeView) rather than introducing a separate
auth library. Custom logic here covers: email verification, the
unverified-account gate on login, and focused cache-based rate limiting.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth.views import (
    LoginView as DjangoLoginView,
)
from django.contrib.auth.views import (
    LogoutView as DjangoLogoutView,
)
from django.contrib.auth.views import (
    PasswordChangeDoneView as DjangoPasswordChangeDoneView,
)
from django.contrib.auth.views import (
    PasswordChangeView as DjangoPasswordChangeView,
)
from django.contrib.auth.views import (
    PasswordResetCompleteView as DjangoPasswordResetCompleteView,
)
from django.contrib.auth.views import (
    PasswordResetConfirmView as DjangoPasswordResetConfirmView,
)
from django.contrib.auth.views import (
    PasswordResetDoneView as DjangoPasswordResetDoneView,
)
from django.contrib.auth.views import (
    PasswordResetView as DjangoPasswordResetView,
)
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.http import require_http_methods

from apps.partnerships.enums import PartnershipStatus
from apps.partnerships.policies import safe_display_name
from apps.partnerships.selectors import get_active_partnership, get_partner
from apps.whiteboard.selectors import whiteboard_for_partnership

from . import ratelimit
from .forms import (
    AvatarForm,
    CandleLoginForm,
    CandlePasswordResetForm,
    EmailChangeRequestForm,
    ProfileForm,
    RegistrationForm,
    ResendVerificationForm,
)
from .models import EmailVerificationToken, User
from .services import send_email_change_confirmation, send_verification_email

logger = logging.getLogger("apps.accounts")

RATE_LIMITS = {
    "login": {"limit": 6, "window": 300, "cooldown": 300},
    "register": {"limit": 5, "window": 3600, "cooldown": 900},
    "password_reset": {"limit": 3, "window": 900, "cooldown": 900},
    "password_reset_confirm": {"limit": 6, "window": 900, "cooldown": 900},
    "resend": {"limit": 3, "window": 900, "cooldown": 600},
}


def client_ip(request: HttpRequest) -> str:
    """Best-effort client IP honoring the trusted proxy header in production."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded is not None:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _rate_limited(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/rate_limited.html", status=429)


# --------------------------------------------------------------------------- #
# Registration with email verification
# --------------------------------------------------------------------------- #
def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    result = ratelimit.check("register", client_ip(request), **RATE_LIMITS["register"])
    if not result.allowed:
        return _rate_limited(request)

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            send_verification_email(user)
            logger.info("user.registered ip=%s user_id=%s", client_ip(request), user.pk)
            return render(
                request,
                "accounts/verify_sent.html",
                {"done": True, "email": user.email},
            )
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


# --------------------------------------------------------------------------- #
# Email verification (also handles email-change confirmation)
# --------------------------------------------------------------------------- #
def resend_verification(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ResendVerificationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            result = ratelimit.check(
                "resend",
                f"{client_ip(request)}:{email.lower()}",
                **RATE_LIMITS["resend"],
            )
            if not result.allowed:
                return _rate_limited(request)
            user = User.objects.filter(email__iexact=email).first()
            if user is not None and not user.email_verified:
                send_verification_email(user)
        # Always show a generic confirmation page (enumeration-resistant).
        return render(request, "accounts/resend_done.html", {})
    form = ResendVerificationForm()
    return render(request, "accounts/resend.html", {"form": form})


def verify_email(request: HttpRequest, uidb64: str, token: str) -> HttpResponse:
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = get_user_model().objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None:
        return render(request, "accounts/verification_invalid.html", status=400)

    record = EmailVerificationToken.consume_any(user, token)
    if record is None:
        return render(request, "accounts/verification_invalid.html", status=400)

    if record.purpose == EmailVerificationToken.PURPOSE_CHANGE:
        new_email = record.new_email or user.email
        user.email = new_email
        user.email_pending = ""
        user.email_verified = True
        user.save(update_fields=["email", "email_pending", "email_verified", "updated_at"])
        logger.info("email.change_confirmed user_id=%s", user.pk)
        return render(
            request,
            "accounts/email_change_done.html",
            {"new_email": new_email},
        )

    user.email_verified = True
    user.save(update_fields=["email_verified", "updated_at"])
    logger.info("email.verified user_id=%s", user.pk)
    messages.success(request, "Your email address is verified. You can now sign in.")
    return redirect("accounts:login")


# --------------------------------------------------------------------------- #
# Login / Logout
# --------------------------------------------------------------------------- #
class LoginView(DjangoLoginView):
    template_name = "accounts/login.html"
    authentication_form = CandleLoginForm
    redirect_authenticated_user = True

    def _attempt_key(self) -> str:
        email = self.request.POST.get("username", "") if self.request.method == "POST" else ""
        return f"{client_ip(self.request)}:{email.lower()}"

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        result = ratelimit.check("login", self._attempt_key(), **RATE_LIMITS["login"])
        if not result.allowed:
            return _rate_limited(request)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form: CandleLoginForm) -> HttpResponse:
        user = form.get_user()
        if not user.email_verified:
            # Do not establish a session; ask the owner to verify first.
            auth_logout(self.request)
            send_verification_email(user)
            messages.error(
                self.request,
                "Please verify your email address. We sent you a new link.",
            )
            logger.info(
                "login.blocked_unverified ip=%s user_id=%s", client_ip(self.request), user.pk
            )
            return self.form_invalid(form)
        ratelimit.reset("login", self._attempt_key())
        logger.info("login.success ip=%s user_id=%s", client_ip(self.request), user.pk)
        response = super().form_valid(form)
        if form.cleaned_data.get("remember"):
            self.request.session.set_expiry(1209600)  # 2 weeks
        else:
            self.request.session.set_expiry(0)  # browser-session cookie
        return response

    def form_invalid(self, form: CandleLoginForm) -> HttpResponse:
        logger.info(
            "login.failed ip=%s email=%s",
            client_ip(self.request),
            self.request.POST.get("username", "").lower(),
        )
        return super().form_invalid(form)


class LogoutView(DjangoLogoutView):
    http_method_names = ["post"]


class _RateLimitedPasswordResetView(DjangoPasswordResetView):
    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        email = request.POST.get("email", "")
        result = ratelimit.check(
            "password_reset",
            f"{client_ip(request)}:{email.lower()}",
            **RATE_LIMITS["password_reset"],
        )
        if not result.allowed:
            return _rate_limited(request)
        # Logged unconditionally, before Django's form decides whether the
        # address matches an account — the response stays enumeration-generic
        # either way; this is server-side-only visibility into reset activity.
        logger.info("password_reset.requested ip=%s email=%s", client_ip(request), email.lower())
        return super().post(request, *args, **kwargs)


class PasswordResetView(_RateLimitedPasswordResetView):
    template_name = "registration/password_reset_form.html"
    form_class = CandlePasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(DjangoPasswordResetDoneView):
    template_name = "registration/password_reset_done.html"


def _invalidate_other_sessions(user_pk: int, keep_session_key: str | None = None) -> int:
    """Delete all active sessions for a user except the current one.

    Returns the number of sessions removed.  Used after a password reset so a
    potentially compromised pre-reset session cannot remain valid.
    """
    from django.contrib.sessions.models import Session

    removed = 0
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        if keep_session_key and session.session_key == keep_session_key:
            continue
        try:
            data = session.get_decoded()
        except Exception:  # noqa: BLE001 - skip undecodable sessions
            data = {}
        if int(data.get("_auth_user_id", 0) or 0) == user_pk:
            session.delete()
            removed += 1
    return removed


class PasswordResetConfirmView(DjangoPasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def post(self, request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        uidb64 = str(self.kwargs.get("uidb64", ""))
        result = ratelimit.check(
            "password_reset_confirm",
            f"{client_ip(request)}:{uidb64}",
            **RATE_LIMITS["password_reset_confirm"],
        )
        if not result.allowed:
            return _rate_limited(request)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form: PasswordResetForm) -> HttpResponse:
        response = super().form_valid(form)
        # Invalidate every pre-reset session for this user (except the current
        # one) so a compromised session cannot survive the password change.
        user_pk = getattr(form.user, "pk", None)
        if user_pk:
            _invalidate_other_sessions(int(user_pk), self.request.session.session_key)
        return response


class PasswordResetCompleteView(DjangoPasswordResetCompleteView):
    template_name = "registration/password_reset_complete.html"


class PasswordChangeView(DjangoPasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:password_change_done")

    def form_valid(self, form: PasswordChangeForm) -> HttpResponse:
        response = super().form_valid(form)
        # Rotate the auth hash so the current session keeps working while old
        # password sessions are invalidated (password change == session change).
        update_session_auth_hash(self.request, form.user)
        return response


class PasswordChangeDoneView(DjangoPasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"


# --------------------------------------------------------------------------- #
# Authenticated dashboard, profile and settings
# --------------------------------------------------------------------------- #
@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    user = request.user

    # The dashboard is the product home: each signed-in user has at most one
    # active membership (DB-enforced), and that partnership has exactly one
    # whiteboard. We branch on the partnership's *status*: PENDING is not yet a
    # shareable space.
    partnership = get_active_partnership(user)
    is_pending = partnership is not None and partnership.status == PartnershipStatus.PENDING
    active_partnership = None if is_pending else partnership
    whiteboard = None
    whiteboard_url = None
    if active_partnership is not None:
        whiteboard = whiteboard_for_partnership(active_partnership)
        whiteboard_url = reverse(
            "whiteboard:whiteboard_home", args=[str(active_partnership.public_id)]
        )

    partner = get_partner(user, partnership) if partnership is not None else None
    partner_name = safe_display_name(partner) if partner and not is_pending else None
    pending_partner_name = safe_display_name(partner) if partner and is_pending else None

    return render(
        request,
        "accounts/dashboard.html",
        {
            "page_title": "Dashboard",
            "active_partnership": active_partnership,
            "pending_partnership": partnership if is_pending else None,
            "whiteboard": whiteboard,
            "whiteboard_url": whiteboard_url,
            "partner_name": partner_name,
            "pending_partner_name": pending_partner_name,
            "profile": user.profile,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def profile(request: HttpRequest) -> HttpResponse:
    user = request.user
    profile = user.profile

    if request.method == "POST":
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "accounts/_profile_form.html",
                    {"form": form, "profile": profile},
                )
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=profile)

    return render(
        request,
        "accounts/profile.html",
        {
            "page_title": "Profile",
            "form": form,
            "profile": profile,
            "avatar_form": AvatarForm(),
            "email_change_form": EmailChangeRequestForm(user=user),
        },
    )


@login_required
@require_http_methods(["POST"])
def avatar_upload(request: HttpRequest) -> HttpResponse:
    form = AvatarForm(request.POST, request.FILES)
    if form.is_valid():
        form.save(request.user)
        messages.success(request, "Avatar updated.")
    else:
        error = form.errors.get("avatar", ["Could not process the image."])
        messages.error(request, error[0] if isinstance(error, list) else error)
    return redirect("accounts:profile")


@login_required
@require_http_methods(["POST"])
def logout_all_devices(request: HttpRequest) -> HttpResponse:
    """End every active session for the current user, including this one."""
    from django.contrib import messages
    from django.contrib.sessions.models import Session

    count = 0
    for session in Session.objects.filter(expire_date__gt=timezone.now()).exclude(
        session_key=request.session.session_key
    ):
        try:
            data = session.get_decoded()
        except Exception:  # noqa: BLE001 - skip undecodable
            data = {}
        if int(data.get("_auth_user_id", 0) or 0) == request.user.pk:
            session.delete()
            count += 1

    auth_logout(request)
    messages.success(request, f"You signed out {count} other device(s).")
    return redirect("core:home")


@login_required
@require_http_methods(["GET", "POST"])
def settings(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "accounts/settings.html",
        {
            "page_title": "Settings",
            "profile": request.user.profile,
            "email_change_form": EmailChangeRequestForm(user=request.user),
        },
    )


@login_required
@require_http_methods(["POST"])
def email_change_request(request: HttpRequest) -> HttpResponse:
    form = EmailChangeRequestForm(request.user, request.POST)
    if form.is_valid():
        new_email = form.cleaned_data["new_email"]
        user = request.user
        user.email_pending = new_email
        user.save(update_fields=["email_pending", "updated_at"])
        send_email_change_confirmation(user, new_email)
        messages.success(request, "We sent a confirmation link to your new email address.")
        return redirect("accounts:settings")

    error_list = form.errors.get("new_email", [])
    if error_list:
        messages.error(request, error_list[0])
    else:
        messages.error(request, "Could not request the email change.")
    return redirect("accounts:settings")
