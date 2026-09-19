"""Whiteboard operation service (the domain boundary's write path).

This is the only place that mutates whiteboard operation state. The view/API
layer never touches ``WhiteboardOperation`` directly — it calls this service
after authenticating and authorizing the caller.

A submit is atomic and row-locked (on PostgreSQL; SQLite has no row-level
locking, so the surrounding transaction serializes the whole database instead
-- see ``OPTIONS["transaction_mode"]`` in settings):

    BEGIN TRANSACTION
        lock whiteboard row                  (select_for_update)
        reject if archived                   (read-only)
        for each operation, in order:
            dedupe by operation_id           (idempotency within a batch)
            return existing if already seen  (cross-request idempotency)
            stale check: base_version == expected
            version/sequence = ++expected
            create row (unique constraints back this)
        update whiteboard.version / last_operation_at
    COMMIT                       (any failure -> ROLLBACK, nothing partial)

Server-authoritative: actor, created_at, sequence, base_version/resulting_version
are always computed here. Client-supplied values for those are rejected by the
validator. We never keep a transaction open across I/O: the whole body is a
fast, pure-DB critical section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from .enums import WhiteboardStatus
from .errors import (
    InvalidOperationError,
    StaleVersionError,
    WhiteboardReadOnlyError,
)
from .models import Whiteboard, WhiteboardOperation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.accounts.models import User


@dataclass
class OperationAck:
    """The server-authoritative acknowledgement for one accepted operation."""

    operation_id: str
    sequence: int
    resulting_version: int
    duplicate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "sequence": self.sequence,
            "version": self.resulting_version,
            "duplicate": self.duplicate,
        }


@dataclass
class SubmitResult:
    """Result of a (possibly batched) submit.

    ``acks`` is returned in the same order as the accepted operations (duplicates
    removed). ``new_versions`` is the count of operations actually applied (i.e.
    not duplicates). The board's resulting version is the last ack's version.
    """

    acks: list[OperationAck] = field(default_factory=list)
    applied: int = 0

    @property
    def version(self) -> int:
        return self.acks[-1].resulting_version if self.acks else 0


class WhiteboardOperationService:
    """Authorizes, validates, versions, sequences and persists operations."""

    def __init__(self, validator: Any) -> None:
        # Injected so the service is testable without importing DRF or the HTTP
        # layer; the validator does pure structural validation.
        self.validator = validator

    # ------------------------------------------------------------------ public
    def submit(
        self,
        whiteboard: Whiteboard,
        actor: User,
        operation: dict[str, Any],
        *,
        wire_size: int | None = None,
    ) -> SubmitResult:
        """Submit a single operation; duplicate submissions are safe."""
        return self.submit_batch(
            whiteboard,
            actor,
            [operation],
            wire_sizes=[wire_size] if wire_size is not None else None,
        )

    def submit_batch(
        self,
        whiteboard: Whiteboard,
        actor: User,
        operations: list[dict[str, Any]],
        *,
        wire_sizes: list[int | None] | None = None,
    ) -> SubmitResult:
        """Submit an ordered list of operations in one atomic transaction.

        Idempotency across requests: if ``operation_id`` is already persisted,
        the existing row is returned with ``duplicate=True`` and the version is
        **not** bumped again (a retry cannot double-apply).
        """
        if not operations:
            raise InvalidOperationError("No operations submitted.")

        # Pre-validate structure before taking the lock, so malformed input
        # never reserves a version slot. Pair each op with its wire size.
        sized = list(zip(operations, wire_sizes or [None] * len(operations), strict=False))
        for op, size in sized:
            self.validator.validate(op, byte_length=size)

        with transaction.atomic():
            locked = Whiteboard.objects.select_for_update().get(pk=whiteboard.pk)
            self._ensure_writable(locked)

            result = SubmitResult()
            seen: set[str] = set()
            expected = locked.version
            now = timezone.now()

            for op, _size in sized:
                op_id = str(op["operation_id"])

                if op_id in seen:
                    # Duplicate within this batch: ignore the repeat.
                    continue

                existing = WhiteboardOperation.objects.filter(
                    whiteboard=locked, operation_id=op_id
                ).first()
                if existing is not None:
                    # Cross-request idempotent retry of an already-applied op.
                    result.acks.append(
                        OperationAck(
                            operation_id=op_id,
                            sequence=existing.sequence,
                            resulting_version=existing.resulting_version,
                            duplicate=True,
                        )
                    )
                    seen.add(op_id)
                    continue

                base_version = op["base_version"]
                if base_version != expected:
                    raise StaleVersionError(current_version=expected, client_version=base_version)

                sequence = expected + 1
                WhiteboardOperation.objects.create(
                    whiteboard=locked,
                    operation_id=op_id,
                    sequence=sequence,
                    actor=actor,
                    operation_type=op["operation_type"],
                    payload=op.get("payload", {}),
                    base_version=base_version,
                    resulting_version=sequence,
                )
                result.acks.append(
                    OperationAck(
                        operation_id=op_id,
                        sequence=sequence,
                        resulting_version=sequence,
                    )
                )
                result.applied += 1
                expected = sequence
                seen.add(op_id)

            if result.applied:
                locked.version = expected
                locked.last_operation_at = now
                locked.save(update_fields=["version", "last_operation_at", "updated_at"])

            return result

    # ----------------------------------------------------------------- private
    def _ensure_writable(self, whiteboard: Whiteboard) -> None:
        if whiteboard.is_archived:
            raise WhiteboardReadOnlyError()


class WhiteboardMetadataService:
    """Mutates whiteboard *metadata* (title), separate from the op ledger.

    ``WhiteboardOperationService`` owns the append-only drawing history. Renaming
    the board is a presentation concern that lives on ``Whiteboard.title`` and
    does not create an operation, so it is deliberately not part of the op
    service and never touches the version/sequence ledger.
    """

    TITLE_TRIM = " \t\r\n"

    def rename(self, whiteboard: Whiteboard, title: str) -> str:
        clean = (title or "").strip(self.TITLE_TRIM)
        if not clean:
            raise InvalidOperationError("Title cannot be blank.")
        if len(clean) > whiteboard._meta.get_field("title").max_length:
            raise InvalidOperationError(
                f"Title must be at most "
                f"{whiteboard._meta.get_field('title').max_length} characters."
            )
        with transaction.atomic():
            locked = Whiteboard.objects.select_for_update().get(pk=whiteboard.pk)
            locked.title = clean
            locked.save(update_fields=["title", "updated_at"])
        return clean

    def archive(self, whiteboard: Whiteboard) -> Whiteboard:
        """Archive a whiteboard, making it read-only (no new operations).

        Idempotent: archiving an already-archived board is a no-op that returns
        the current row unchanged (no double timestamp bump).
        """
        with transaction.atomic():
            locked = Whiteboard.objects.select_for_update().get(pk=whiteboard.pk)
            if locked.status != WhiteboardStatus.ARCHIVED:
                locked.status = WhiteboardStatus.ARCHIVED
                locked.archived_at = timezone.now()
                locked.save(update_fields=["status", "archived_at", "updated_at"])
        return locked

    def unarchive(self, whiteboard: Whiteboard) -> Whiteboard:
        """Restore an archived whiteboard to active (writable) status.

        Idempotent: unarchiving an already-active board is a no-op.
        """
        with transaction.atomic():
            locked = Whiteboard.objects.select_for_update().get(pk=whiteboard.pk)
            if locked.status != WhiteboardStatus.ACTIVE:
                locked.status = WhiteboardStatus.ACTIVE
                locked.archived_at = None
                locked.save(update_fields=["status", "archived_at", "updated_at"])
        return locked


def normalize_batch(ref: Any) -> list[dict[str, Any]]:
    """Extract the list of operation dicts from a batch request.

    ``ref`` is the JSON-decoded request body. Accepted shapes:

    * a bare operation object (single submission): ``{...}``
    * an object wrapping a list: ``{"operations": [...]}``
    * a list of operations: ``[...]``

    Returns an empty list for an invalid shape so the caller can reject it
    uniformly.
    """
    if isinstance(ref, dict):
        # A bare operation object is a single-op batch (has a top-level
        # operation_id / operation_type); a wrapper has an "operations" list.
        wrapped = ref.get("operations")
        if isinstance(wrapped, list) and all(isinstance(x, dict) for x in wrapped):
            return list(wrapped)
        if "operation_id" in ref or "operation_type" in ref:
            return [ref]
        return []
    if isinstance(ref, list) and all(isinstance(x, dict) for x in ref):
        return list(ref)
    return []
