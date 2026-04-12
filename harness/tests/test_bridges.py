"""Tests for BrowserBridge and ObservabilityBridge."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from harness.bridges.browser_bridge import BrowserBridge
from harness.bridges.observability_bridge import ObservabilityBridge


class TestBrowserBridge:
    def test_navigate(self) -> None:
        bb = BrowserBridge()
        result = bb.navigate("http://localhost:3000")
        assert result["action"] == "navigate"
        assert result["status"] == "stub"

    def test_screenshot(self) -> None:
        bb = BrowserBridge()
        result = bb.screenshot(selector="#main")
        assert result["action"] == "screenshot"

    def test_get_text(self) -> None:
        bb = BrowserBridge()
        result = bb.get_text("h1")
        assert result["action"] == "get_text"
        assert result["text"] == ""

    def test_click(self) -> None:
        bb = BrowserBridge()
        result = bb.click("button.submit")
        assert result["action"] == "click"

    def test_evaluate(self) -> None:
        bb = BrowserBridge()
        result = bb.evaluate("document.title")
        assert result["action"] == "evaluate"

    def test_action_log(self) -> None:
        bb = BrowserBridge()
        bb.navigate("http://localhost")
        bb.click("button")
        assert len(bb.action_log) == 2

    def test_clear_log(self) -> None:
        bb = BrowserBridge()
        bb.navigate("http://localhost")
        bb.clear_log()
        assert len(bb.action_log) == 0


class TestObservabilityBridge:
    def _create_run(self, tmpdir: str, trace_id: str, record: dict) -> None:
        run_dir = Path(tmpdir) / trace_id
        run_dir.mkdir(parents=True)
        (run_dir / "run_record.json").write_text(json.dumps(record), encoding="utf-8")

    def test_list_traces(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_run(tmpdir, "t-1", {"status": "success"})
            self._create_run(tmpdir, "t-2", {"status": "failure"})
            ob = ObservabilityBridge(runs_dir=tmpdir)
            traces = ob.list_traces()
            assert sorted(traces) == ["t-1", "t-2"]

    def test_list_traces_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ob = ObservabilityBridge(runs_dir=tmpdir)
            assert ob.list_traces() == []

    def test_get_run_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_run(tmpdir, "t-1", {"status": "success", "tokens_used": 500})
            ob = ObservabilityBridge(runs_dir=tmpdir)
            record = ob.get_run_record("t-1")
            assert record["status"] == "success"

    def test_get_run_record_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ob = ObservabilityBridge(runs_dir=tmpdir)
            assert ob.get_run_record("nonexistent") is None

    def test_get_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_run(tmpdir, "t-1", {
                "tokens_used": 500, "cost_usd": 0.05, "duration_ms": 1200, "status": "success",
            })
            ob = ObservabilityBridge(runs_dir=tmpdir)
            m = ob.get_metrics("t-1")
            assert m["tokens_used"] == 500
            assert m["cost_usd"] == 0.05

    def test_aggregate_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_run(tmpdir, "t-1", {"tokens_used": 500, "cost_usd": 0.05, "duration_ms": 100})
            self._create_run(tmpdir, "t-2", {"tokens_used": 300, "cost_usd": 0.03, "duration_ms": 200})
            ob = ObservabilityBridge(runs_dir=tmpdir)
            agg = ob.aggregate_metrics()
            assert agg["trace_count"] == 2
            assert agg["total_tokens"] == 800
            assert agg["avg_tokens"] == 400

    def test_get_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_run(tmpdir, "t-1", {})
            art_dir = Path(tmpdir) / "t-1" / "artifacts"
            art_dir.mkdir()
            (art_dir / "art-1.json").write_text("{}", encoding="utf-8")
            (art_dir / "art-2.json").write_text("{}", encoding="utf-8")
            ob = ObservabilityBridge(runs_dir=tmpdir)
            assert sorted(ob.get_artifacts("t-1")) == ["art-1", "art-2"]
