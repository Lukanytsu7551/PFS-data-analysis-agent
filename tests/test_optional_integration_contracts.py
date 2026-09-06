import os
import tempfile
import unittest
from unittest.mock import patch

from agent.tools.workspace.teams import WorkspaceTeamStore
from api import create_app
from infrastructure.compat import cloud_login_enabled


class OptionalIntegrationContractTests(unittest.TestCase):
    @staticmethod
    def _app():
        os.environ["PFS_NO_BROWSER"] = "1"
        app = create_app()
        app.config.update(TESTING=True)
        return app

    def test_local_team_mailbox_lifecycle_is_durable_and_reviewable(self):
        with tempfile.TemporaryDirectory(prefix="pfs-team-contract-") as raw:
            with patch.dict(os.environ, {"PFS_DATA_DIR": raw}, clear=False):
                store = WorkspaceTeamStore("team-contract-session")
                created = store.create(
                    "经营复核",
                    "用于本地结果复核的团队",
                    [{"name": "分析员", "role": "analyst"}],
                )
                self.assertTrue(created["quality_reviewer"]["auto_added"])
                self.assertEqual(2, len(created["members"]))

                sent = store.send_message(
                    "经营复核", "分析员", "请复核城市订单量", sender="leader",
                )
                self.assertEqual(1, sent["sent"])
                started = store.begin_member_turn("经营复核", "分析员")
                self.assertEqual(1, len(started["inbox"]))
                self.assertEqual("running", started["member"]["status"])
                completed = store.complete_member_turn(
                    "经营复核",
                    "分析员",
                    "已复核：城市订单量与明细一致。",
                    tool_events=[{"tool": "query_data", "status": "ok"}],
                )
                self.assertEqual("idle", completed["status"])

                status = store.status("经营复核")
                self.assertEqual(1, status["lead_unread_messages"])
                self.assertTrue(
                    any(
                        message["message_type"] == "result"
                        for message in status["recent_messages"]
                    )
                )
                self.assertEqual(
                    2, store.clear_messages("经营复核")["cleared_messages"]
                )
                self.assertEqual(
                    "经营复核",
                    store.delete("经营复核", require_inactive=True)["deleted"],
                )
                self.assertEqual([], store.list())

    def test_hooks_api_validates_prompt_hooks_and_rejects_side_effect_tests(self):
        app = self._app()
        prompt_settings = {
            "enabled": True,
            "allow_command_hooks": False,
            "hooks": [{
                "id": "contract-prompt",
                "name": "本地提示",
                "event": "turn_start",
                "action": {
                    "type": "prompt",
                    "message": "请关注：$MESSAGE",
                    "timeout": 5,
                },
            }],
        }
        with app.test_client() as client:
            validated = client.post("/api/hooks/validate", json=prompt_settings)
            tested = client.post(
                "/api/hooks/test",
                json={
                    "event": "turn_start",
                    "settings": prompt_settings,
                    "context": {"message": "检查利润口径"},
                },
            )
            side_effect = client.post(
                "/api/hooks/test",
                json={
                    "event": "turn_start",
                    "settings": {
                        "enabled": True,
                        "hooks": [{
                            "id": "contract-http",
                            "event": "turn_start",
                            "action": {
                                "type": "http",
                                "url": "http://127.0.0.1:9/should-not-run",
                                "timeout": 1,
                            },
                        }],
                    },
                },
            )
            invalid = client.post(
                "/api/hooks/validate",
                json={
                    "hooks": [{
                        "id": "bad-timeout",
                        "event": "turn_start",
                        "action": {
                            "type": "prompt", "message": "x", "timeout": 0,
                        },
                    }],
                },
            )

        self.assertEqual(200, validated.status_code)
        self.assertEqual(200, tested.status_code)
        self.assertIn(
            "请关注：检查利润口径",
            tested.get_json()["prompt_messages"],
        )
        self.assertEqual(400, side_effect.status_code)
        self.assertIn("不会在此接口执行", side_effect.get_json()["error"])
        self.assertEqual(400, invalid.status_code)

    def test_feishu_webhook_verification_and_challenge_are_local_contracts(self):
        app = self._app()
        with patch(
            "api.feishu_bot.event_verification_token",
            return_value="contract-token",
        ), app.test_client() as client:
            unauthorized = client.post(
                "/api/feishu-bot/events",
                json={"token": "wrong-token", "type": "url_verification"},
            )
            challenge = client.post(
                "/api/feishu-bot/events",
                json={
                    "token": "contract-token",
                    "type": "url_verification",
                    "challenge": "challenge-value",
                },
            )
            with patch("api.feishu_bot.dispatch_inbound_event", return_value=True) as dispatch:
                accepted = client.post(
                    "/api/feishu-bot/events",
                    json={
                        "token": "contract-token",
                        "header": {
                            "event_type": "im.message.receive_v1",
                            "event_id": "contract-event-1",
                        },
                        "event": {"message": {"chat_id": "oc_contract"}},
                    },
                )

        self.assertEqual(401, unauthorized.status_code)
        self.assertEqual(200, challenge.status_code)
        self.assertEqual("challenge-value", challenge.get_json()["challenge"])
        self.assertEqual(200, accepted.status_code)
        self.assertTrue(accepted.get_json()["accepted"])
        dispatch.assert_called_once()

    def test_cloud_login_requires_both_explicit_flag_and_host_marker(self):
        self.assertFalse(
            cloud_login_enabled({"PFS_ENABLE_CLOUD_LOGIN": "1"})
        )
        self.assertFalse(
            cloud_login_enabled({"RAILWAY_PROJECT_ID": "pfs-prod"})
        )
        self.assertTrue(
            cloud_login_enabled({
                "PFS_ENABLE_CLOUD_LOGIN": "true",
                "RAILWAY_PROJECT_ID": "pfs-prod",
            })
        )
        self.assertTrue(
            cloud_login_enabled({
                "PFS_ENABLE_CLOUD_LOGIN": "yes",
                "VERCEL": "1",
            })
        )

    def test_cloud_login_api_is_dormant_when_cloud_mode_is_off(self):
        app = self._app()
        requests = [
            ("post", "/api/auth/send-code", {"email": "user@example.com"}),
            ("post", "/api/auth/register", {"email": "user@example.com"}),
            ("post", "/api/auth/login", {"email": "user@example.com"}),
            ("post", "/api/auth/logout", None),
            ("get", "/api/auth/me", None),
        ]
        with patch("api.auth.is_cloud_managed", return_value=False):
            with app.test_client() as client:
                responses = [
                    getattr(client, method)(path, json=payload)
                    if payload is not None
                    else getattr(client, method)(path)
                    for method, path, payload in requests
                ]

        self.assertEqual([404] * len(requests), [response.status_code for response in responses])
        self.assertTrue(all(response.get_json()["error"] == "云端登录未启用" for response in responses))


if __name__ == "__main__":
    unittest.main()
