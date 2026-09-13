"""Notification channels and the run_atpg_suite/run_ranking_suite hooks
that decide *when* to fire them. No real network call is ever made here --
DiscordChannel/EmailChannel are exercised with a fake transport."""
import json

import pytest

from pyatpg.automation.manifest import Manifest, ManifestRecord
from pyatpg.automation.notify import ConsoleChannel, DiscordChannel, EmailChannel, Notifier, null_notifier
from pyatpg.automation.suite import _notify_on_transition, _notify_recovery


class TestConsoleChannel:
    def test_prints_and_logs(self, tmp_path):
        log_path = tmp_path / "notifications.log"
        printed = []
        channel = ConsoleChannel(log_path=str(log_path), _print=printed.append)

        channel.send("c17: trouble", "having a bad time", urgent=True)

        assert any("c17: trouble" in line for line in printed)
        logged = log_path.read_text(encoding="utf-8")
        assert "c17: trouble" in logged and "having a bad time" in logged

    def test_missing_log_directory_does_not_raise(self, tmp_path):
        channel = ConsoleChannel(log_path=str(tmp_path / "does" / "not" / "exist" / "log.txt"), _print=lambda s: None)
        channel.send("title", "message", urgent=False)  # must not raise


class TestDiscordChannel:
    def test_posts_expected_json_payload(self):
        captured = {}

        class FakeResponse:
            def close(self):
                pass

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["headers"] = dict(request.header_items())
            return FakeResponse()

        channel = DiscordChannel(webhook_url="https://discord.com/api/webhooks/fake", _urlopen=fake_urlopen)
        channel.send("Key exhausted", "swap it", urgent=True)

        assert captured["url"] == "https://discord.com/api/webhooks/fake"
        assert captured["method"] == "POST"
        assert "Key exhausted" in captured["body"]["content"]
        assert "swap it" in captured["body"]["content"]
        assert captured["headers"]["Content-type"] == "application/json"

    def test_urgent_and_non_urgent_use_different_prefixes(self):
        sent = []

        class FakeResponse:
            def close(self):
                pass

        def fake_urlopen(request, timeout=None):
            sent.append(json.loads(request.data.decode("utf-8"))["content"])
            return FakeResponse()

        channel = DiscordChannel(webhook_url="https://x", _urlopen=fake_urlopen)
        channel.send("t", "m", urgent=True)
        channel.send("t", "m", urgent=False)
        assert sent[0] != sent[1]

    def test_network_failure_is_swallowed_not_raised(self):
        import urllib.error

        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError("no network")

        channel = DiscordChannel(webhook_url="https://x", _urlopen=fake_urlopen)
        channel.send("t", "m", urgent=False)  # must not raise


class TestEmailChannel:
    def test_sends_via_smtp_with_starttls_and_login(self):
        calls = []

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                calls.append(("connect", host, port))

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def starttls(self, context=None):
                calls.append(("starttls",))

            def login(self, user, password):
                calls.append(("login", user, password))

            def sendmail(self, from_addr, to_addrs, message):
                calls.append(("sendmail", from_addr, to_addrs, message))

        channel = EmailChannel(
            smtp_host="smtp.example.com", smtp_port=587, username="me@example.com",
            password="app-password", from_addr="me@example.com", to_addrs=("friend@example.com",),
            _smtp_cls=FakeSMTP,
        )
        channel.send("Out of credits", "switch the key", urgent=True)

        kinds = [c[0] for c in calls]
        assert kinds == ["connect", "starttls", "login", "sendmail"]
        sendmail_call = calls[-1]
        assert sendmail_call[1] == "me@example.com"
        assert sendmail_call[2] == ["friend@example.com"]
        assert "switch the key" in sendmail_call[3]
        assert "ACTION NEEDED" in sendmail_call[3]  # urgent subject marker

    def test_smtp_failure_is_swallowed_not_raised(self):
        import smtplib

        class FailingSMTP:
            def __init__(self, *a, **k):
                raise smtplib.SMTPConnectError(421, "nope")

        channel = EmailChannel(
            smtp_host="smtp.example.com", smtp_port=587, username="", password="",
            from_addr="me@example.com", to_addrs=("friend@example.com",), _smtp_cls=FailingSMTP,
        )
        channel.send("t", "m", urgent=False)  # must not raise


class TestNotifierFromEnv:
    def test_console_only_by_default(self, monkeypatch):
        monkeypatch.delenv("NOTIFY_DISCORD_WEBHOOK", raising=False)
        monkeypatch.delenv("NOTIFY_EMAIL_TO", raising=False)
        notifier = Notifier.from_env()
        assert len(notifier.channels) == 1
        assert isinstance(notifier.channels[0], ConsoleChannel)

    def test_discord_webhook_env_var_adds_the_channel(self, monkeypatch):
        monkeypatch.setenv("NOTIFY_DISCORD_WEBHOOK", "https://discord.com/api/webhooks/x")
        monkeypatch.delenv("NOTIFY_EMAIL_TO", raising=False)
        notifier = Notifier.from_env()
        assert any(isinstance(c, DiscordChannel) for c in notifier.channels)

    def test_email_without_smtp_host_is_a_clear_error(self, monkeypatch):
        monkeypatch.setenv("NOTIFY_EMAIL_TO", "friend@example.com")
        monkeypatch.delenv("NOTIFY_EMAIL_SMTP_HOST", raising=False)
        monkeypatch.delenv("NOTIFY_DISCORD_WEBHOOK", raising=False)
        with pytest.raises(ValueError, match="NOTIFY_EMAIL_SMTP_HOST"):
            Notifier.from_env()

    def test_email_env_vars_build_the_channel(self, monkeypatch):
        monkeypatch.setenv("NOTIFY_EMAIL_TO", "a@example.com, b@example.com")
        monkeypatch.setenv("NOTIFY_EMAIL_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("NOTIFY_EMAIL_SMTP_USER", "me@example.com")
        monkeypatch.delenv("NOTIFY_DISCORD_WEBHOOK", raising=False)
        notifier = Notifier.from_env()
        email_channels = [c for c in notifier.channels if isinstance(c, EmailChannel)]
        assert len(email_channels) == 1
        assert email_channels[0].to_addrs == ("a@example.com", "b@example.com")
        assert email_channels[0].from_addr == "me@example.com"


class TestNullNotifier:
    def test_has_no_channels_and_never_raises(self):
        notifier = null_notifier()
        assert notifier.channels == []
        notifier.notify("title", "message", urgent=True)  # must not raise


class TestNotifyOnTransition:
    """Alert once per distinct failure state, not on every repeat of it."""

    def test_first_failure_notifies(self):
        seen = []
        notifier = Notifier(channels=[_SpyChannel(seen)])
        _notify_on_transition(notifier, previous=None, label="c17", classification="transient", error="503")
        assert len(seen) == 1
        assert seen[0][2] is False  # not urgent

    def test_credentials_failure_is_urgent(self):
        seen = []
        notifier = Notifier(channels=[_SpyChannel(seen)])
        _notify_on_transition(notifier, previous=None, label="c17", classification="credentials", error="quota")
        assert len(seen) == 1
        assert seen[0][2] is True

    def test_repeating_the_same_failure_does_not_renotify(self):
        seen = []
        notifier = Notifier(channels=[_SpyChannel(seen)])
        previous = ManifestRecord(key="c17", status="failed", fingerprint="fp", classification="credentials")
        _notify_on_transition(notifier, previous=previous, label="c17", classification="credentials", error="quota")
        assert seen == []

    def test_changing_failure_kind_notifies_again(self):
        seen = []
        notifier = Notifier(channels=[_SpyChannel(seen)])
        previous = ManifestRecord(key="c17", status="failed", fingerprint="fp", classification="credentials")
        _notify_on_transition(notifier, previous=previous, label="c17", classification="transient", error="503")
        assert len(seen) == 1

    def test_permanent_failures_are_not_notified(self):
        # A bad netlist needs the user to look at the file, which the
        # regular [fail] log line already says -- not worth a ping.
        seen = []
        notifier = Notifier(channels=[_SpyChannel(seen)])
        _notify_on_transition(notifier, previous=None, label="c17", classification="permanent", error="bad netlist")
        assert seen == []


class TestNotifyRecovery:
    def test_notifies_when_previous_was_failed(self):
        seen = []
        notifier = Notifier(channels=[_SpyChannel(seen)])
        previous = ManifestRecord(key="c17", status="failed", fingerprint="fp", classification="transient")
        _notify_recovery(notifier, previous, "c17")
        assert len(seen) == 1

    def test_no_notification_on_a_first_ever_success(self):
        seen = []
        notifier = Notifier(channels=[_SpyChannel(seen)])
        _notify_recovery(notifier, previous=None, label="c17")
        assert seen == []


class _SpyChannel:
    def __init__(self, sink):
        self.sink = sink

    def send(self, title, message, *, urgent):
        self.sink.append((title, message, urgent))
