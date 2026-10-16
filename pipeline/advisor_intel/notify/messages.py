"""What the adviser is told: review ready, reminders, manual handling (spec §5.5, §10.3, §11.1)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .notifier import Notification, NotificationKind


@dataclass
class ReviewSummary:
    """Counts for the message, from the run's profile (all zero when it has none yet)."""

    needs_you: int = 0  # facts not auto-accepted, at or above the collapse threshold
    open_flags: int = 0
    open_followups: int = 0
    partial: bool = False
    unfinished_groups: list[str] = field(default_factory=list)
    banners: list[str] = field(default_factory=list)  # call quality banners (§5.5)
    reminder_number: int = 0


def review_url(call_id: str) -> str | None:
    """The review screen in the FNA app: ``ADVISOR_INTEL_REVIEW_URL`` with ``{call_id}``, else the app origin."""
    template = os.environ.get("ADVISOR_INTEL_REVIEW_URL")
    if template:
        return template.replace("{call_id}", call_id)
    origin = os.environ.get("ADVISOR_INTEL_FNA_APP_ORIGIN")
    return f"{origin.rstrip('/')}/?review={call_id}" if origin else None


def compose(
    kind: NotificationKind, *, call_id: str, run_id: str, recipient: str | None, client_label: str, s: ReviewSummary
) -> Notification:
    url = review_url(call_id)
    lines: list[str] = []
    if kind in ("review_ready", "review_reminder"):
        if kind == "review_ready":
            subject = f"Call with {client_label} is ready for review"
            lines.append(f"The call with {client_label} has been processed and is ready for your review.")
        else:
            subject = f"Reminder: call with {client_label} is waiting for review"
            lines.append(f"The call with {client_label} is still waiting for your review (reminder {s.reminder_number}).")
        lines.append(f"- {s.needs_you} item(s) need you")
        if s.open_flags:
            lines.append(f"- {s.open_flags} blocking flag(s) must be cleared before you can submit")
        if s.open_followups:
            lines.append(f"- {s.open_followups} follow-up question(s) for the client")
        if s.partial:
            groups = ", ".join(s.unfinished_groups) if s.unfinished_groups else "some sections"
            lines.append(f"- The automatic pass ran out of budget; unfinished: {groups}. Those fields are marked in the review.")
        for b in s.banners:
            lines.append(f"- Call quality: {b}")
    elif kind == "manual_review":
        subject = f"Call with {client_label} needs manual handling"
        lines.append(
            f"The recording of the call with {client_label} could not be processed automatically (speaker detection outside 2–4 speakers)."
        )
        lines.append("Please capture this call's details manually.")
        for b in s.banners:
            lines.append(f"- Call quality: {b}")
    else:
        subject = f"Review complete for {client_label}"
        lines.append(f"The reviewed FNA update for {client_label} is ready to import into the FNA app.")
    if url:
        lines.append("")
        lines.append(f"Open: {url}")
    lines.append("")
    lines.append(f"Reference: {call_id} ({run_id})")
    return Notification(
        kind=kind,
        call_id=call_id,
        run_id=run_id,
        recipient=recipient,
        subject=subject,
        body="\n".join(lines),
        review_url=url,
        data={
            "needs_you": s.needs_you,
            "open_flags": s.open_flags,
            "open_followups": s.open_followups,
            "partial": s.partial,
            "reminder_number": s.reminder_number,
        },
    )
