import hashlib
import secrets
import uuid
from datetime import timedelta

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

VERIFICATION_TOKEN_LIFETIME = timedelta(hours=24)


def hash_token(token: str) -> str:
    """Return a non-reversible digest of a raw verification token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class UserManager(BaseUserManager["User"]):
    """Manager for the email-based custom User model.

    The full address is normalized to lowercase (local part included) before
    persistence so lookups are unambiguous and the address can serve as a
    stable, case-insensitive login identifier. Django's default behaviour only
    lowercases the domain, which would otherwise make the same mailbox
    addressable under two spellings.
    """

    use_in_migrations = True

    @staticmethod
    def normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    def get_by_natural_key(self, username: str) -> "User":
        return self.get(**{self.model.USERNAME_FIELD: self.normalize_email(username)})

    def _create_user(
        self,
        email: str,
        password: str | None,
        *,
        is_staff: bool = False,
        is_superuser: bool = False,
        email_verified: bool = False,
        **extra_fields: object,
    ) -> "User":
        if not email:
            raise ValueError("The given email must be set")
        email = self.normalize_email(email)
        user = self.model(
            email=email,
            is_staff=is_staff,
            is_superuser=is_superuser,
            email_verified=email_verified,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> "User":
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Application user identity, authenticated by email.

    This model deliberately carries only authentication/identity fields.
    Products that belong to authenticated identity (e.g. a display name) and
    partner/whiteboard relationships are kept on separate models so that the
    identity model stays independent of future domain logic.
    """

    email = models.EmailField("email address", unique=True, db_index=True)
    email_verified = models.BooleanField(default=False)
    # Pending email address awaiting owner confirmation via verification link.
    email_pending = models.EmailField(blank=True)

    # Stable public identifier for cross-resource references (e.g. realtime
    # presence / actor messages). Never exposed as an internal sequence; matches
    # the public_id convention used by Partnership / Whiteboard / Invitation.
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)

    first_name = models.CharField("first name", max_length=150, blank=True)
    last_name = models.CharField("last name", max_length=150, blank=True)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ["-date_joined"]

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)

    def get_full_name(self) -> str:
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.email

    def get_short_name(self) -> str:
        return self.first_name or self.email

    def issue_verification_token(
        self, purpose: str = "verify_email", new_email: str = ""
    ) -> bytes:
        """Create a new single-use verification token for this user.

        Returns the raw token bytes; the URL-safe encoded form is what the
        user receives in the verification email.
        """
        raw = secrets.token_urlsafe(32)
        self.email_verification_tokens.filter(purpose=purpose, used=False).delete()
        EmailVerificationToken.objects.create(
            user=self,
            token_hash=hash_token(raw),
            purpose=purpose,
            new_email=new_email,
            used=False,
            expires_at=timezone.now() + VERIFICATION_TOKEN_LIFETIME,
        )
        return raw.encode("ascii")


# Preset palette offered for a profile's accent color — the same hues used for
# whiteboard stroke swatches, so a partner's identity color feels native to
# the drawing surface rather than an arbitrary picker value.
ACCENT_COLOR_CHOICES = [
    "#2563eb",  # blue
    "#dc2626",  # red
    "#16a34a",  # green
    "#d97706",  # amber
    "#7c3aed",  # violet
    "#db2777",  # pink
    "#0891b2",  # cyan
    "#171717",  # near-black
]


def default_accent_color() -> str:
    """Pick a random preset so two fresh profiles are visually distinct by default."""
    return secrets.choice(ACCENT_COLOR_CHOICES)


class Profile(models.Model):
    """Optional, product-level identity information kept apart from the User.

    Authentication identity (the User) and product identity (a display name,
    avatar, regional preferences) are intentionally separate so neither import
    is coupled to the other and future partner/whiteboard domains stay clean.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="profile", verbose_name="user"
    )
    display_name = models.CharField(max_length=150, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True)
    bio = models.CharField(max_length=280, blank=True, help_text="A short line about you.")
    pronouns = models.CharField(max_length=30, blank=True)
    accent_color = models.CharField(
        max_length=7,
        default=default_accent_color,
        help_text="Your identity color across avatars and presence.",
    )
    timezone = models.CharField(max_length=64, default="UTC", blank=True)
    locale = models.CharField(max_length=10, default="en")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "profile"
        verbose_name_plural = "profiles"

    def __str__(self) -> str:
        return f"Profile for {self.user.email}"

    def get_display_name(self) -> str:
        return self.display_name or self.user.get_short_name() or self.user.email

    def get_initials(self) -> str:
        name = (self.display_name or self.user.get_short_name() or self.user.email).strip()
        parts = name.replace("@", " ").split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return (parts[0][0] if parts else "C").upper()


class EmailVerificationToken(models.Model):
    """Server-side, single-use token used for email verification and email change.

    Only a SHA-256 digest of the token is stored; the raw token travels only in
    the verification link. A token can be used at most once, after which the
    record is removed, and it expires after VERIFICATION_TOKEN_LIFETIME.
    """

    PURPOSE_VERIFY = "verify_email"
    PURPOSE_CHANGE = "change_email"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="email_verification_tokens"
    )
    token_hash = models.CharField(max_length=64)
    purpose = models.CharField(max_length=32)
    new_email = models.EmailField(blank=True)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "email verification token"
        verbose_name_plural = "email verification tokens"
        constraints = [
            models.UniqueConstraint(fields=["user", "purpose"], name="uniq_user_active_token")
        ]

    def __str__(self) -> str:
        return f"{self.purpose} for {self.user.email}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @classmethod
    def consume_any(cls, user: "User", raw_token: str) -> "EmailVerificationToken | None":
        """Validate and consume a raw token for any purpose, returning it or None.

        The token must match the stored digest, be unexpired and unused. On
        success the record is deleted so it cannot be replayed (single-use).
        The caller inspects ``record.purpose`` to decide how to apply it.
        """
        digest = hash_token(raw_token)
        token = cls.objects.filter(user=user, token_hash=digest, used=False).first()
        if token is None or token.is_expired:
            return None
        token.delete()
        return token
