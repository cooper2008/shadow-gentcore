"""Tests for G-TDI — PackIndex boot-time cache."""

from __future__ import annotations

from pathlib import Path

import yaml

from harness.core.pack_index import PackIndex, build_default_index


def _write_pack(base: Path, rel: str, data: dict) -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


# ── Scan — basic shape ────────────────────────────────────────────────────


class TestScan:
    def test_scans_shipped_pack_layout(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "cloud/aws.yaml", {
            "id": "toolpack://cloud/aws",
            "description": "AWS CLI",
            "tools": [{"id": "tool://aws_s3"}, {"id": "tool://aws_ecs"}],
        })
        _write_pack(tmp_path, "cloud/kubectl.yaml", {
            "id": "toolpack://cloud/kubectl",
            "tools": [{"id": "tool://kubectl_get"}],
        })
        index = PackIndex()
        count = index.scan([tmp_path])
        assert count == 2
        assert index.pack_count() == 2
        assert index.tool_count() == 3

    def test_ignores_non_pack_yamls(self, tmp_path: Path) -> None:
        # Not a pack — no id, or id doesn't start with toolpack://
        _write_pack(tmp_path, "random.yaml", {"id": "agent://x"})
        _write_pack(tmp_path, "noid.yaml", {"description": "x"})
        _write_pack(tmp_path, "pack.yaml", {
            "id": "toolpack://core/filesystem",
            "tools": [{"id": "tool://file_read"}],
        })
        index = PackIndex()
        assert index.scan([tmp_path]) == 1

    def test_malformed_yaml_silently_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "broken.yaml").write_text(": :\n:: bad", encoding="utf-8")
        _write_pack(tmp_path, "good.yaml", {
            "id": "toolpack://core/shell",
            "tools": [],
        })
        index = PackIndex()
        assert index.scan([tmp_path]) == 1

    def test_rescan_replaces_index(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "a.yaml", {"id": "toolpack://a/one"})
        index = PackIndex()
        index.scan([tmp_path])
        assert index.pack_count() == 1
        # Rewrite directory with different packs
        (tmp_path / "a.yaml").unlink()
        _write_pack(tmp_path, "b.yaml", {"id": "toolpack://b/one"})
        _write_pack(tmp_path, "c.yaml", {"id": "toolpack://c/one"})
        index.scan([tmp_path])
        assert index.pack_count() == 2
        assert index.get_pack("toolpack://a/one") is None

    def test_missing_root_returns_zero(self, tmp_path: Path) -> None:
        index = PackIndex()
        missing = tmp_path / "nope"
        assert index.scan([missing]) == 0


# ── Lookups on current-shape packs (no B3 metadata) ───────────────────────


class TestLookupsWithoutB3:
    def test_get_pack_returns_full_metadata(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "aws.yaml", {
            "id": "toolpack://cloud/aws",
            "description": "AWS CLI",
            "tools": [{"id": "tool://aws_s3"}],
        })
        index = PackIndex()
        index.scan([tmp_path])
        data = index.get_pack("toolpack://cloud/aws")
        assert data is not None
        assert data["description"] == "AWS CLI"

    def test_find_tool_resolves_to_pack(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "aws.yaml", {
            "id": "toolpack://cloud/aws",
            "tools": [{"id": "tool://aws_s3"}],
        })
        index = PackIndex()
        index.scan([tmp_path])
        assert index.find_tool("tool://aws_s3") == "toolpack://cloud/aws"
        assert index.find_tool("tool://nonexistent") is None

    def test_capability_lookups_empty_without_b3(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "aws.yaml", {
            "id": "toolpack://cloud/aws",
            "tools": [{"id": "tool://aws_s3"}],
            # no `provides` field yet
        })
        index = PackIndex()
        index.scan([tmp_path])
        assert index.find_by_capability("cloud_query") == []
        assert index.find_by_action_type("cloud_action") == []


# ── Lookups with B3 metadata (forward-compat) ─────────────────────────────


class TestLookupsWithB3:
    def test_capability_index_populated(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "aws.yaml", {
            "id": "toolpack://cloud/aws",
            "provides": ["cloud_query", "cloud_control"],
            "tools": [{"id": "tool://aws_s3"}],
        })
        _write_pack(tmp_path, "gcp.yaml", {
            "id": "toolpack://cloud/gcp",
            "provides": ["cloud_query"],
            "tools": [{"id": "tool://gcp_storage"}],
        })
        index = PackIndex()
        index.scan([tmp_path])
        assert set(index.find_by_capability("cloud_query")) == {
            "toolpack://cloud/aws", "toolpack://cloud/gcp"
        }
        assert index.find_by_capability("cloud_control") == ["toolpack://cloud/aws"]

    def test_action_type_index_populated(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "slack.yaml", {
            "id": "toolpack://services/slack",
            "action_type": "notification",
        })
        _write_pack(tmp_path, "datadog.yaml", {
            "id": "toolpack://observability/datadog",
            "action_type": "observability",
        })
        index = PackIndex()
        index.scan([tmp_path])
        assert index.find_by_action_type("notification") == ["toolpack://services/slack"]

    def test_use_case_index_populated(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "pd.yaml", {
            "id": "toolpack://observability/pagerduty",
            "typical_use_case": ["incident_response", "alerting"],
        })
        index = PackIndex()
        index.scan([tmp_path])
        assert "toolpack://observability/pagerduty" in index.find_by_use_case("incident_response")
        assert "toolpack://observability/pagerduty" in index.find_by_use_case("alerting")

    def test_provides_can_be_scalar_string(self, tmp_path: Path) -> None:
        """Be defensive: yaml authors may write `provides: x` not `provides: [x]`."""
        _write_pack(tmp_path, "p.yaml", {
            "id": "toolpack://x/y",
            "provides": "cap",
        })
        index = PackIndex()
        index.scan([tmp_path])
        assert index.find_by_capability("cap") == ["toolpack://x/y"]


# ── metadata_snapshot() ───────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_shape_consistent_with_or_without_b3(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "bare.yaml", {
            "id": "toolpack://core/filesystem",
            "tools": [{"id": "tool://file_read"}, {"id": "tool://file_write"}],
        })
        _write_pack(tmp_path, "rich.yaml", {
            "id": "toolpack://cloud/aws",
            "description": "AWS CLI",
            "provides": ["cloud_query"],
            "action_type": "cloud_action",
            "typical_use_case": ["incident_response"],
            "requires_env": ["AWS_PROFILE"],
            "cost_hint": "low",
            "tools": [{"id": "tool://aws_s3"}],
        })
        index = PackIndex()
        index.scan([tmp_path])
        snapshot = index.metadata_snapshot()
        assert len(snapshot) == 2

        expected_keys = {
            "id", "description", "tool_count",
            "provides", "action_type", "typical_use_case",
            "requires_env", "cost_hint",
        }
        for entry in snapshot:
            assert set(entry.keys()) == expected_keys

        bare = next(e for e in snapshot if e["id"] == "toolpack://core/filesystem")
        assert bare["provides"] == []
        assert bare["action_type"] is None
        assert bare["tool_count"] == 2

        rich = next(e for e in snapshot if e["id"] == "toolpack://cloud/aws")
        assert rich["provides"] == ["cloud_query"]
        assert rich["action_type"] == "cloud_action"
        assert rich["tool_count"] == 1

    def test_snapshot_sorted_for_determinism(self, tmp_path: Path) -> None:
        _write_pack(tmp_path, "z.yaml", {"id": "toolpack://z/pack"})
        _write_pack(tmp_path, "a.yaml", {"id": "toolpack://a/pack"})
        _write_pack(tmp_path, "m.yaml", {"id": "toolpack://m/pack"})
        index = PackIndex()
        index.scan([tmp_path])
        ids = [e["id"] for e in index.metadata_snapshot()]
        assert ids == sorted(ids)


# ── all_packs() ───────────────────────────────────────────────────────────


class TestAllPacks:
    def test_all_packs_returns_sorted_ids(self, tmp_path: Path) -> None:
        for name in ["z", "a", "m"]:
            _write_pack(tmp_path, f"{name}.yaml", {"id": f"toolpack://{name}/pack"})
        index = PackIndex()
        index.scan([tmp_path])
        assert index.all_packs() == [
            "toolpack://a/pack",
            "toolpack://m/pack",
            "toolpack://z/pack",
        ]


# ── build_default_index() ─────────────────────────────────────────────────


class TestDefaultBuild:
    def test_default_index_returns_packindex(self) -> None:
        """The convenience helper always returns a PackIndex instance,
        even when agent-tools is not installed (empty index)."""
        index = build_default_index()
        assert isinstance(index, PackIndex)

    def test_default_index_picks_up_shipped_packs_when_available(self) -> None:
        """When agent_tools is installed in the editable env, the shipped
        packs should populate the index. Skips gracefully otherwise."""
        import importlib
        try:
            importlib.import_module("agent_tools")
        except ImportError:
            import pytest
            pytest.skip("agent-tools not installed in this env")
        index = build_default_index()
        # At minimum we expect > 10 shipped packs (40+ in the full set)
        assert index.pack_count() > 10
        # cloud/aws should always be discoverable by tool id
        aws_pack = index.find_tool("tool://aws_s3")
        if aws_pack is not None:  # B3 aws pack ships tool://aws_s3
            assert aws_pack == "toolpack://cloud/aws"
