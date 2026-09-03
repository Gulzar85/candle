from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile, User


@receiver(post_save, sender=User)
def ensure_profile(sender: type[User], instance: User, created: bool, **kwargs: object) -> None:
    """Guarantee every User has a Profile so product identity is always safe.

    Registration already creates a Profile explicitly; this signal makes it the
    single source of truth so staff and superusers created through the ORM or
    management commands are covered too. It is idempotent.
    """
    if created:
        Profile.objects.get_or_create(user=instance)
