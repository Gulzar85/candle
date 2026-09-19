"""Account forms.

Authentication and password forms build on Django's own framework rather than
re-implementing any password logic. Custom forms here only adjust labels,
widgets, model integration, and the specific business rules (uniqueness,
email-change) that belong to this application.
"""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UsernameField
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .avatars import process_avatar, validate_upload_size
from .models import ACCENT_COLOR_CHOICES, Profile, User

COMMON_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "Africa/Abidjan",
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Seoul",
    "Asia/Tokyo",
    "Australia/Brisbane",
    "Australia/Sydney",
]


def normalize_email(value: str) -> str:
    return get_user_model().objects.normalize_email(value or "")


class RegistrationForm(forms.ModelForm):
    """Registration with email, optional name and confirmed password."""

    display_name = forms.CharField(required=False, max_length=150, label="Display name")
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "autocapitalize": "none"}
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password", "autocapitalize": "none"}
        ),
    )
    terms = forms.BooleanField(label="I agree to the Terms of Service and Privacy Policy.")

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name"]
        field_classes = {"email": UsernameField}
        widgets = {
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "first_name": forms.TextInput(attrs={"autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name"}),
        }

    def clean_email(self) -> str:
        return normalize_email(self.cleaned_data.get("email", ""))

    def clean_password2(self) -> str:
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise ValidationError("The two password fields didn't match.")
        return password2

    def _post_clean(self) -> None:
        # Run Django's password validators against the chosen password.
        super()._post_clean()
        password = self.cleaned_data.get("password2")
        if password:
            try:
                validate_password(password, self.instance)
            except ValidationError as error:
                self.add_error("password2", error)

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password2"])
        user.email_verified = False
        if commit:
            user.save()
            # The post_save signal created this user's Profile; apply the
            # optional display name on top of it.
            profile = user.profile
            profile.display_name = self.cleaned_data.get("display_name", "")
            profile.save()
        return user


class CandleLoginForm(AuthenticationForm):
    username = UsernameField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    remember = forms.BooleanField(
        required=False,
        label="Keep me signed in",
        help_text="Stay signed in on this device for two weeks.",
        widget=forms.CheckboxInput(attrs={"class": "mt-1"}),
    )


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )


class EmailChangeRequestForm(forms.Form):
    new_email = forms.EmailField(
        label="New email address",
        widget=forms.EmailInput(attrs={"autocomplete": "off"}),
    )

    def __init__(self, user: User | None = None, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_new_email(self) -> str:
        new_email = normalize_email(self.cleaned_data.get("new_email", ""))
        if self.user is not None and new_email.lower() == self.user.email.lower():
            raise ValidationError("That is your current email address.")
        if get_user_model()._default_manager.filter(email__iexact=new_email).exists():
            raise ValidationError("An account already uses that email address.")
        return new_email


class ProfileForm(forms.ModelForm):
    accent_color = forms.ChoiceField(
        choices=[(c, c) for c in ACCENT_COLOR_CHOICES],
        widget=forms.RadioSelect,
        label="Identity color",
    )

    class Meta:
        model = Profile
        fields = ["display_name", "pronouns", "bio", "accent_color", "timezone", "locale"]
        widgets = {
            "display_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "pronouns": forms.TextInput(
                attrs={"autocomplete": "off", "placeholder": "e.g. she/her"}
            ),
            "bio": forms.Textarea(
                attrs={"rows": 2, "maxlength": 280, "placeholder": "A short line about you"}
            ),
            "timezone": forms.Select(choices=[(tz, tz) for tz in COMMON_TIMEZONES]),
            "locale": forms.Select(
                choices=[("en", "English"), ("fr", "French"), ("es", "Spanish")]
            ),
        }


class AvatarForm(forms.Form):
    avatar = forms.ImageField(label="Avatar", validators=[validate_upload_size])

    def save(self, user: User) -> None:
        profile = user.profile
        content = process_avatar(self.cleaned_data["avatar"])
        profile.avatar.save("avatar.png", content, save=True)


class CandlePasswordResetForm(PasswordResetForm):
    """Password reset form using the branded HTML/plain-text templates."""

    html_email_template_name = "registration/password_reset_email.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
