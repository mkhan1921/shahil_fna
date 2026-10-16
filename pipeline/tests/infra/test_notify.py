"""Adviser notifications: SMTP, signed webhook, logged fallback (spec §13.1 notify_advisor / send_review_reminder)."""

from __future__ import annotations

import json
import logging
import smtplib

import httpx
import pytest

from advisor_intel.notify import Notifier, ReviewSummary, SmtpConfig, WebhookConfig, compose, sign
from advisor_intel.notify.notifier import NotificationError


def _note(recipient="thandi@practice.co.za"):
    return compose(
        "review_ready",
        call_id="call-1",
        run_id="call-1-run-a",
        recipient=recipient,
        client_label="Thabo Mokoena",
        s=ReviewSummary(needs_you=7, open_flags=2, open_followups=3, partial=True, unfinished_groups=["estate"], banners=["low ASR confidence"]),
    )


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise smtplib.SMTPServerDisconnected("gone")
        FakeSMTP.sent.append((self.calls, msg))


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.fail = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_messages_say_what_needs_the_adviser(monkeypatch):
    monkeypatch.setenv("ADVISOR_INTEL_REVIEW_URL", "https://fna.example.co.za/review/{call_id}")
    n = _note()
    assert n.subject == "Call with Thabo Mokoena is ready for review"
    for part in ("7 item(s) need you", "2 blocking flag(s)", "3 follow-up question(s)", "unfinished: estate", "low ASR confidence", "https://fna.example.co.za/review/call-1"):
        assert part in n.body
    r = compose("review_reminder", call_id="c", run_id="r", recipient=None, client_label="X", s=ReviewSummary(reminder_number=2))
    assert r.subject.startswith("Reminder") and "reminder 2" in r.body
    m = compose("manual_review", call_id="c", run_id="r", recipient=None, client_label="X", s=ReviewSummary())
    assert "manual" in m.subject and "2–4 speakers" in m.body


async def test_logged_fallback_without_channels(caplog):
    with caplog.at_level(logging.WARNING, logger="advisor_intel.notify.notifier"):
        assert await Notifier().send(_note()) == "log"
    assert "ready for review" in caplog.text


async def test_email_over_smtp(fake_smtp):
    n = Notifier(smtp=SmtpConfig(host="smtp.test", port=587, user="bot", password="pw", sender="advisor-intel@practice.co.za"))
    assert await n.send(_note()) == "smtp"
    [(calls, msg)] = fake_smtp.sent
    assert calls == ["starttls", ("login", "bot")]
    assert msg["To"] == "thandi@practice.co.za" and msg["X-Advisor-Intel-Call"] == "call-1" and "7 item(s) need you" in msg.get_content()


async def test_signed_webhook():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["sig"] = request.headers["X-Advisor-Intel-Signature"]
        seen["body"] = request.content
        seen["event"] = request.headers["X-Advisor-Intel-Event"]
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        n = Notifier(webhook=WebhookConfig(url="https://app.test/hooks/advisor", secret="s3cret"), http=http)
        assert await n.send(_note()) == "webhook"
    assert seen["sig"] == sign("s3cret", seen["body"]) and seen["event"] == "review_ready"
    assert json.loads(seen["body"])["data"]["open_flags"] == 2


async def test_one_working_channel_is_enough_but_all_failing_raises(fake_smtp):
    fake_smtp.fail = True
    ok = httpx.MockTransport(lambda r: httpx.Response(200))
    bad = httpx.MockTransport(lambda r: httpx.Response(503))
    async with httpx.AsyncClient(transport=ok) as http:
        n = Notifier(smtp=SmtpConfig(host="smtp.test"), webhook=WebhookConfig(url="https://app.test/h"), http=http)
        assert await n.send(_note()) == "webhook"
    async with httpx.AsyncClient(transport=bad) as http:
        n = Notifier(smtp=SmtpConfig(host="smtp.test"), webhook=WebhookConfig(url="https://app.test/h"), http=http)
        with pytest.raises(NotificationError, match="smtp: .*webhook: "):
            await n.send(_note())


async def test_from_env(monkeypatch):
    monkeypatch.setenv("ADVISOR_INTEL_SMTP_HOST", "smtp.office")
    monkeypatch.setenv("ADVISOR_INTEL_SMTP_PORT", "465")
    monkeypatch.setenv("ADVISOR_INTEL_NOTIFY_WEBHOOK_URL", "https://app/h")
    n = Notifier.from_env()
    assert n.channels == ["smtp", "webhook"] and n.smtp.port == 465 and n.smtp.starttls is False
