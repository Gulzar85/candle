"""Concurrency tests for whiteboard operations.

Verifies that concurrent operation submissions are handled correctly:
- No duplicate sequences
- No duplicate versions
- No lost operations
- No inconsistent whiteboard state
- Atomic transaction behavior
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from apps.whiteboard import selectors
from apps.whiteboard.enums import WhiteboardOperationType, WhiteboardStatus
from apps.whiteboard.errors import StaleVersionError, WhiteboardReadOnlyError
from apps.whiteboard.models import WhiteboardOperation
from apps.whiteboard.restore import RestoreService
from apps.whiteboard.service import WhiteboardOperationService
from apps.whiteboard.state_reconstruction import reconstruct_state
from apps.whiteboard.validator import OperationValidator

if TYPE_CHECKING:
    from apps.accounts.models import User

from .base import ConcurrencyTestCase, make_active_partnership, make_user


def _make_stroke_op(*, base_version: int = 0, object_id: str | None = None) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.CREATE_STROKE,
        "base_version": base_version,
        "payload": {
            "object_id": object_id or str(uuid.uuid4()),
            "points": [{"x": 10, "y": 20}, {"x": 11, "y": 21}],
            "color": "#2563eb",
            "width": 3,
            "opacity": 1.0,
        },
    }


class ConcurrencyTests(ConcurrencyTestCase):
    """Test concurrent operation submissions against the same whiteboard.

    Uses threads to simulate two users submitting operations at
    approximately the same time. The database-level locking
    (select_for_update) ensures deterministic ordering.
    """

    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = selectors.whiteboard_for_partnership(self.partnership)

    def test_concurrent_operations_receive_distinct_sequences(self) -> None:
        """Two concurrent operations must get different sequences."""
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)
        results: list[dict] = []
        errors: list[Exception] = []

        def submit_op(user: User, base_version: int) -> None:
            try:
                op = _make_stroke_op(base_version=base_version)
                result = svc.submit(self.whiteboard, user, op)
                results.append(
                    {"user": user.email, "version": result.version, "applied": result.applied}
                )
            except Exception as e:
                errors.append(e)

        # Both submit at version 0. One will get seq 1, the other seq 2.
        # The second will get a StaleVersionError because after the first
        # commits, the version is 1, not 0.
        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(submit_op, self.alice, 0)
            f2 = executor.submit(submit_op, self.bobby, 0)
            f1.result()
            f2.result()

        # Exactly one should succeed, one should get StaleVersionError.
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["applied"], 1)

    def test_sequential_concurrent_submissions(self) -> None:
        """Two users submitting sequentially (alice then bobby) should work."""
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)

        op1 = _make_stroke_op(base_version=0)
        result1 = svc.submit(self.whiteboard, self.alice, op1)
        self.assertEqual(result1.version, 1)

        op2 = _make_stroke_op(base_version=1)
        result2 = svc.submit(self.whiteboard, self.bobby, op2)
        self.assertEqual(result2.version, 2)

    def test_no_duplicate_sequences_after_concurrent_attempts(self) -> None:
        """Even with concurrent failures, sequences must never duplicate."""
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)
        succeeded = []

        def try_submit(base_version: int) -> None:
            op = _make_stroke_op(base_version=base_version)
            try:
                result = svc.submit(self.whiteboard, self.alice, op)
                succeeded.append(result.version)
            except Exception:
                pass

        # Launch 5 concurrent attempts, all at version 0.
        # Only one should succeed.
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(try_submit, 0) for _ in range(5)]
            for f in as_completed(futures):
                f.result()

        self.assertEqual(len(succeeded), 1)
        self.assertEqual(succeeded[0], 1)

    def test_whiteboard_version_never_decreases(self) -> None:
        """The whiteboard.version must always increase or stay the same."""
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)
        versions_seen = []

        for _ in range(10):
            self.whiteboard.refresh_from_db()
            versions_seen.append(self.whiteboard.version)
            op = _make_stroke_op(base_version=self.whiteboard.version)
            try:
                svc.submit(self.whiteboard, self.alice, op)
            except Exception:
                pass

        # Versions should be monotonically non-decreasing.
        for i in range(1, len(versions_seen)):
            self.assertGreaterEqual(versions_seen[i], versions_seen[i - 1])

    def test_all_operations_persisted_after_sequential_batch(self) -> None:
        """10 sequential operations should all be persisted."""
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)
        expected_version = 0

        for _i in range(10):
            op = _make_stroke_op(base_version=expected_version)
            result = svc.submit(self.whiteboard, self.alice, op)
            expected_version = result.version

        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.version, 10)
        self.assertEqual(
            WhiteboardOperation.objects.filter(whiteboard=self.whiteboard).count(),
            10,
        )

    def test_replay_is_deterministic_after_sequential_ops(self) -> None:
        """Replay of 10 sequential operations must produce correct state."""
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)
        object_ids = []

        for i in range(10):
            oid = f"stroke-{i}"
            object_ids.append(oid)
            op = _make_stroke_op(base_version=i, object_id=oid)
            svc.submit(self.whiteboard, self.alice, op)

        ops = WhiteboardOperation.objects.filter(whiteboard=self.whiteboard).order_by("sequence")
        state = reconstruct_state(ops)

        self.assertEqual(len(state.objects), 10)
        for oid in object_ids:
            self.assertIn(oid, state.objects)


class IdempotencyConcurrencyTests(ConcurrencyTestCase):
    """Test that concurrent duplicate submissions are safe."""

    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = selectors.whiteboard_for_partnership(self.partnership)

    def test_concurrent_duplicate_submissions(self) -> None:
        """Two threads submitting the same operation_id concurrently."""
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)

        # First, apply the operation once.
        op = _make_stroke_op(base_version=0)
        result = svc.submit(self.whiteboard, self.alice, op)
        self.assertEqual(result.version, 1)

        # Now both users try to submit the same op concurrently.
        results = []
        errors = []

        def retry() -> None:
            try:
                r = svc.submit(self.whiteboard, self.bobby, op)
                results.append(r)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(retry)
            f2 = executor.submit(retry)
            f1.result()
            f2.result()

        # Both should get duplicate=True acks.
        for r in results:
            self.assertTrue(r.acks[0].duplicate)
            self.assertEqual(r.acks[0].resulting_version, 1)

        # Only one row in the database.
        count = WhiteboardOperation.objects.filter(
            whiteboard=self.whiteboard, operation_id=op["operation_id"]
        ).count()
        self.assertEqual(count, 1)


class TransactionRollbackTests(ConcurrencyTestCase):
    """Test that failed operations are rolled back cleanly."""

    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = selectors.whiteboard_for_partnership(self.partnership)

    def test_failed_batch_rolls_back_entirely(self) -> None:
        """A batch where the second op has a wrong base_version must not
        partially commit."""
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)

        op1 = _make_stroke_op(base_version=0)
        op2 = _make_stroke_op(base_version=999)  # Wrong version

        try:
            svc.submit_batch(self.whiteboard, self.alice, [op1, op2])
        except Exception:
            pass

        self.whiteboard.refresh_from_db()
        # The whiteboard version should still be 0 because the batch failed.
        self.assertEqual(self.whiteboard.version, 0)
        self.assertEqual(
            WhiteboardOperation.objects.filter(whiteboard=self.whiteboard).count(),
            0,
        )


class RestoreConcurrencyTests(ConcurrencyTestCase):
    """Restore submits through the same locked path as any other operation
    (see restore.RestoreService) -- confirm it actually gets the same
    concurrency safety net, not just that the code looks like it should."""

    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = selectors.whiteboard_for_partnership(self.partnership)
        # Seed some history so there's something to restore to.
        svc = WhiteboardOperationService(validator=OperationValidator())
        svc.submit(self.whiteboard, self.alice, _make_stroke_op(base_version=0))
        svc.submit(self.whiteboard, self.bobby, _make_stroke_op(base_version=1))
        self.whiteboard.refresh_from_db()

    def test_restore_races_normal_op_exactly_one_wins(self) -> None:
        """A restore and an ordinary create_stroke, both submitted at the
        board's current version concurrently, must not both succeed."""
        restore_svc = RestoreService()
        op_svc = WhiteboardOperationService(validator=OperationValidator())
        current = self.whiteboard.version
        results: list[str] = []
        errors: list[Exception] = []

        def do_restore() -> None:
            try:
                restore_svc.restore(
                    self.whiteboard,
                    self.alice,
                    operation_id=str(uuid.uuid4()),
                    base_version=current,
                    target_sequence=0,
                )
                results.append("restore")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def do_stroke() -> None:
            try:
                op_svc.submit(self.whiteboard, self.bobby, _make_stroke_op(base_version=current))
                results.append("stroke")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(do_restore)
            f2 = executor.submit(do_stroke)
            f1.result()
            f2.result()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], StaleVersionError)

        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.version, current + 1)
        # No matter which one won, no rows were lost or corrupted.
        self.assertEqual(
            WhiteboardOperation.objects.filter(whiteboard=self.whiteboard).count(),
            current + 1,
        )


class ArchivedBoardConcurrencyTests(ConcurrencyTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = selectors.whiteboard_for_partnership(self.partnership)

    def test_operations_rejected_on_archived_board(self) -> None:
        validator = OperationValidator()
        svc = WhiteboardOperationService(validator=validator)

        self.whiteboard.status = WhiteboardStatus.ARCHIVED
        self.whiteboard.save(update_fields=["status"])

        op = _make_stroke_op(base_version=0)
        with self.assertRaises(WhiteboardReadOnlyError):
            svc.submit(self.whiteboard, self.alice, op)
