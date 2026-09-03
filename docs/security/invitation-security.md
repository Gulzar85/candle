# Invitation Security — Threat Model & Controls

**Author:** Candle engineering (Senior Django Architect / Security review)
**Date:** 2026-09-03
**Applies to:** the invitation/token flow in `apps/partnerships`.
**Purpose:** Document the threat model for token-authenticated invitations and
the controls that mitigate each threat. Complements
`docs/architecture/partnerships.md` (domain) and
`docs/architecture/shared-resource-access.md` (lifecycle).

---

## 1. Assets to protect

- The **raw invitation token** (the capability that lets someone accept a
  partnership for a given email).
- Messages between an inviter and invitee (partnership state).
- A user's identity / email address (privacy).

## 2. Token design

- Generated with `secrets.token_urlsafe(32)` → **256 bits of entropy**;
  unguessable.
- Stored **only as `SHA-256(token).hexdigest()`** (`token_hash`); the raw value
  is never persisted or logged.
- Lookup is by digest (`find_invitation_by_token`), so a DB leak does not reveal
  usable tokens.
- **Single-use**: accepted/revoked/rejected/expired invitations cannot be used
  again (the accept path re-validates `status == SENT` under a row lock).
- **Time-limited**: `expires_at` set at creation/resend; a stale invitation is
  deterministically marked `EXPIRED`.
- **Revocable**: the inviter can revoke at any time while `SENT`.

## 3. Threat model & controls

| # | Threat | Control |
|---|---|---|
| T1 | Token guessed / brute-forced | 256-bit random token; rate-limited accept (`PARTNERSHIP_RATE_LIMITS.invite_accept`) |
| T2 | Token leaked from DB | Only SHA-256 digest stored; raw token never logged |
| T3 | Token leaked in transit | App is HTTPS-only in production (HSTS, secure cookies); token is in the email body, not a referrer |
| T4 | Email forwarded / token reused by attacker | Single-use + time-limit; accept is **not** token-only — the logged-in `request.user` must also be the invitee (`is_invitation_for`) |
| T5 | Token in browser history / server logs / referer | Accept link is `GET` and renders a no-action landing; the actual accept is a `POST` with the recipient logged in; `Referrer-Policy` / CSP `base-uri 'self'` |
| T6 | IDOR / enumerate invitation by ID | Invitations are addressed by unpredictable `public_id` (UUID) and by token; views authorize via `can_view_invitation` / `is_invitation_for` |
| T7 | Enumeration of which emails have accounts | Invitation exists regardless of whether the email is already registered; landing reveals minimal state |
| T8 | Privacy: that email is used / shown publicly | Landing shows `safe_display_name` only — a neutral label (never the raw email) when no display name is set |
| T9 | Invite spam / abuse | Rate-limited create/resend/revoke; one `SENT` per `(inviter, email)`; pending-partnership cap per user |
| T10 | Concurrent double-accept | `select_for_update` + DB constraints → exactly one winner; losers get a clean `CapacityError` |
| T11 | Mutual invites create two partnerships | Postgres advisory lock on the unordered pair + creation-time mutual check |
| T12 | Revoke/resend of someone else's invite | Inviter-only (`can_manage_invitation`) enforced in `policies` + `services` |
| T13 | Accept a partnership invite while already connected | `_has_any_active_membership` gate + partial unique index + trigger |

## 4. Authorization summary

- The raw token is a **means to locate** an invitation record, not (by itself) a
  full credential. Completing an accept requires:
  1. A valid, `SENT`, unexpired invitation (located by token).
  2. The authenticated `request.user` matching the invitee
     (`is_invitation_for` — checks `invitee_id` and normalized
     `invitee_email`).
- Object-level predicates in `policies.py` are the single source of truth; views
  and services both consult them.

## 5. Privacy controls

- `policies.safe_display_name(user)` guarantees public surfaces never show a raw
  partner/inviter email.
- The invitation landing gives the must-know message ("You're invited",
  "Expired", "No longer available") without leaking who else might have been
  invited or account-existence details.
- Email-to-account linking is not required to *receive* an invite; acceptance
  requires logging into the matching account.

## 6. Rate limits (configurable)

`PARTNERSHIP_RATE_LIMITS` (defaults):

| Action | Limit |
|---|---|
| `invite` (create/resend) | 5 / hour |
| `invite_resend` | 5 / hour |
| `invite_accept` | 10 / 15 min |
| `invite_reject` | 10 / 15 min |
| `invite_revoke` | 5 / hour |

Limits are keyed by client IP + user id, backed by the Redis cache, and reuse
the Phase 1 `apps/accounts/ratelimit` module.

## 7. Testing coverage

See `tests/partnerships/`:

- `test_models.py` — invariants, token hashing, constraints.
- `test_invitations.py` — create/resend/revoke/accept/reject lifecycle,
  single-use, expiry, wrong-user rejection.
- `test_authorization.py` — IDOR, landing privacy, invalid/expired tokens.
- `test_concurrency.py` — single-winner accept and mutual-invite race
  (uses `TransactionTestCase` + per-thread connections + real row locks).

## 8. Related documents

- `docs/architecture/partnerships.md` — domain, services, concurrency.
- `docs/architecture/shared-resource-access.md` — ownership/lifecycle.
- `docs/architecture/phase-2-audit.md` — the Phase 0/1 audit.
