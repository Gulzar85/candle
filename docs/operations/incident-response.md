# Incident Response

**Status: documented process.** No incident has occurred against a
production deployment (none exists yet) — this establishes the process to
follow, so the first real incident isn't the first time anyone thinks about
it.

## Severity levels

| Level | Definition | Example |
|---|---|---|
| SEV1 | Total outage, or any data-integrity/security breach. | Database unreachable; unauthorized cross-partnership whiteboard access confirmed. |
| SEV2 | Significant degradation, workaround exists or impact is partial. | Real-time collaboration down (WebSocket/Redis), but HTTP draw-and-sync still works; login rate limiting misfiring and blocking legitimate users. |
| SEV3 | Minor issue, low user impact. | A single non-critical background task failing; a cosmetic bug. |

## Process

1. **Detect** — via monitoring alert (`docs/operations/monitoring.md`) or a
   user report. Confirm it's real before escalating (check
   `/health/ready/`, check recent deploys).
2. **Triage severity** — using the table above. This determines urgency, not
   the emotional weight of the report.
3. **Mitigate first, root-cause second** — if a rollback (`docs/operations/
   production-runbook.md`) resolves user impact, do that before spending
   time understanding *why* the bad release was bad. Understanding can
   happen with impact already contained.
4. **Communicate** — for SEV1/SEV2 affecting users, a brief status update
   (even "we're aware and investigating" beats silence) as soon as
   mitigation is underway, and a resolution note once fixed.
5. **Resolve** — confirm via the same signal that detected the incident
   (the alert clears, the health check passes, the user report is
   followed up on).
6. **Postmortem** (SEV1/SEV2 only, blameless) — what happened, what the
   actual user impact was, what the fix was, and one or two concrete
   follow-up actions (a test that would have caught it, a monitoring gap to
   close, a runbook step to add). Skip this ceremony for SEV3 — it's not
   worth the overhead for low-impact issues.

## Security-specific incidents

Given this application's core guarantee is that a user can only access their
own shared resources (`docs/security/security-checklist.md`), any confirmed
authorization bypass — a user accessing another partnership's whiteboard,
data, or operations — is automatically **SEV1**, regardless of how it was
discovered (internal testing, external report, or active exploitation).

1. Contain first: if the bypass is a specific, identifiable code path,
   consider whether it can be blocked at the reverse-proxy level
   (deny the specific route) faster than shipping a code fix, buying time
   for a correct fix rather than a rushed one.
2. Determine scope: which users/data were actually exposed, not just which
   theoretically could have been — check application logs for actual
   requests matching the vulnerable pattern.
3. Fix the root cause with a real code change and a regression test (see
   `docs/testing/production-test-plan.md`'s IDOR test coverage as the
   pattern to extend).
4. Notify affected users if their private data was actually accessed by
   someone unauthorized (not merely "theoretically accessible") — the exact
   legal/notification obligations depend on jurisdiction and are outside
   this document's scope to determine unilaterally.

## Deferred to actual deployment

This process has not been exercised against a real incident. An on-call
rotation, a paging tool (PagerDuty/Opsgenie/equivalent), and a status-page
mechanism for user communication are all deployment-time decisions not yet
made — this document defines *the process*, not the specific tooling to run
it with.
