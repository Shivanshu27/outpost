# ADR-0011 — Outpost will never submit an application

**Status:** Accepted
**Date:** 2026-09-25
**Deciders:** Shivanshu Singla

## Context

The obvious next feature after "rank jobs for me" is "apply to them for me." It
is the most-requested feature of every tool in this category, it is technically
straightforward for ATS forms with known schemas, and competitors ship it.

It is also the reason the hiring market these tools operate in is degrading.
Mass auto-application floods employers with low-signal volume; employers respond
with more aggressive automated filtering; candidates respond with more
automation. Every participant ends up worse off, and the candidates hurt most
are the ones without the leverage to bypass the filter — which is precisely this
project's user (PRD §4).

There is a narrower argument too. An auto-applied application is sent in the
user's name, over their signature, to a company they may care about, with
content they did not read. When it goes wrong — wrong role, wrong company, a
hallucinated claim in a cover letter — the reputational damage is theirs and it
is not recoverable.

## Decision

**Outpost does not submit applications.** Not behind a flag, not opt-in, not
"assisted." No code path in this project will POST to an employer's form.

The boundary is drawn at *composition*, not generation:

| Permitted | Not permitted |
|---|---|
| Ranking and explaining fit | Submitting a form |
| Drafting a cover letter the user reads and edits | Sending anything |
| Deep-linking to the application page | Filling a form on the user's behalf |
| Tracking what the user applied to, manually | Acting as the user against a third party |

The test: **the user performs the irreversible, outward-facing act.** Outpost
prepares; the human sends.

## Consequences

### Positive

- The project cannot contribute to the dynamic that makes the problem it solves
  worse.
- A clear, statable ethical position is a genuine differentiator in a category
  full of spam tools — and it is a credible one precisely because it is
  architectural rather than a policy that could quietly change.
- Removes an entire class of liability, ToS violation, and CAPTCHA/bot-detection
  arms race.
- Keeps the product honest about what it is: an attention filter, not a volume
  amplifier.

### Negative

- **We will lose users to tools that do it.** Some meaningful fraction of the
  market wants exactly this feature, and they will not be persuaded by an ADR.
- It forecloses a plausible monetisation path.
- Recurring feature requests and PRs will need declining, permanently. This ADR
  exists so that conversation is short and is not re-litigated per maintainer.

## Alternatives considered

**Opt-in auto-apply, off by default.** Rejected. A default is a speed bump, not
a boundary; the harm is the same once enabled, and shipping the capability means
maintaining it. If the code exists, the position is decorative.

**Auto-fill the form but require a human click to submit.** The closest call,
and defensible — the human still performs the irreversible act. Rejected for v1
because it requires driving a browser against third-party forms (fragile,
CAPTCHA-adjacent, ToS-grey), and because "review this pre-filled form" reliably
becomes "click submit without reading." Revisitable, but the bar is high.

**Generate application materials but not send.** *Accepted* — this is the
permitted column above, and the boundary sits exactly here.
