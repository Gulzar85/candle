"""Partnership forms.

Forms validate input and render with the design system; business rules are
enforced authoritatively in the service layer (which the views call).
"""

from __future__ import annotations

from django import forms


class InviteForm(forms.Form):
    """The email address of the person a user wants to connect with."""

    email = forms.EmailField(
        label="Partner's email address",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "placeholder": "partner@example.com",
                "autocomplete": "email",
                "autocapitalize": "none",
                "class": "w-full h-11 rounded-lg border bg-background px-3 py-2 "
                "text-sm placeholder:text-muted-foreground focus:outline-none "
                "focus:ring-2 focus:ring-ring focus:border-ring disabled:opacity-50 "
                "min-h-[44px]",
            }
        ),
        help_text="We'll send them a private, time-limited invitation link.",
    )

    def clean_email(self) -> str:
        email = (self.cleaned_data.get("email") or "").strip().lower()
        return email


class ConfirmEndForm(forms.Form):
    """Typed-confirmation field to make ending irreversible an explicit action."""

    confirm = forms.CharField(
        label='Type "end" to confirm',
        max_length=10,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autocapitalize": "none",
                "class": "w-full h-11 rounded-lg border bg-background px-3 py-2 text-sm "
                "placeholder:text-muted-foreground focus:outline-none focus:ring-2 "
                "focus:ring-ring focus:border-ring disabled:opacity-50 min-h-[44px]",
            }
        ),
    )

    def clean_confirm(self) -> str:
        value = (self.cleaned_data.get("confirm") or "").strip().lower()
        if value != "end":
            raise forms.ValidationError('Please type "end" to confirm.')
        return value
