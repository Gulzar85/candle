"""Concurrency / race-condition tests for the invite and accept flows.

These use ``TransactionTestCase`` (real commits) and separate database
connections per thread so the ``select_for_update`` locking and database
constraints are genuinely exercised rather than simulated within a single
test transaction. Each test asserts a deterministic, conflict-free final
outcome no matter how the requests interleave.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from django.db import OperationalError, connections
from django.test import TransactionTestCase

from apps.accounts.models import User
from apps.partnerships.enums import InvitationStatus, PartnershipStatus
from apps.partnerships.models import Partnership, PartnershipInvitation
from apps.partnerships.services import (
    CapacityError,
    InvitationUsageError,
    NotAuthorizedError,
    accept_invitation,
    create_invitation,
)


def run_parallel(fn: Callable[[dict], object], *requests: dict) -> list[tuple[dict, object]]:
    """Run ``fn(request)`` concurrently on separate DB connections.

    Django's ``connections`` handler is thread-local, so each worker thread
    lazily opens (and here explicitly tears down) its own physical connection,
    letting the service functions participate in real row locking rather than a
    simulated single-connection interleave. Returns ``(request, outcome)`` pairs
    where each outcome is either the return value or the raised exception.
    """
    results: list[tuple[dict, object]] = []
    lock = threading.Lock()

    def worker(request: dict) -> None:
        out: object
        try:
            out = fn(request)
        except Exception as exc:  # noqa: BLE001
            out = exc
        finally:
            try:
                connections["default"].close()
            except Exception:  # noqa: BLE001
                pass
        with lock:
            results.append((request, out))

    threads = [threading.Thread(target=worker, args=(r,)) for r in requests]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


class ConcurrentAcceptTest(TransactionTestCase):
    """Two requests race to accept the same invitation.

    Both requests are for the same (valid) recipient, so at most one may
    succeed — the ``select_for_update`` single-use guarantee must hold no matter
    how the requests interleave. The loser surfaces as one of the conflict
    exceptions; a PostgreSQL deadlock between the overlapping row locks is
    converted to ``CapacityError`` by the service.
    """

    def test_single_accept_wins_others_fail(self):
        alice = User.objects.create_user(email="a@example.com", password="x")
        User.objects.create_user(email="b@example.com", password="x")
        invitation = create_invitation(alice, "b@example.com")
        inv_id, part_id = invitation.pk, invitation.partnership_id

        def _accept(request: dict) -> Partnership:
            inv = PartnershipInvitation.objects.get(pk=request["invitation_id"])
            user = User.objects.get(email=request["email"])
            return accept_invitation(inv, user)

        outcomes = run_parallel(
            _accept,
            {"invitation_id": inv_id, "email": "b@example.com"},
            {"invitation_id": inv_id, "email": "b@example.com"},
        )
        failures = [o for r, o in outcomes if isinstance(o, Exception)]
        successes = [o for r, o in outcomes if not isinstance(o, Exception)]
        self.assertEqual(len(successes), 1)  # exactly one accept wins
        self.assertEqual(len(failures), 1)  # the other must fail cleanly
        loser = failures[0]
        self.assertIsInstance(
            loser,
            (InvitationUsageError, CapacityError, NotAuthorizedError, OperationalError),
        )

        partnership = Partnership.objects.get(pk=part_id)
        self.assertEqual(partnership.status, PartnershipStatus.ACTIVE)
        self.assertEqual(
            partnership.members.filter(status="active").count(),
            2,  # owner + exactly one accepted partner
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, InvitationStatus.ACCEPTED)


class ConcurrentCreateTest(TransactionTestCase):
    """Two users invite each other at almost the same time (mutual race)."""

    def test_mutual_invites_never_create_two_partnerships(self):
        User.objects.create_user(email="a@example.com", password="x")
        User.objects.create_user(email="b@example.com", password="x")

        def _invite(request: dict) -> PartnershipInvitation:
            inviter = User.objects.get(email=request["inviter_email"])
            return create_invitation(inviter, request["invitee_email"])

        outcomes = run_parallel(
            _invite,
            {"inviter_email": "a@example.com", "invitee_email": "b@example.com"},
            {"inviter_email": "b@example.com", "invitee_email": "a@example.com"},
        )

        # The mutual "you invite me while I invite you" race must never produce
        # two partnerships; exactly one invite (or a mutual-detection rejection)
        # resolves, and at most one partnership/invitation row ever exists.
        successes = [o for _, o in outcomes if not isinstance(o, Exception)]
        self.assertLessEqual(len(successes), 1)
        self.assertLessEqual(Partnership.objects.count(), 1)
        self.assertLessEqual(PartnershipInvitation.objects.count(), 1)
