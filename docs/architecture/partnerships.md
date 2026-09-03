# Partnerships & Invitations — Architecture

**Author:** Candle engineering (Senior Django Architect / Security review)
**Date:** 2026-09-03
**Applies to:** the `apps/partnerships` application added in Phase 2.
**Purpose:** Pin down the domain model, service layer, authorization model,
concurrency strategy, event/notification ledger, and email delivery approach for
the partnership and invitation flows. Complements the Phase 0/1 audit
(`docs/architecture/phase-2-audit.md`).

---

## 1. Domain model

The partnership domain lives entirely in `apps/partnerships`. The `User` model
(Phase 1) is deliberately kept free of relationship fields; all relationship
state is expressed here.

- **`Partnership`** — the two-person boundary. Lifecycle:
  `PENDING` → `ACTIVE` → `ENDED` / `CANCELLED`. Holds `public_id` (used in
  routes), `accepted_at`, `ended_at`, `created_at`, `updated_at`.
- **`PartnershipMember`** — a person's membership in a partnership. `role`
  (`OWNER` / `MEMBER`) and `status` (`ACTIVE` / `LEFT`). A `PENDING`
  partnership has exactly one `ACTIVE` `OWNER` member; accepting adds a second
  `ACTIVE` `MEMBER`. Ending marks both memberships `LEFT`.
- **`PartnershipInvitation`** — a single-use, time-limited, token-authenticated
  invitation addressed to one email. Stores only a SHA-256 `token_hash` (never
  the raw token). Statuses: `SENT`, `ACCEPTED`, `REJECTED`, `REVOKED`,
  `EXPIRED`, `INVALID`.
- **`PartnershipEvent`** — an immutable audit ledger of domain events (created,
  resent, accepted, rejected, revoked, expired, activated, ended, cancelled,
  member-left).
- **`Notification`** — a lightweight, reusable in-app notification foundation
  (email delivery is kept separate in `apps/core/emailing.py`).

### 1.1 Invariants (enforced in the app **and** at the database level)

1. One **active** partnership per user — a user holds at most one `ACTIVE`
   membership row.
2. At most **two** active members per partnership.
3. One `SENT` invitation per `(inviter, email)` and per `(partnership, email)`.
4. Invitations are single-use, time-limited, revocable, and token-authenticated.
5. Mutual invitations never create two partnerships (detected at creation time).

See §5 for how these are enforced against concurrent requests.

---

## 2. Layering

The domain is split into four focused modules so the HTTP/HTMX/mobile/WebSocket
surfaces stay thin and business logic is reusable:

| Module | Responsibility |
|---|---|
| `models.py` | Schema, enums from `enums.py`, DB constraints/triggers |
| `selectors.py` | Read/query layer (no side effects) |
| `services.py` | Write/command layer + all domain logic + email/events |
| `policies.py` | Object-level authorization predicates |
| `tokens.py` | Raw-token generation, SHA-256 digest, lookup by digest |
| `forms.py` | Bound-form validation for the template views |
| `views.py` | Translate HTTP/HTMX into service calls |
| `admin.py` | Read-mostly admin, secure by default |

`views.py` never contains domain logic; it only binds HTTP to `services` /
`selectors` / `policies`. This keeps the same calls usable from a future REST
API or Channels consumer without duplicating invariants.

---

## 3. Object-level authorization (`policies.py`)

Centralized predicates used by views (and any future surface) to enforce who may
see and do what. All are explicit; nothing relies on guessing.

- `is_member(user, partnership)`, `is_active_member(user, partnership)`
- `can_view_partnership(user, p)` — active member (or member whose partnership
  is still pending, e.g. the owner viewing their pending state)
- `can_manage_partnership(user, p)` — member allowed to end the partnership
- `can_access_whiteboard(user, p)`
- `can_issue_invitation(user)`, `can_view_invitation(user, inv)`,
  `can_manage_invitation(user, inv)` (inviter-only actions: revoke/resend)
- `is_invitation_for(user, inv)`, `can_accept_invitation(user, inv)`

Privacy: `safe_display_name(user)` in `policies.py` returns a display name that
**never leaks an email address** on public surfaces (see §6 and
`docs/security/invitation-security.md`).

---

## 4. Service layer (`services.py`)

All commands are `@transaction.atomic` and re-validate state inside the
transaction. API:

| Command | Behaviour |
|---|---|
| `create_invitation(inviter, email)` | Validates (self/connected/pending/mutual), creates a `PENDING` partnership with an `ACTIVE OWNER` member, generates a token, emails the accept link, records events. Returns the `SENT` invitation. |
| `resend_invitation(inv, actor)` | Rotates the token, extends expiry, re-emails. Inviter-only. |
| `revoke_invitation(inv, actor)` | Marks `REVOKED`, cancels the pending partnership, ends the owner's active membership. Inviter-only. |
| `accept_invitation(inv, user)` | Locks the invitation + partnership, re-validates, adds the second member, activates the partnership, records events + notifications + emails. Single-winner under contention. |
| `reject_invitation(inv, user)` | Marks `REJECTED`, cancels the pending partnership, ends the owner's active membership. Recipient-only. |
| `end_partnership(partnership, actor)` | Marks `ENDED`, both memberships `LEFT`, records events/notifications/emails. Member-only. |

Exceptions (all carry a safe `user_message` for templates and a precise message
for logs): `PartnershipError` (base), `AlreadyConnectedError`,
`PendingAlreadyExistsError`, `MutualInvitationExistsError`,
`CannotInviteSelfError`, `InvitationUsageError`, `InvitationInvalidError`,
`CapacityError`, `NotAuthorizedError`.

### 4.1 Token handling (`tokens.py`)

- Raw token generated with `secrets.token_urlsafe(32)` (256 bits).
- Only `SHA-256(token)` is stored (`token_hash`).
- `find_invitation_by_token(raw)` looks up by digest, so the raw token never
  needs to be stored or indexed in plaintext.
- Email contains only the raw token inside the accept link (`/invite/<token>/`).

### 4.2 Email delivery

Transactional emails (invitation received / accepted / rejected / partnership
ended) are sent via `apps/core/emailing.send_html_email`, which runs delivery on
a background thread so SMTP round-trips never block the request. Tests use the
synchronous locmem backend for deterministic assertions.

---

## 5. Concurrency strategy

Invitation-accept and mutual-invitation are the two real races. Both are closed
at the application level **and** backed by database constraints so no two
requests can ever produce an invalid state.

### 5.1 Accept race (single-winner)

`accept_invitation`:

1. `_mark_expired_invitation` marks a stale invitation `EXPIRED` in its **own
   committed atomic**, so a subsequent raise cannot roll the marker back.
2. It then re-fetches the invitation with `select_for_update()` and
   re-validates (status `SENT`, already-expired → reject, recipient matches).
3. In a second atomic it `select_for_update()`s the partnership row, re-checks
   the one-active-membership and two-member rules, and inserts the member +
   activates the partnership.
4. A lost race surfaces as either an `IntegrityError` (constraint violation) or,
   when two requests lock overlapping rows, a PostgreSQL **deadlock /
   serialization** `OperationalError` — both are translated to a clean
   `CapacityError` ("you are already connected") so the UI never shows a raw
   deadlock.

Outcome is deterministic: exactly one request wins, all others fail cleanly.

### 5.2 Mutual-invitation race

Two people inviting each other at the same instant could both pass the
"reverse invitation exists?" check and create two partnerships. `create_invitation`
runs inside `transaction.atomic()` and first acquires a **transaction-scoped
PostgreSQL advisory lock** keyed on the sorted pair of the two emails
(`pg_advisory_xact_lock` on two int4 keys from `crc32`). This serializes
creation for the same unordered pair, so the second mutual invite reliably sees
the first and raises `MutualInvitationExistsError`. The lock is released
automatically on commit/rollback.

### 5.3 Database backstops

- `PartnershipMember.Meta` partial unique index `uniq_one_active_membership_per_user`
  on `(user)` where `status='active'` → at most one active membership per user.
- Postgres trigger `enforce_partnership_member_rules()` on
  `partnerships_partnershipmember` (INSERT/UPDATE of `status`) enforces the
  one-active-membership-per-user rule and the at-most-two-active-members-per-
  partnership rule even if the app layer is bypassed. Both are installed by
  migration `0002_parternship_db_constraints` (idempotent).

These make the invariants true no matter how requests interleave.

---

## 6. Privacy on public surfaces

The invitation landing (`invite_landing`) is a rare public (pre-auth) surface.
It is designed to leak as little as possible:

- It shows the inviter's **display name via `safe_display_name`** — if the
  inviter has no display name, it shows a neutral label, **never the raw email**.
- It shows `just-friends` breadcrumbs / state, not the raw accept token.
- Accept requires authentication; the token alone is not sufficient to
  authenticate — the logged-in user must also be the invitee.

See `docs/security/invitation-security.md` for the full threat model.

---

## 7. Rate limiting

Sensitive invitation actions reuse the Phase 1 cache-backed ratelimiter
(`apps/accounts/ratelimit.py`). Limits are configurable via the
`PARTNERSHIP_RATE_LIMITS` setting (create, resend, accept, reject, revoke), e.g.
5 create / hour, 5 resend / hour, 10 accept / 15 min.

---

## 8. UI / templates

- `partnerships/home.html` — the single mobile-first dashboard presenting the
  user's state: empty-state + invite form, pending invitation (resend/revoke),
  received invitations, or connected (partner card + shared-space entry + end
  confirmation via Alpine).
- `invitation_list.html`, `invitation_detail.html`, `invite_landing.html`,
  `invite_invalid.html`.
- Email templates (`templates/emails/invitation_*.html/.txt`,
  `partnership_ended.html/.txt`).
- Template tags in `apps/partnerships/templatetags/partnership_tags.py`
  (`invitation_status_label`, `is_expired`, `remaining_time`) — loaded as
  `{% load partnership_tags %}`.

The "Partnership" entry point is wired into the app shell
(`templates/layouts/shell.html`: sidebar, mobile menu, bottom nav).

---

## 9. Related documents

- `docs/architecture/phase-2-audit.md` — Phase 0/1 audit this app builds on.
- `docs/architecture/shared-resource-access.md` — ownership/lifecycle policy for
  shared resources, including the invitation-aware account-deactivation story.
- `docs/security/invitation-security.md` — token, authorization, and privacy
  threat model.
