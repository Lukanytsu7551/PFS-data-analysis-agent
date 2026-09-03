import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.tools.schemas import AGENT_TOOLS
from api import create_app
from data.datasource_config_manager import DataSourceConfigManager


ROOT = Path(__file__).resolve().parents[1]


class RetiredFeatureTests(unittest.TestCase):
    def test_business_canvas_and_google_sheets_routes_are_absent(self):
        routes = {rule.rule for rule in create_app().url_map.iter_rules()}
        self.assertFalse(any("business-canvas" in route for route in routes))
        self.assertFalse(any("connect-gsheets" in route for route in routes))

    def test_business_canvas_agent_tools_are_absent(self):
        names = {
            str((tool.get("function") or {}).get("name") or "")
            for tool in AGENT_TOOLS
        }
        self.assertTrue({
            "display_diagram", "edit_diagram", "get_diagram", "get_shape_library",
        }.isdisjoint(names))

    def test_retired_implementation_files_are_removed(self):
        for relative in (
            "api/business_canvas.py",
            "data/business_canvas_store.py",
            "data/sources/gsheets.py",
            "frontend/features/business-canvas.js",
            "frontend/features/ui/business-canvas-ui.js",
            "static/drawio/index.html",
            "data/shape_libs",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_legacy_google_credentials_are_not_returned_or_resaved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "datasource.json"
            config_path.write_text(
                '{"gsheets":{"creds_json":"secret","spreadsheet":"old"},'
                '"api":{"url":"https://example.invalid"}}',
                encoding="utf-8",
            )
            with patch("data.datasource_config_manager._CONFIG_FILE", config_path), patch(
                "data.datasource_config_manager._CONFIG_DIR", config_path.parent
            ):
                manager = DataSourceConfigManager()
                public = manager.list_public()
                self.assertNotIn("gsheets", public)
                self.assertIn("api", public)
                with self.assertRaises(ValueError):
                    manager.save("gsheets", {"creds_json": "new-secret"})


if __name__ == "__main__":
    unittest.main()
