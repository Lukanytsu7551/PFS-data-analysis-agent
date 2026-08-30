import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.tools.business.diagram import (
    handle_display_diagram,
    handle_edit_diagram,
    handle_get_diagram,
    handle_get_shape_library,
)
from data.business_canvas_store import reset_business_canvas_store


class PfsDiagramToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="pfs-diagram-")
        self.db_path = Path(self.temp_dir.name) / "canvas.sqlite"
        self.env = patch.dict(os.environ, {"PFS_BUSINESS_CANVAS_DB": str(self.db_path)})
        self.env.start()
        reset_business_canvas_store()

    def tearDown(self):
        reset_business_canvas_store()
        self.env.stop()
        self.temp_dir.cleanup()

    def test_template_diagram_can_be_created_read_and_edited(self):
        created = handle_display_diagram(
            {
                "template_id": "swot_analysis",
                "title": "PFS SWOT",
                "content": {
                    "strengths": "可追踪的分析口径",
                    "weaknesses": "外部连接器仍待验收",
                },
            },
            session_id="pfs-diagram-session",
        )
        self.assertTrue(created["ok"])
        project_id = created["project"]["id"]
        self.assertIn("可追踪的分析口径", created["xml"])

        fetched = handle_get_diagram(
            {"project_id": project_id},
            session_id="pfs-diagram-session",
        )
        self.assertTrue(fetched["ok"])
        self.assertEqual("swot_analysis", fetched["template_id"])

        edited = handle_edit_diagram(
            {
                "project_id": project_id,
                "operations": [{
                    "operation": "update",
                    "cell_id": "3",
                    "new_xml": (
                        '<mxCell id="3" value="PFS 已验证优势" '
                        'style="text;html=1;" vertex="1" parent="2">'
                        '<mxGeometry width="100" height="50" as="geometry"/>'
                        '</mxCell>'
                    ),
                }],
            },
            session_id="pfs-diagram-session",
        )
        self.assertTrue(edited["ok"])
        self.assertIn("PFS 已验证优势", edited["xml"])

    def test_shape_library_is_local_and_path_traversal_is_rejected(self):
        flowchart = handle_get_shape_library({"library": "flowchart"})
        self.assertTrue(flowchart["ok"])
        self.assertIn("mxgraph.flowchart", flowchart["content"])

        rejected = handle_get_shape_library({"library": "../flowchart"})
        self.assertFalse(rejected["ok"])
        self.assertEqual("Invalid library name", rejected["error"])


if __name__ == "__main__":
    unittest.main()
