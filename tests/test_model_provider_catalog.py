import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from LLM.llm_config_manager import (
    LLMConfig,
    LLMConfigManager,
    RETIRED_BUILTIN_PROVIDERS,
    SUPPORTED_BUILTIN_PROVIDERS,
    get_llm_client,
)
from api.models import bp as models_bp, model_defaults


class ModelProviderCatalogTests(unittest.TestCase):
    def _manager(self, root: Path, data: dict) -> LLMConfigManager:
        config_path = root / "llm_config.json"
        config_path.write_text(json.dumps(data), encoding="utf-8")
        with (
            patch("LLM.llm_config_manager.LLM_CONFIG_FILE", config_path),
            patch("LLM.llm_config_manager.CONFIG_DIR", root),
        ):
            return LLMConfigManager()

    def test_public_catalog_hides_retired_builtins_but_keeps_custom_models(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._manager(
                Path(temp_dir),
                {
                    "openai": {
                        "provider": "openai",
                        "api_key": "legacy-openai",
                        "base_url": "https://api.openai.com/v1",
                        "model": "legacy",
                    },
                    "atlascloud": {
                        "provider": "atlascloud",
                        "api_key": "legacy-atlas",
                        "base_url": "https://api.atlascloud.ai/v1",
                        "model": "legacy",
                    },
                    "ollama": {
                        "provider": "ollama",
                        "api_key": "no-key",
                        "base_url": "http://localhost:11434/v1",
                        "model": "legacy",
                    },
                    "deepseek": {
                        "provider": "deepseek",
                        "api_key": "deepseek-key",
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-chat",
                    },
                    "custom_local": {
                        "provider": "custom_local",
                        "api_key": "no-key",
                        "base_url": "http://127.0.0.1:9000/v1",
                        "model": "local-model",
                        "name": "自定义本地模型",
                        "is_custom": True,
                    },
                },
            )

            public = manager.list_configs()

            self.assertEqual(set(SUPPORTED_BUILTIN_PROVIDERS) & set(public), {"deepseek"})
            self.assertIn("custom_local", public)
            self.assertTrue(RETIRED_BUILTIN_PROVIDERS.isdisjoint(public))
            self.assertTrue(RETIRED_BUILTIN_PROVIDERS.isdisjoint(LLMConfigManager.DEFAULT_CONFIGS))
            self.assertEqual("deepseek", manager.get_default_provider())
            self.assertEqual(["deepseek", "custom_local"], manager.get_enabled_providers())

    def test_retired_builtin_cannot_be_set_or_tested_but_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "llm_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "openai": {
                            "provider": "openai",
                            "api_key": "legacy-openai",
                            "base_url": "https://api.openai.com/v1",
                            "model": "legacy",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("LLM.llm_config_manager.LLM_CONFIG_FILE", config_path),
                patch("LLM.llm_config_manager.CONFIG_DIR", root),
            ):
                manager = LLMConfigManager()
                self.assertFalse(manager.set_config("openai", "new-key"))
                self.assertEqual("provider_retired", manager.test_config("openai")["code"])
                ok, _message = manager.clear_builtin_config("openai")
                self.assertTrue(ok)

            self.assertNotIn("openai", json.loads(config_path.read_text()))

    def test_environment_loading_does_not_recreate_retired_builtins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "llm_config.json"
            with (
                patch("LLM.llm_config_manager.LLM_CONFIG_FILE", config_path),
                patch("LLM.llm_config_manager.CONFIG_DIR", root),
                patch.dict(
                    "os.environ",
                    {"OPENAI_API_KEY": "must-not-load", "DEEPSEEK_API_KEY": "allowed"},
                    clear=False,
                ),
            ):
                manager = LLMConfigManager(load_from_env=True)

            self.assertIn("deepseek", manager.configs)
            self.assertNotIn("openai", manager.configs)

    def test_public_model_defaults_exclude_retired_builtins(self):
        app = Flask(__name__)
        with app.test_request_context():
            payload = model_defaults().get_json()

        self.assertEqual(set(SUPPORTED_BUILTIN_PROVIDERS), set(payload))
        self.assertTrue(RETIRED_BUILTIN_PROVIDERS.isdisjoint(payload))

    def test_all_supported_builtin_configs_can_call_a_local_openai_compatible_fixture(self):
        """Exercise every built-in endpoint without using a vendor credential.

        This is intentionally a local transport check, not an external provider
        acceptance test.  It catches broken base URL/model wiring and keeps the
        Coding Plan entries on the same request path as the regular models.
        """
        requests = []
        requests_lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib handler API
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                with requests_lock:
                    requests.append(
                        {
                            "authorization": self.headers.get("Authorization"),
                            "payload": body,
                        }
                    )
                response = {
                    "id": "fixture-completion",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "fixture ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            fixture_url = f"http://127.0.0.1:{server.server_port}/v1"
            environment = {}
            for provider in SUPPORTED_BUILTIN_PROVIDERS:
                defaults = LLMConfigManager.DEFAULT_CONFIGS[provider]
                environment[defaults["env_var"]] = f"{provider}-fixture-key"
                environment[f"{provider.upper()}_BASE_URL"] = fixture_url

            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                with (
                    patch("LLM.llm_config_manager.LLM_CONFIG_FILE", root / "llm_config.json"),
                    patch("LLM.llm_config_manager.CONFIG_DIR", root),
                    patch.dict(os.environ, environment, clear=False),
                ):
                    manager = LLMConfigManager(load_from_env=True)
                    self.assertEqual(set(SUPPORTED_BUILTIN_PROVIDERS), set(manager.configs))

                    results = {
                        provider: manager.test_config(provider)
                        for provider in SUPPORTED_BUILTIN_PROVIDERS
                    }

            for provider, result in results.items():
                self.assertTrue(result["success"], msg=f"{provider}: {result}")
                self.assertEqual(
                    LLMConfigManager.DEFAULT_CONFIGS[provider]["model"],
                    result["model"],
                )

            with requests_lock:
                captured = list(requests)
            self.assertEqual(len(SUPPORTED_BUILTIN_PROVIDERS), len(captured))
            self.assertEqual(
                [
                    LLMConfigManager.DEFAULT_CONFIGS[provider]["model"]
                    for provider in SUPPORTED_BUILTIN_PROVIDERS
                ],
                [entry["payload"]["model"] for entry in captured],
            )
            self.assertTrue(
                all(
                    entry["authorization"] == f"Bearer {provider}-fixture-key"
                    for provider, entry in zip(SUPPORTED_BUILTIN_PROVIDERS, captured)
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_http_model_endpoints_enforce_retirement_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "llm_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "openai": {
                            "provider": "openai",
                            "api_key": "legacy-openai",
                            "base_url": "https://api.openai.com/v1",
                            "model": "legacy",
                        },
                        "custom_local": {
                            "provider": "custom_local",
                            "api_key": "no-key",
                            "base_url": "http://127.0.0.1:9000/v1",
                            "model": "local-model",
                            "name": "自定义本地模型",
                            "is_custom": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            app = Flask(__name__)
            app.register_blueprint(models_bp)
            with (
                patch("LLM.llm_config_manager.LLM_CONFIG_FILE", config_path),
                patch("LLM.llm_config_manager.CONFIG_DIR", root),
            ):
                manager = LLMConfigManager()
                with patch("api.models.config_manager", manager):
                    with app.test_client() as client:
                        listed = client.get("/api/models")
                        defaults = client.get("/api/models/defaults")
                        tested = client.post(
                            "/api/models/test",
                            json={"provider": "openai"},
                        )
                        set_retired = client.post(
                            "/api/models/set-builtin",
                            json={"provider": "openai", "api_key": "new-key"},
                        )
                        cleared = client.post(
                            "/api/models/clear-builtin",
                            json={"provider": "openai"},
                        )

            self.assertEqual(200, listed.status_code)
            self.assertIn("custom_local", listed.get_json())
            self.assertTrue(RETIRED_BUILTIN_PROVIDERS.isdisjoint(listed.get_json()))
            self.assertEqual(200, defaults.status_code)
            self.assertEqual(set(SUPPORTED_BUILTIN_PROVIDERS), set(defaults.get_json()))
            self.assertEqual(200, tested.status_code)
            self.assertEqual("provider_retired", tested.get_json()["code"])
            self.assertEqual(400, set_retired.status_code)
            self.assertEqual(200, cleared.status_code)
            self.assertTrue(cleared.get_json()["ok"])
            self.assertNotIn("openai", json.loads(config_path.read_text()))

    def test_llm_client_rejects_retired_provider_even_if_old_config_exists(self):
        manager = type(
            "Manager",
            (),
            {
                "get_default_provider": lambda self: "openai",
                "get_config": lambda self, provider: LLMConfig(
                    provider=provider,
                    api_key="legacy",
                    base_url="https://example.invalid/v1",
                    model="legacy-model",
                ),
            },
        )()
        with patch("LLM.llm_config_manager.get_config_manager", return_value=manager):
            with self.assertRaisesRegex(ValueError, "已退役"):
                get_llm_client("openai")


if __name__ == "__main__":
    unittest.main()
