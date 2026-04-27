"""Tests for H5 — debounce RuleEngine._hot_reload (fix/phase2b-permission-engine).

Before H5: `check_tool_call` calls `_hot_reload()` every invocation; `_hot_reload`
does a `stat()` each time. In a ReAct loop that's ~80 stat syscalls per agent
run for zero real config churn.

After H5: `_hot_reload()` skips the stat if <500ms has passed since the last
mtime check. File changes still take effect within ~500ms.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch


from harness.core.rule_engine import RuleEngine


def _write_rules(path: Path, deny_shell: bool) -> None:
    body = (
        "platform:\n"
        "  blocked_commands: []\n"
        "defaults:\n"
        "  file_read: allow\n"
        "  file_write: ask\n"
        "  shell_command: "
        + ("deny" if deny_shell else "ask")
        + "\n"
    )
    path.write_text(body)


class TestHotReloadDebounce:
    def test_consecutive_calls_issue_single_stat(self, tmp_path: Path) -> None:
        rules_path = tmp_path / "rules.yaml"
        _write_rules(rules_path, deny_shell=False)
        engine = RuleEngine(rules_path)

        # Capture stat calls on the rules path
        real_stat = Path.stat
        stat_hits = {"n": 0}

        def counting_stat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self == rules_path:
                stat_hits["n"] += 1
            return real_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", counting_stat):
            # 10 consecutive hot-reload calls in rapid succession
            for _ in range(10):
                engine._hot_reload()

        # Debounce must collapse these into at most ~1 stat (allow 2 for safety
        # across clock-edge corner cases)
        assert stat_hits["n"] <= 2, (
            f"Expected <=2 stat calls across 10 rapid _hot_reload calls, "
            f"got {stat_hits['n']}"
        )

    def test_reload_still_happens_after_debounce_window(self, tmp_path: Path) -> None:
        """File change is still picked up — just not on every single call."""
        rules_path = tmp_path / "rules.yaml"
        _write_rules(rules_path, deny_shell=False)
        engine = RuleEngine(rules_path)

        # Initial state: shell_command = ask
        d1 = engine.check_tool_call("shell_exec", {"command": "echo hi"})
        assert d1.decision.value == "ask"

        # Wait past debounce window and change the file
        time.sleep(0.6)
        _write_rules(rules_path, deny_shell=True)
        # Force mtime bump (in case the edit landed at the same second)
        import os as _os
        _os.utime(rules_path, None)

        # Next call should observe the new rules
        d2 = engine.check_tool_call("shell_exec", {"command": "echo hi"})
        assert d2.decision.value == "deny", (
            f"Expected hot-reload to pick up new rules after debounce window, "
            f"but decision was {d2.decision.value}"
        )

    def test_debounce_does_not_break_file_read_default(self, tmp_path: Path) -> None:
        rules_path = tmp_path / "rules.yaml"
        _write_rules(rules_path, deny_shell=False)
        engine = RuleEngine(rules_path)

        # file_read defaults to allow — must work regardless of debounce
        for _ in range(5):
            d = engine.check_tool_call("file_read", {"path": "x.txt"})
            assert d.decision.value == "allow"


class TestHotReloadDebounceWindowAttribute:
    def test_engine_exposes_debounce_window_constant(self) -> None:
        """Sanity: make the debounce window discoverable/configurable."""
        engine = RuleEngine()
        # Either module-level constant or instance attribute is acceptable
        has_attr = (
            hasattr(engine, "_hot_reload_debounce_ms")
            or hasattr(engine, "HOT_RELOAD_DEBOUNCE_MS")
        )
        assert has_attr, (
            "Expected RuleEngine to expose a debounce window "
            "(_hot_reload_debounce_ms or HOT_RELOAD_DEBOUNCE_MS) for observability"
        )
