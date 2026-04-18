"""Tests for harness.providers.model_hints — narrow model-id matching."""

from __future__ import annotations

from harness.providers.model_hints import get_model_hint


class TestModelHintMatching:
    def test_empty_model_returns_empty(self) -> None:
        assert get_model_hint("") == ""
        assert get_model_hint(None) == ""

    def test_strong_models_get_no_hint(self) -> None:
        """Claude Opus/Sonnet + GPT-5+ follow schemas well — no nudge."""
        for m in ("claude-opus-4-7", "claude-sonnet-4-6",
                  "gpt-5", "gpt-5.4", "claude-haiku-4-5"):
            assert get_model_hint(m) == "", f"unexpected hint for {m}"

    def test_minimax_m27_gets_minimax_hint(self) -> None:
        hint = get_model_hint("m2.7")
        assert "Alembic" in hint
        assert "sa.Column" in hint

    def test_minimax_prefix(self) -> None:
        assert "JSON" in get_model_hint("minimax-abc")

    def test_gemini_flash_gets_flash_hint(self) -> None:
        hint = get_model_hint("gemini-3-flash-preview")
        assert "ONLY" in hint
        assert "markdown" in hint

    def test_gemini_pro_thinking_gets_pro_hint(self) -> None:
        hint = get_model_hint("gemini-3.1-pro-preview")
        assert "tool_call" in hint or "single JSON" in hint

    def test_glm_family_gets_glm_hint(self) -> None:
        for m in ("glm-5.1", "glm-5", "glm-4.6", "glm-4.5-flash", "glm-4.5-x"):
            hint = get_model_hint(m)
            assert hint, f"no hint for {m}"
            assert "JSON object" in hint

    def test_case_insensitive(self) -> None:
        assert get_model_hint("GLM-5.1") == get_model_hint("glm-5.1")
        assert get_model_hint("M2.7") == get_model_hint("m2.7")

    def test_unknown_model_returns_empty(self) -> None:
        assert get_model_hint("some-future-model-7b") == ""
