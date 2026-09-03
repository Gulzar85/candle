# Shared-Resource Access & Lifecycle Policy

**Author:** Candle engineering (Senior Django Architect / Security review)
**Date:** 2026-09-03
**Applies to:** ownership, access, and lifecycle handling of resources shared
between two partners (partnership, membership, events, notifications, and the
future whiteboard data). Builds on the account-lifecycle gap flagged as §5.3 in
`docs/architecture/phase-2-audit.md`.

---

## 1. Ownership model

A partnership is a **two-person boundary**, not a single-owner object:

- `PartnershipMember.role`: `OWNER` (the inviter who starts a pending
  partnership) and `MEMBER` (the person who accepts). Once a partnership is
  `ACTIVE`, both members have **equal** `can_manage_partnership` rights — either
  may end it. The `OWNER`/`MEMBER` distinction has no ongoing privilege after
  activation (both can end; only a member can see the space).
- `PartnershipEvent` is owned by the partnership and is **immutable** — it is an
  audit ledger and is never edited or hard-deleted.
- `Notification` is per-recipient: a user only ever sees/reads their own
  notifications (`mark_read` is idempotent).

## 2. Visibility rules

- A user sees a partnership only if they are (or were) an `ACTIVE` member
  (`is_member`). Non-members never see partnership state, members, or events.
- The invitation landing is the one pre-auth surface and is minimized
  (see `docs/security/invitation-security.md`).
- No globally addressable "list all partnerships" surface exists; pairing is done
  only through the personal dashboard.

## 3. Concurrency & integrity guarantees

- `select_for_update()` serializes accept and end operations (see
  `docs/architecture/partnerships.md` §5).
- Database-level backstops (partial unique index + trigger) make
  "one active partnership per user" and "at most two active members per
  partnership" true under any interleaving.
- Read queries (`selectors.py`) always scope by the requesting user; there is no
  IDOR surface on the home dashboard because the dashboard is inherently scoped
  to `request.user`.

## 4. Account lifecycle policy

The Phase 1 `User` model exposed a gap: no coherent distinction between email
unverified, disabled, deactivated, and deleted (audit §5.3). This section states
Phase 2 policy.

### 4.1 Inactive / deactivated user

- An inactive user (`is_active=False`) **cannot authenticate** (Django's auth
  gate) and holds no session.
- The user's **active partnership(s) are handled safely and statelessly**: the
  app treats an inactive member like an absent partner. Their membership row and
  the partnership are **preserved** (never deleted) so the other partner is not
  silently orphaned.
- We do **not** auto-end a partnership merely because a member is inactive —
  ending is a deliberate, confirmed action. Instead the UI surfaces the partner
  as "unavailable" while data is retained.

### 4.2 Deletion / anonymization (future policy, recorded)

Hard-deleting a user would cascade through `PartnershipMember` and destroy
shared data the other partner depends on. Adopted policy for a future
"delete account" feature:

1. **Deactivate** (non-destructive): preserve all shared data and history.
2. **Anonymize**: strip `Profile` identity and PII from `User` while keeping
   membership/event rows, so the other partner and audit history survive.
3. **Purge**: only after both partners agree / the last active membership is
   cleared; immutables (`PartnershipEvent`) are never purged.

In other words: shared resources are **owned by the partnership**, not by an
individual user, so an individual's account lifecycle must never silently
cascade-delete the partner's data.

## 5. Audit ledger

`PartnershipEvent` provides traceability for every state change (created,
resent, accepted, rejected, revoked, expired, activated, ended, cancelled,
member-left). It records `event_type`, `partner`, optional `actor`, a stable
`data` JSON context, and `created_at`. Because it is append-only, it is safe to
use as both an audit trail and the foundation for future notification/activity
features.

## 6. Related documents

- `docs/architecture/partnerships.md` — domain model, services, authorization,
  concurrency.
- `docs/security/invitation-security.md` — token and authorization threat model.
- `docs/architecture/phase-2-audit.md` — the Phase 0/1 audit this policy
  responds to.
