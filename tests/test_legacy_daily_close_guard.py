from pathlib import Path


def test_legacy_daily_close_is_disabled_by_default():
    script = Path("scripts/production/daily-close-runtime-run.sh").read_text(encoding="utf-8")

    assert "LEGACY / MANUAL-ONLY WRAPPER" in script
    assert 'ALLOW_LEGACY_DAILY_CLOSE:-0' in script
    assert "pretrade-unified-runtime-run.sh" in script


def test_legacy_daily_close_uses_shared_ai_mode_when_explicitly_enabled():
    script = Path("scripts/production/daily-close-runtime-run.sh").read_text(encoding="utf-8")

    assert 'source "$ROOT/scripts/production/nightly-ai-mode-env.sh"' in script
    assert 'AI_EXECUTION_MODE="INTERACTIVE_CHAT"' in script
    assert 'AI_EXECUTION_MODE="AUTO_API"' in script
