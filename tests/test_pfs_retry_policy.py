import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.agent import BusinessAgent
from agent.retry import (
    call_with_retry,
    is_context_length_error,
    is_provider_switchable,
    is_retryable,
)
from LLM.llm_config_manager import LLMConfig, get_llm_client_with_fallback


class PfsRetryPolicyTests(unittest.TestCase):
    def test_transient_service_error_retries_with_exponential_backoff(self):
        calls = []

        def flaky_call(value):
            calls.append(value)
            if len(calls) < 3:
                raise RuntimeError("upstream returned 503")
            return "ok"

        with patch("agent.retry.time.sleep") as sleep:
            result = call_with_retry(flaky_call, "payload", max_retries=2)

        self.assertEqual("ok", result)
        self.assertEqual(["payload", "payload", "payload"], calls)
        self.assertEqual([3.0, 6.0], [call.args[0] for call in sleep.call_args_list])

    def test_non_retryable_error_is_returned_without_waiting(self):
        calls = []

        def rejected_call():
            calls.append(True)
            raise RuntimeError("upstream returned 401 unauthorized")

        with patch("agent.retry.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "401"):
                call_with_retry(rejected_call, max_retries=3)

        self.assertEqual(1, len(calls))
        sleep.assert_not_called()

    def test_context_overflow_is_not_retried_as_a_network_failure(self):
        error = RuntimeError("context_length_exceeded: prompt is too long")
        self.assertTrue(is_context_length_error(error))
        self.assertEqual((False, 0.0), is_retryable(error))
        self.assertFalse(is_provider_switchable(error))

    def test_authentication_failure_can_switch_provider(self):
        self.assertTrue(is_provider_switchable(RuntimeError("401 unauthorized")))

    def test_fallback_priority_includes_minimax(self):
        class FakeManager:
            configs = {
                "minimax": LLMConfig(
                    provider="minimax",
                    api_key="minimax-key",
                    base_url="https://example.invalid/v1",
                    model="MiniMax-M3",
                    enabled=True,
                ),
                "openai": LLMConfig(
                    provider="openai",
                    api_key="openai-key",
                    base_url="https://example.invalid/v1",
                    model="fallback-model",
                    enabled=True,
                ),
            }

            def get_config(self, provider):
                return self.configs.get(provider)

        with patch(
            "LLM.llm_config_manager.get_config_manager",
            return_value=FakeManager(),
        ), patch("openai.OpenAI", return_value=object()):
            _client, provider, _config = get_llm_client_with_fallback(
                preferred_provider="deepseek",
                excluded_providers={"deepseek"},
            )

        self.assertEqual("minimax", provider)

    def test_agent_switches_provider_after_primary_stream_setup_failure(self):
        def chunk(text):
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    finish_reason="stop",
                    delta=SimpleNamespace(
                        content=text,
                        reasoning_content=None,
                        tool_calls=[],
                    ),
                )],
            )

        class FakeCompletions:
            def __init__(self, *, failure=None, response=None):
                self.failure = failure
                self.response = response
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if self.failure:
                    raise self.failure
                return self.response

        class FakeClient:
            def __init__(self, completions):
                self.chat = SimpleNamespace(completions=completions)

        primary_completions = FakeCompletions(
            failure=RuntimeError("upstream returned 503"),
        )
        fallback_completions = FakeCompletions(
            response=[chunk("fallback answer")],
        )
        primary = FakeClient(primary_completions)
        fallback = FakeClient(fallback_completions)
        fallback_config = LLMConfig(
            provider="openai",
            api_key="test-key",
            base_url="https://example.invalid/v1",
            model="fallback-model",
            context_window=32_000,
            max_output_tokens=2_000,
            supports_prompt_cache=True,
            prompt_cache_mode="openai",
        )
        agent = BusinessAgent(
            client=primary,
            model="primary-model",
            session_id="pfs-fallback-test",
            user_id="pfs-test-user",
            provider="deepseek",
            supports_prompt_cache=True,
            prompt_cache_mode="deepseek",
        )

        def invoke_without_sleep(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch(
            "LLM.llm_config_manager.get_llm_client_with_fallback",
            return_value=(fallback, "openai", fallback_config),
        ), patch("agent.agent._call_with_retry", side_effect=invoke_without_sleep):
            events = list(agent.run("请返回一句话", history=[]))

        self.assertEqual(1, len(primary_completions.calls))
        self.assertEqual(1, len(fallback_completions.calls))
        self.assertEqual("fallback-model", fallback_completions.calls[0]["model"])
        self.assertNotIn("extra_body", fallback_completions.calls[0])
        self.assertTrue(
            fallback_completions.calls[0]["prompt_cache_key"].startswith("pfs-")
        )
        self.assertEqual("openai", agent._provider)
        self.assertEqual("fallback-model", agent.model)
        self.assertIn(
            "当前模型暂时不可用，已切换备用模型，正在重试…",
            [event.get("message") for event in events if event.get("type") == "agent_activity"],
        )
        self.assertIn(
            {"type": "text", "content": "fallback answer"},
            events,
        )


if __name__ == "__main__":
    unittest.main()
