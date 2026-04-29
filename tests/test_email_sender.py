"""Tests for backend.app.email_sender — smtplib is mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_is_configured_false_when_no_password(monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    from backend.app import email_sender
    assert email_sender.is_configured() is False


def test_send_email_skips_when_unconfigured(monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    from backend.app import email_sender
    with patch("backend.app.email_sender.smtplib.SMTP") as smtp_cls:
        ok = email_sender.send_email("a@example.com", "subj", "body")
    assert ok is False
    smtp_cls.assert_not_called()


def test_send_email_calls_smtp_with_starttls(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test-app-password")
    monkeypatch.setenv("GMAIL_FROM_ADDRESS", "test@gmail.com")
    from backend.app import email_sender

    instance = MagicMock()
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)

    with patch("backend.app.email_sender.smtplib.SMTP", return_value=instance) as smtp_cls:
        ok = email_sender.send_email("user@example.com", "Hello", "Body text")

    assert ok is True
    smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
    instance.starttls.assert_called_once()
    instance.login.assert_called_once_with("test@gmail.com", "test-app-password")
    sent_msg = instance.send_message.call_args[0][0]
    assert sent_msg["From"] == "test@gmail.com"
    assert sent_msg["To"] == "user@example.com"
    assert sent_msg["Subject"] == "Hello"
    assert "Body text" in sent_msg.get_content()


def test_send_email_returns_false_on_smtp_exception(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test-app-password")
    monkeypatch.setenv("GMAIL_FROM_ADDRESS", "test@gmail.com")
    from backend.app import email_sender

    with patch("backend.app.email_sender.smtplib.SMTP", side_effect=OSError("network")):
        ok = email_sender.send_email("user@example.com", "s", "b")
    assert ok is False


def test_welcome_body_includes_token(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "p")
    monkeypatch.setenv("GMAIL_FROM_ADDRESS", "test@gmail.com")
    from backend.app import email_sender

    instance = MagicMock()
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    with patch("backend.app.email_sender.smtplib.SMTP", return_value=instance):
        email_sender.send_welcome_token("u@example.com", "abc123def")

    sent_msg = instance.send_message.call_args[0][0]
    body = sent_msg.get_content()
    assert "abc123def" in body
    assert sent_msg["Subject"] == email_sender.WELCOME_SUBJECT


def test_forgot_body_includes_token(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "p")
    monkeypatch.setenv("GMAIL_FROM_ADDRESS", "test@gmail.com")
    from backend.app import email_sender

    instance = MagicMock()
    instance.__enter__ = MagicMock(return_value=instance)
    instance.__exit__ = MagicMock(return_value=False)
    with patch("backend.app.email_sender.smtplib.SMTP", return_value=instance):
        email_sender.send_forgot_token("u@example.com", "newtok")

    sent_msg = instance.send_message.call_args[0][0]
    assert "newtok" in sent_msg.get_content()
    assert sent_msg["Subject"] == email_sender.FORGOT_SUBJECT
