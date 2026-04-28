def test_claude_fallback_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("CLAUDE_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-must-not-be-used")
    from backend.app.main import _get_claude_definition
    assert _get_claude_definition("galleria") is None


def test_claude_fallback_default_enabled_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("CLAUDE_FALLBACK_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from backend.app.main import _get_claude_definition
    # Default-on, no key => None (because no API key, not because disabled)
    assert _get_claude_definition("galleria") is None


def test_claude_fallback_accepts_truthy_variants(monkeypatch):
    """The disable check uses the same truthy parsing as BACKFILL_DEFINITIONS."""
    from backend.app.main import _get_claude_definition
    for falsy in ("false", "FALSE", "0", "no", "No"):
        monkeypatch.setenv("CLAUDE_FALLBACK_ENABLED", falsy)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        assert _get_claude_definition("x") is None, f"{falsy!r} should disable"
