import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ExtensionCrossProcessTests(unittest.TestCase):
    @staticmethod
    def _run(script: str, env: dict[str, str]) -> str:
        result = subprocess.run(
            [sys.executable, "-B", "-c", textwrap.dedent(script)],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def test_knowledge_and_memory_survive_independent_process_readback(self):
        with tempfile.TemporaryDirectory(prefix="pfs-extension-process-") as raw:
            data_root = Path(raw) / "data"
            env = os.environ.copy()
            env.update({"PFS_DATA_DIR": str(data_root), "PFS_NO_BROWSER": "1"})

            self._run(
                """
                from Function.Knowledge.knowledge_base import KnowledgeBase
                from data import memory_store

                kb = KnowledgeBase(user_id="process-user")
                try:
                    kb.add_rule(
                        rule_id="orders_nonnegative",
                        description="订单量不能为负",
                        condition="orders < 0",
                        severity="error",
                    )
                    kb.index_document(
                        "process-note.md",
                        "跨进程知识检索样例：订单量按自然日汇总。",
                    )
                finally:
                    kb.close()

                memory_store.create_record(
                    {
                        "name": "process-preference",
                        "type": "user",
                        "title": "跨进程偏好",
                        "body": "优先显示订单量和来源。",
                    },
                    user_id="process-user",
                )
                """,
                env,
            )

            output = self._run(
                """
                import json

                from Function.Knowledge.knowledge_base import KnowledgeBase
                from data import memory_store

                kb = KnowledgeBase(user_id="process-user")
                try:
                    rules = kb.search("orders_nonnegative")["rules"]
                    documents = kb.search("跨进程知识检索")["documents"]
                finally:
                    kb.close()

                memory = memory_store.get_record(
                    "process-preference", user_id="process-user"
                )
                print(json.dumps({
                    "rule": any(item["rule_id"] == "orders_nonnegative" for item in rules),
                    "document": any(
                        item["source_name"] == "process-note.md"
                        for item in documents
                    ),
                    "memory": memory["body"] if memory else "",
                }, ensure_ascii=False))
                """,
                env,
            )
            payload = json.loads(output.strip().splitlines()[-1])

        self.assertTrue(payload["rule"])
        self.assertTrue(payload["document"])
        self.assertEqual("优先显示订单量和来源。", payload["memory"])


if __name__ == "__main__":
    unittest.main()
