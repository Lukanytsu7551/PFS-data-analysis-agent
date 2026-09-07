import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Function.Knowledge.knowledge_base import KnowledgeBase


class ExtensionVerticalSliceTests(unittest.TestCase):
    @staticmethod
    def _app(data_root: Path):
        os.environ["PFS_NO_BROWSER"] = "1"
        from api import create_app

        app = create_app()
        app.config.update(TESTING=True)
        return app

    def test_knowledge_and_memory_are_real_api_scoped_flows(self):
        with tempfile.TemporaryDirectory(prefix="pfs-extension-slice-") as raw:
            root = Path(raw)
            with patch.dict(
                os.environ,
                {"PFS_DATA_DIR": str(root / "data")},
                clear=False,
            ):
                app = self._app(root / "data")
                user_a = {"X-PFS-User-ID": "extension-user-a"}
                user_b = {"X-PFS-User-ID": "extension-user-b"}
                with app.test_client() as client:
                    metric = client.post(
                        "/api/knowledge/metrics",
                        json={
                            "name": "月活用户",
                            "alias": "MAU",
                            "definition": "自然月内活跃用户数",
                        },
                        headers=user_a,
                    )
                    note = client.post(
                        "/api/knowledge/notes",
                        json={
                            "topic": "经营口径",
                            "content": "月活用户按自然月去重统计",
                            "tags": "用户,口径",
                        },
                        headers=user_a,
                    )
                    search_a = client.get(
                        "/api/knowledge/search?q=月活用户",
                        headers=user_a,
                    )
                    search_b = client.get(
                        "/api/knowledge/search?q=月活用户",
                        headers=user_b,
                    )
                    memory = client.post(
                        "/api/memory",
                        json={
                            "name": "demo-preference",
                            "type": "user",
                            "title": "演示偏好",
                            "body": "优先展示经营指标",
                        },
                        headers=user_a,
                    )
                    memory_a = client.get(
                        "/api/memory/demo-preference", headers=user_a
                    )
                    memory_b = client.get(
                        "/api/memory/demo-preference", headers=user_b
                    )
                    archived = client.delete(
                        "/api/memory/demo-preference",
                        json={"confirm": True},
                        headers=user_a,
                    )
                    commands = client.get("/api/commands")
                    unknown = client.post(
                        "/api/session/extension-slice/commands/not-real/execute",
                        json={},
                    )

                self.assertEqual(201, metric.status_code)
                self.assertEqual(201, note.status_code)
                search_payload = search_a.get_json()
                self.assertTrue(
                    any(item["name"] == "月活用户" for item in search_payload["metrics"])
                )
                self.assertTrue(
                    any(item["topic"] == "经营口径" for item in search_payload["notes"])
                )
                self.assertEqual(200, search_a.status_code)
                self.assertEqual(
                    {"metrics": [], "rules": [], "notes": [], "documents": []},
                    search_b.get_json(),
                )
                self.assertEqual(201, memory.status_code)
                self.assertEqual(200, memory_a.status_code)
                self.assertEqual(404, memory_b.status_code)
                self.assertEqual(200, archived.status_code)
                self.assertEqual(200, commands.status_code)
                command_names = {item["name"] for item in commands.get_json()["commands"]}
                self.assertIn("help", command_names)
                self.assertIn("compact", command_names)
                self.assertEqual(404, unknown.status_code)
                self.assertEqual("unknown_command", unknown.get_json()["code"])

                kb = KnowledgeBase(user_id="extension-user-a")
                try:
                    indexed = kb.index_document(
                        "经营口径.md",
                        "月活用户定义：自然月内去重后的活跃用户数。",
                    )
                    document_results = kb.search("月活用户定义")
                finally:
                    kb.close()
                self.assertGreater(indexed["chunks"], 0)
                self.assertTrue(
                    any(
                        item["source_name"] == "经营口径.md"
                        for item in document_results["documents"]
                    )
                )

    def test_skill_api_keeps_builtin_protection_and_custom_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix="pfs-skill-slice-") as raw:
            root = Path(raw)
            skills_root = root / "skills"
            with patch.dict(
                os.environ,
                {
                    "PFS_DATA_DIR": str(root / "data"),
                    "PFS_SKILLS_DIR": str(skills_root),
                },
                clear=False,
            ), patch(
                "agent.skills.loader.DEFAULT_USER_DIR", skills_root,
            ):
                app = self._app(root / "data")
                with app.test_client() as client:
                    listing = client.get("/api/skills")
                    builtin = client.get("/api/skills/data")
                    created = client.post(
                        "/api/skills",
                        json={
                            "name": "demo-skill",
                            "description": "用于演示的销售分析技能",
                            "prompt": "请按月汇总销售额。",
                        },
                    )
                    uploaded = client.post(
                        "/api/skills/upload",
                        data={
                            "file": (
                                io.BytesIO(
                                    "---\nname: uploaded-skill\ndescription: UTF-8 汉字 skill\n---\n请按月度汇总。\n".encode("utf-8")
                                ),
                                "SKILL.md",
                            ),
                        },
                        content_type="multipart/form-data",
                    )
                    custom = client.get("/api/skills/demo-skill")
                    denied = client.put(
                        "/api/skills/data",
                        json={
                            "description": "不应覆盖内置技能",
                            "prompt": "不应写入",
                        },
                    )
                    updated = client.put(
                        "/api/skills/demo-skill",
                        json={
                            "name": "demo-skill",
                            "description": "更新后的演示技能",
                            "prompt": "请按区域分析销售额。",
                        },
                    )
                    deleted = client.delete("/api/skills/demo-skill")
                    uploaded_deleted = client.delete("/api/skills/uploaded-skill")

                self.assertEqual(200, listing.status_code)
                self.assertEqual([], listing.get_json()["diagnostics"])
                self.assertTrue(
                    any(item["name"] == "data" for item in listing.get_json()["skills"])
                )
                self.assertEqual(200, builtin.status_code)
                self.assertEqual("builtin", builtin.get_json()["skill"]["source"])
                self.assertEqual(201, created.status_code)
                self.assertEqual(201, uploaded.status_code)
                self.assertEqual("uploaded-skill", uploaded.get_json()["name"])
                self.assertEqual(200, custom.status_code)
                self.assertEqual("demo-skill", custom.get_json()["skill"]["name"])
                self.assertEqual(403, denied.status_code)
                self.assertEqual(200, updated.status_code)
                self.assertEqual(200, deleted.status_code)
                self.assertEqual(200, uploaded_deleted.status_code)

    def test_team_endpoints_enforce_session_ownership(self):
        """Every session-scoped Teams route must fail closed for another user."""
        app = self._app(Path("/tmp/pfs-team-ownership-test"))
        routes = (
            ("get", "/api/session/owned-by-a/teams"),
            ("get", "/api/session/owned-by-a/teams/demo"),
            ("delete", "/api/session/owned-by-a/teams/demo"),
            ("delete", "/api/session/owned-by-a/teams/demo/messages"),
            ("get", "/api/session/owned-by-a/team-plans"),
            ("get", "/api/session/owned-by-a/team-plans/plan-1"),
            ("post", "/api/session/owned-by-a/team-plans/plan-1/actions/cancel"),
            ("post", "/api/session/owned-by-a/team-plans/plan-1/workflow-draft"),
        )
        with patch(
            "api.state.check_session_ownership",
            return_value=(False, "user-b"),
        ):
            with app.test_client() as client:
                for method, path in routes:
                    with self.subTest(method=method, path=path):
                        response = getattr(client, method)(path, json={})
                        self.assertEqual(403, response.status_code)
                        self.assertEqual("forbidden", response.get_json()["code"])


if __name__ == "__main__":
    unittest.main()
