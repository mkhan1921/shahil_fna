"""Adviser notifications and review reminders (spec §11, §13.1 ``notify_advisor``, ``send_review_reminder``).

Channels, all optional and configured from the environment:

- **SMTP** (``ADVISOR_INTEL_SMTP_HOST``, ``_PORT``, ``_USER``, ``_PASSWORD``, ``_FROM``, ``_STARTTLS``):
  an email to the adviser when the call record has an address;
- **webhook** (``ADVISOR_INTEL_NOTIFY_WEBHOOK_URL``, optional ``_SECRET``): a JSON POST for in-app
  notification, signed with HMAC-SHA256 in ``X-Advisor-Intel-Signature`` when a secret is set;
- **log**: when no channel is configured (or none applies), the notification is logged, so local
  runs and tests still show it.

A send succeeds when at least one configured channel delivers; it raises ``NotificationError``
(retried by Temporal) only when every configured channel failed. The activity makes sends
idempotent across retries, so the adviser gets one message per event.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Literal

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)

NotificationKind = Literal["review_ready", "review_reminder", "manual_review", "review_complete"]


class Notification(BaseModel):
    kind: NotificationKind
    call_id: str
    run_id: str
    recipient: str | None = None  # the adviser's email address
    subject: str
    body: str
    review_url: str | None = None
    data: dict[str, Any] = {}


class NotificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int = 587
    user: str | None = None
    password: str | None = None
    sender: str = "advisor-intel@localhost"
    starttls: bool = True
    timeout_s: float = 20.0


@dataclass(frozen=True)
class WebhookConfig:
    url: str
    secret: str | None = None
    timeout_s: float = 10.0


def _env(name: str) -> str | None:
    v = os.environ.get(f"ADVISOR_INTEL_{name}")
    return v if v else None


def smtp_from_env() -> SmtpConfig | None:
    host = _env("SMTP_HOST")
    if not host:
        return None
    port = int(_env("SMTP_PORT") or 587)
    starttls = (_env("SMTP_STARTTLS") or ("false" if port == 465 else "true")).lower() in ("1", "true", "yes", "on")
    return SmtpConfig(host=host, port=port, user=_env("SMTP_USER"), password=_env("SMTP_PASSWORD"), sender=_env("SMTP_FROM") or "advisor-intel@localhost", starttls=starttls)


def webhook_from_env() -> WebhookConfig | None:
    url = _env("NOTIFY_WEBHOOK_URL")
    return WebhookConfig(url=url, secret=_env("NOTIFY_WEBHOOK_SECRET")) if url else None


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class Notifier:
    def __init__(self, *, smtp: SmtpConfig | None = None, webhook: WebhookConfig | None = None, http: httpx.AsyncClient | None = None) -> None:
        self.smtp = smtp
        self.webhook = webhook
        self._http = http

    @classmethod
    def from_env(cls) -> "Notifier":
        return cls(smtp=smtp_from_env(), webhook=webhook_from_env())

    @property
    def channels(self) -> list[str]:
        return [name for name, cfg in (("smtp", self.smtp), ("webhook", self.webhook)) if cfg is not None]

    async def send(self, n: Notification) -> str:
        """Deliver ``n``; returns the channels that delivered, e.g. ``"smtp+webhook"`` or ``"log"``."""
        delivered: list[str] = []
        errors: list[str] = []
        attempted = 0
        if self.smtp is not None and n.recipient:
            attempted += 1
            try:
                await asyncio.to_thread(self._send_email, n)
                delivered.append("smtp")
            except (OSError, smtplib.SMTPException) as e:
                errors.append(f"smtp: {e}")
                log.warning("email to %s failed: %s", n.recipient, e)
        if self.webhook is not None:
            attempted += 1
            try:
                await self._post_webhook(n)
                delivered.append("webhook")
            except httpx.HTTPError as e:
                errors.append(f"webhook: {e}")
                log.warning("notification webhook failed: %s", e)
        if attempted and not delivered:
            raise NotificationError("; ".join(errors))
        if not delivered:
            log.warning("[notification %s] to %s: %s\n%s", n.kind, n.recipient or "(no address)", n.subject, n.body)
            return "log"
        return "+".join(delivered)

    def _send_email(self, n: Notification) -> None:
        cfg = self.smtp
        assert cfg is not None and n.recipient
        msg = EmailMessage()
        msg["From"] = cfg.sender
        msg["To"] = n.recipient
        msg["Subject"] = n.subject
        msg["X-Advisor-Intel-Call"] = n.call_id
        msg.set_content(n.body)
        if cfg.port == 465 and not cfg.starttls:
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout_s, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout_s)
        with server:
            if cfg.starttls and not isinstance(server, smtplib.SMTP_SSL):
                server.starttls(context=ssl.create_default_context())
            if cfg.user and cfg.password:
                server.login(cfg.user, cfg.password)
            server.send_message(msg)

    async def _post_webhook(self, n: Notification) -> None:
        cfg = self.webhook
        assert cfg is not None
        body = json.dumps(n.model_dump(mode="json"), separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "X-Advisor-Intel-Event": n.kind}
        if cfg.secret:
            headers["X-Advisor-Intel-Signature"] = sign(cfg.secret, body)
        if self._http is not None:
            resp = await self._http.post(cfg.url, content=body, headers=headers, timeout=cfg.timeout_s)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(cfg.url, content=body, headers=headers, timeout=cfg.timeout_s)
        resp.raise_for_status()
