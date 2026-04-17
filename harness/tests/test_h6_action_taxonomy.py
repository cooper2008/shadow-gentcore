"""Tests for H6 — pack-driven tool → action map (fix/phase2b-permission-engine).

Before H6: `RuleEngine._tool_to_action` hardcoded 8 tool names; anything else
defaulted to `shell_command` (the most restrictive action). Once H3 wires
set_rule_context into the live path, that default would silently block every
MCP tool, every AWS CLI tool, every domain-specific tool.

After H6: a `register_tool_actions({tool_name: action_type})` method populates
an instance-level map, read BEFORE the hardcoded fallback. At bootstrap, a
helper walks agent-tools pack YAMLs and builds the map from the `action_type:`
field introduced by B3.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from harness.core.rule_engine import RuleEngine


class TestRegisterToolActions:
    def test_register_populates_map(self) -> None:
        engine = RuleEngine()
        engine.register_tool_actions({
            "aws_s3": "cloud_action",
            "dd_query_metrics": "network_call",
        })
        assert engine._tool_to_action("aws_s3") == "cloud_action"
        assert engine._tool_to_action("dd_query_metrics") == "network_call"

    def test_register_merges_across_calls(self) -> None:
        engine = RuleEngine()
        engine.register_tool_actions({"aws_s3": "cloud_action"})
        engine.register_tool_actions({"slack_send_message": "notification"})
        assert engine._tool_to_action("aws_s3") == "cloud_action"
        assert engine._tool_to_action("slack_send_message") == "notification"

    def test_register_overwrites_previous_mapping(self) -> None:
        engine = RuleEngine()
        engine.register_tool_actions({"web_search": "network_call"})
        engine.register_tool_actions({"web_search": "knowledge_retrieval"})
        assert engine._tool_to_action("web_search") == "knowledge_retrieval"

    def test_register_is_idempotent_for_empty_dict(self) -> None:
        engine = RuleEngine()
        engine.register_tool_actions({})
        # hardcoded fallback still works
        assert engine._tool_to_action("file_read") == "file_read"


class TestFallbackOrder:
    def test_registered_beats_hardcoded(self) -> None:
        """Pack-declared action_type overrides the hardcoded dict."""
        engine = RuleEngine()
        # file_read is in the hardcoded dict as "file_read"
        engine.register_tool_actions({"file_read": "knowledge_retrieval"})
        assert engine._tool_to_action("file_read") == "knowledge_retrieval"

    def test_hardcoded_used_when_unregistered(self) -> None:
        engine = RuleEngine()
        assert engine._tool_to_action("shell_exec") == "shell_command"
        assert engine._tool_to_action("file_write") == "file_write"

    def test_final_fallback_is_still_shell_command(self) -> None:
        engine = RuleEngine()
        # A completely unknown tool with no registration still falls back to
        # shell_command (most restrictive) — preserves backward compatibility.
        assert engine._tool_to_action("mysterious_new_tool") == "shell_command"


class TestLoadToolActionsFromPacks:
    def test_loads_action_types_from_pack_directory(self, tmp_path: Path) -> None:
        from harness.core.rule_engine import load_tool_actions_from_packs

        # Two packs with action_type + a mix of inline-dict tools and URI-string tools
        (tmp_path / "cloud").mkdir()
        (tmp_path / "cloud" / "aws.yaml").write_text(dedent("""\
            id: "toolpack://cloud/aws"
            action_type: cloud_action
            tools:
              - id: "tool://aws_s3"
              - id: "tool://aws_ecs"
        """))
        (tmp_path / "services").mkdir()
        (tmp_path / "services" / "slack.yaml").write_text(dedent("""\
            id: "toolpack://services/slack"
            action_type: notification
            tools:
              - id: "tool://slack_send_message"
                adapter_class: http_api
              - id: "tool://slack_create_channel"
                adapter_class: http_api
        """))

        mapping = load_tool_actions_from_packs(tmp_path)
        assert mapping["aws_s3"] == "cloud_action"
        assert mapping["aws_ecs"] == "cloud_action"
        assert mapping["slack_send_message"] == "notification"
        assert mapping["slack_create_channel"] == "notification"

    def test_skips_packs_without_action_type(self, tmp_path: Path) -> None:
        from harness.core.rule_engine import load_tool_actions_from_packs

        (tmp_path / "untagged.yaml").write_text(dedent("""\
            id: "toolpack://legacy"
            tools:
              - id: "tool://some_tool"
        """))
        mapping = load_tool_actions_from_packs(tmp_path)
        assert "some_tool" not in mapping  # untagged packs don't pollute the map

    def test_empty_directory_returns_empty_mapping(self, tmp_path: Path) -> None:
        from harness.core.rule_engine import load_tool_actions_from_packs

        assert load_tool_actions_from_packs(tmp_path) == {}

    def test_nonexistent_directory_returns_empty_mapping(self, tmp_path: Path) -> None:
        from harness.core.rule_engine import load_tool_actions_from_packs

        assert load_tool_actions_from_packs(tmp_path / "does_not_exist") == {}

    def test_handles_malformed_yaml_gracefully(self, tmp_path: Path) -> None:
        from harness.core.rule_engine import load_tool_actions_from_packs

        (tmp_path / "good.yaml").write_text(dedent("""\
            id: "toolpack://good"
            action_type: file_read
            tools:
              - id: "tool://good_tool"
        """))
        (tmp_path / "bad.yaml").write_text("not: [valid yaml\n")
        mapping = load_tool_actions_from_packs(tmp_path)
        # Good pack still contributes; bad pack is skipped
        assert mapping.get("good_tool") == "file_read"


class TestShippedPacksPopulateMap:
    """Integration: the real agent-tools packs (tagged in B3) should contribute
    a rich tool → action map without any manual wiring.

    These tests depend on the sibling agent-tools repo being checked out with
    the B3 pack-metadata commits present. If the checkout is on a pre-B3 branch,
    the tests skip rather than fail — H6 itself is validated by the hermetic
    tests above.
    """

    def _pack_dir(self) -> Path | None:
        candidate = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "agent-tools" / "src" / "agent_tools" / "packs"
        )
        return candidate if candidate.is_dir() else None

    def _skip_unless_tool_tagged(self, tool_name: str):  # type: ignore[no-untyped-def]
        from harness.core.rule_engine import load_tool_actions_from_packs

        pack_dir = self._pack_dir()
        if pack_dir is None:
            pytest.skip("agent-tools packs not available in this workspace")
        mapping = load_tool_actions_from_packs(pack_dir)
        if tool_name not in mapping:
            pytest.skip(
                f"tool {tool_name!r} not yet tagged in agent-tools packs on this "
                f"checkout (merge B3 into agent-tools master to enable)"
            )
        return mapping

    def test_aws_s3_is_mapped_to_cloud_action(self) -> None:
        mapping = self._skip_unless_tool_tagged("aws_s3")
        assert mapping["aws_s3"] == "cloud_action"

    def test_slack_send_message_is_mapped_to_notification(self) -> None:
        mapping = self._skip_unless_tool_tagged("slack_send_message")
        assert mapping["slack_send_message"] == "notification"
