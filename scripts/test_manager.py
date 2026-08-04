#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "codex-deepseek-subagent"
    / "scripts"
    / "codex_deepseek.py"
)
spec = importlib.util.spec_from_file_location("codex_deepseek", SCRIPT)
manager = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = manager
spec.loader.exec_module(manager)


class ManagerTests(unittest.TestCase):
    def test_managed_block_is_idempotent(self) -> None:
        original = 'model = "gpt-5.6-sol"\n\n[features]\nmulti_agent = true\n'
        with tempfile.TemporaryDirectory() as directory:
            paths = manager.resolve_paths(directory)
            first = original.rstrip() + "\n" + manager.managed_config_block(paths.agent)
            second = manager.remove_managed_blocks(first).rstrip() + "\n" + manager.managed_config_block(paths.agent)
            self.assertEqual(first, second)
            manager.parse_toml_text(second)

    def test_top_level_catalog_stays_before_tables(self) -> None:
        source = '[features]\nmulti_agent = true\n'
        updated = manager.set_top_level_key(source, "model_catalog_json", "/tmp/models.json")
        parsed = manager.parse_toml_text(updated)
        self.assertEqual(parsed["model_catalog_json"], "/tmp/models.json")
        self.assertEqual(parsed["features"]["multi_agent"], True)

    def test_catalog_merge_preserves_openai_models(self) -> None:
        base = {"models": [{"slug": "gpt-test"}, {"slug": manager.MODEL, "old": True}]}
        merged = manager.merged_catalog(base, {"slug": manager.MODEL, "new": True})
        by_slug = {item["slug"]: item for item in merged["models"]}
        self.assertIn("gpt-test", by_slug)
        self.assertEqual(by_slug[manager.MODEL], {"slug": manager.MODEL, "new": True})

    def test_agent_is_text_only_high_reasoning(self) -> None:
        text = manager.expected_agent_text()
        self.assertIn('model_provider = "deepseek"', text)
        self.assertIn('model_reasoning_effort = "high"', text)
        self.assertIn("text-only", text)

    def test_status_reports_partial_for_empty_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status = manager.static_status(manager.resolve_paths(directory))
            self.assertEqual(status["status"], "partial")
            self.assertFalse(status["checks"]["provider_registered"])


if __name__ == "__main__":
    unittest.main()
