def test_record_call_increments_monthly_total(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MONTHLY_USD_CAP", "1.00")
    from backend.app import spend
    spend.reset_for_test()

    spend.record_claude_call(input_tokens=1_000_000, output_tokens=0)
    assert spend.is_over_cap() is True


def test_under_cap_allows_calls(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MONTHLY_USD_CAP", "10.00")
    from backend.app import spend
    spend.reset_for_test()

    spend.record_claude_call(input_tokens=1000, output_tokens=500)
    assert spend.is_over_cap() is False
    assert spend.month_total_usd() < 0.01


def test_no_cap_set_means_unlimited(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MONTHLY_USD_CAP", raising=False)
    from backend.app import spend
    spend.reset_for_test()
    spend.record_claude_call(input_tokens=10**9, output_tokens=10**9)
    assert spend.is_over_cap() is False
