"""Getting a batch run's problems in front of a human, not just a log file.

Two kinds of thing are worth interrupting someone for:

- a circuit is stuck retrying a 503/429/network error (informational --
  it'll likely clear on its own, but worth knowing if it doesn't)
- an LLM account needs attention: out of credits, quota exceeded, or the
  key is invalid (urgent -- no amount of retrying fixes this; a human has
  to swap the key or top up billing)

Every channel is opt-in and configured entirely through environment
variables (mirroring how LLM providers are configured -- see
``pyatpg.ranking.providers``), and every send is best-effort: a broken
webhook or unreachable SMTP server should never take down the batch run
that was trying to report a *different* problem. Only stdlib is used
(``urllib``, ``smtplib``) so notifications need no extra dependency.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from typing import Callable, Protocol


class Channel(Protocol):
    def send(self, title: str, message: str, *, urgent: bool) -> None: ...


@dataclass
class ConsoleChannel:
    """Always on: stdout, plus an append-only log file that survives even
    if nobody was watching the terminal when it happened."""

    log_path: str | None = None
    _print: Callable[[str], None] = field(default=print)

    def send(self, title: str, message: str, *, urgent: bool) -> None:
        tag = "!!" if urgent else "--"
        line = f"[{tag} NOTIFY {tag}] {title}: {message}"
        self._print(line)
        if self.log_path:
            try:
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass  # a log write failing must never break the run itself


@dataclass
class DiscordChannel:
    """Posts to an incoming webhook. Create one via a Discord channel's
    Settings -> Integrations -> Webhooks -> New Webhook, then set
    NOTIFY_DISCORD_WEBHOOK to its URL."""

    webhook_url: str
    _urlopen: Callable = field(default=urllib.request.urlopen)

    def send(self, title: str, message: str, *, urgent: bool) -> None:
        prefix = "\U0001F6A8 " if urgent else "ℹ️ "  # 🚨 / ℹ️
        payload = json.dumps({"content": f"{prefix}**{title}**\n{message}"}).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            self._urlopen(request, timeout=10).close()
        except (urllib.error.URLError, OSError):
            pass  # never let a notification failure break the batch run


@dataclass
class EmailChannel:
    """Plain SMTP (with STARTTLS if the server offers it) -- no dependency
    on a mail-sending service. Configure via NOTIFY_EMAIL_* (see below)."""

    smtp_host: str
    smtp_port: int
    username: str
    password: str
    from_addr: str
    to_addrs: tuple[str, ...]
    use_starttls: bool = True
    _smtp_cls: type = field(default=smtplib.SMTP)

    def send(self, title: str, message: str, *, urgent: bool) -> None:
        subject = f"[PyATPG{' - ACTION NEEDED' if urgent else ''}] {title}"
        mime = MIMEText(message)
        mime["Subject"] = subject
        mime["From"] = self.from_addr
        mime["To"] = ", ".join(self.to_addrs)
        try:
            with self._smtp_cls(self.smtp_host, self.smtp_port, timeout=10) as server:
                if self.use_starttls:
                    server.starttls(context=ssl.create_default_context())
                if self.username:
                    server.login(self.username, self.password)
                server.sendmail(self.from_addr, list(self.to_addrs), mime.as_string())
        except (smtplib.SMTPException, OSError):
            pass  # never let a notification failure break the batch run


class Notifier:
    """Fans one notification out to every configured channel."""

    def __init__(self, channels: list[Channel]):
        self.channels = channels

    def notify(self, title: str, message: str, *, urgent: bool = False) -> None:
        for channel in self.channels:
            channel.send(title, message, urgent=urgent)

    @classmethod
    def from_env(cls, *, log_path: str | None = None) -> "Notifier":
        """Build a Notifier from ``NOTIFY_*`` environment variables.

        - Console is always included.
        - ``NOTIFY_DISCORD_WEBHOOK`` -- a Discord incoming-webhook URL.
        - ``NOTIFY_EMAIL_TO`` (comma-separated) plus ``NOTIFY_EMAIL_SMTP_HOST``
          / ``_PORT`` (default 587) / ``_USER`` / ``_PASS`` / ``_FROM``
          (defaults to ``_USER``). Most providers (Gmail, Outlook...) want an
          app password here, not your normal login password.
        """
        channels: list[Channel] = [ConsoleChannel(log_path=log_path)]

        webhook = os.environ.get("NOTIFY_DISCORD_WEBHOOK", "").strip()
        if webhook:
            channels.append(DiscordChannel(webhook_url=webhook))

        to_raw = os.environ.get("NOTIFY_EMAIL_TO", "").strip()
        if to_raw:
            host = os.environ.get("NOTIFY_EMAIL_SMTP_HOST", "").strip()
            if not host:
                raise ValueError(
                    "NOTIFY_EMAIL_TO is set but NOTIFY_EMAIL_SMTP_HOST is not -- "
                    "email notifications need an SMTP server to send through."
                )
            user = os.environ.get("NOTIFY_EMAIL_SMTP_USER", "").strip()
            channels.append(EmailChannel(
                smtp_host=host,
                smtp_port=int(os.environ.get("NOTIFY_EMAIL_SMTP_PORT", "587")),
                username=user,
                password=os.environ.get("NOTIFY_EMAIL_SMTP_PASS", ""),
                from_addr=os.environ.get("NOTIFY_EMAIL_FROM", "").strip() or user,
                to_addrs=tuple(a.strip() for a in to_raw.split(",") if a.strip()),
            ))

        return cls(channels)


def null_notifier() -> Notifier:
    """A Notifier with no channels at all -- for tests, or --no-notify."""
    return Notifier(channels=[])
