from pathlib import Path


def test_information_wrapper_uses_shared_nightly_mode_and_strips_legacy_override():
    script = Path("scripts/production/information-runtime-run.sh").read_text(encoding="utf-8")

    assert 'source "$ROOT/scripts/production/nightly-ai-mode-env.sh"' in script
    assert 'AI_EXECUTION_MODE="INTERACTIVE_CHAT"' in script
    assert 'AI_EXECUTION_MODE="AUTO_API"' in script
    assert '--execution-mode "$AI_EXECUTION_MODE"' in script
    assert 'ignoring legacy --execution-mode' in script


def test_information_wrapper_keeps_deployment_gate_and_lock():
    script = Path("scripts/production/information-runtime-run.sh").read_text(encoding="utf-8")

    assert "runtime.deployment_gate" in script
    assert "/tmp/arbitrage-information-runtime.lock" in script
