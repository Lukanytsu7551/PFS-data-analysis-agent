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
from data.sources.csv import CSVDataSource
from pathlib import Path
from agent.pricing import calculate_model_cost_usd, validate_cost_limit


class PfsRetryPolicyTests(unittest.TestCase):
    def test_model_cost_uses_input_and_output_rates(self):
        self.assertEqual(0.001, calculate_model_cost_usd(80, 20, input_price_per_million=10, output_price_per_million=10))
        self.assertIsNone(calculate_model_cost_usd(80, 20))
        with self.assertRaisesRegex(ValueError, "同时填写"):
            calculate_model_cost_usd(80, 20, input_price_per_million=1)

    def test_cost_limit_rejects_non_positive_or_non_finite_values(self):
        self.assertIsNone(validate_cost_limit(None))
        for value in (0, -1, "nan", "inf", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_cost_limit(value)

    def test_agent_rejects_cost_budget_without_complete_price_pair(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
        with self.assertRaisesRegex(ValueError, "同时填写"):
            BusinessAgent(
                client=client,
                model="pfs-missing-price-test",
                session_id="pfs-missing-price-test",
                max_cost_usd=0.01,
            )

    def test_agent_hard_stops_after_actual_cost_budget_is_reached(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return [SimpleNamespace(
                    usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100),
                    choices=[SimpleNamespace(
                        finish_reason="stop",
                        delta=SimpleNamespace(content="已生成分析", reasoning_content=None, tool_calls=None),
                    )],
                )]

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-cost-budget-test",
            session_id="pfs-cost-budget-test",
            input_price_per_million=10,
            output_price_per_million=10,
            max_cost_usd=0.001,
        )
        events = list(agent.run("生成摘要", history=[]))
        self.assertEqual(1, completions.calls)
        usage = next(event for event in events if event.get("type") == "usage")
        self.assertEqual(0.001, usage["cost_usd"])
        self.assertEqual(0.001, usage["run_total_cost_usd"])
        self.assertIn("run_cost_budget_exceeded", [event.get("code") for event in events])
        self.assertEqual({"type": "done"}, events[-1])

    def test_delegated_agent_hard_stops_after_cost_budget(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return SimpleNamespace(
                    model="pfs-delegated-cost-test",
                    usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100),
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content="",
                        tool_calls=[SimpleNamespace(id="cost-call", function=SimpleNamespace(name="get_schema", arguments="{}"))],
                    ))],
                )

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-delegated-cost-test",
            session_id="pfs-delegated-cost-test",
            input_price_per_million=10,
            output_price_per_million=10,
        )
        with patch.object(agent, "_execute_delegated_tool", side_effect=AssertionError("tool must not run")) as execute_tool:
            result = agent._run_delegated_llm(
                member={"role": "analyst", "instructions": ""},
                prompt="检查数据",
                max_cost_usd=0.001,
            )
        self.assertEqual(1, completions.calls)
        execute_tool.assert_not_called()
        self.assertTrue(result["usage"]["cost_budget_exceeded"])
        self.assertEqual(0.001, result["usage"]["cost_usd"])

    def test_delegated_agent_rejects_cost_budget_without_complete_price_pair(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
        agent = BusinessAgent(
            client=client,
            model="pfs-delegated-missing-price-test",
            session_id="pfs-delegated-missing-price-test",
        )
        with self.assertRaisesRegex(ValueError, "同时填写"):
            agent._run_delegated_llm(
                member={"role": "analyst", "instructions": ""},
                prompt="检查数据",
                max_cost_usd=0.001,
            )

    def test_agent_hard_stops_when_run_tool_budget_is_exhausted(self):
        fixture = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pfs_sales.csv"

        def tool_chunk(call_id):
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[SimpleNamespace(
                            index=0,
                            id=call_id,
                            function=SimpleNamespace(
                                name="get_schema", arguments="{}",
                            ),
                        )],
                    ),
                )],
            )

        class FakeCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return [tool_chunk(f"call-{self.calls}")]

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-budget-test",
            data_source=CSVDataSource(str(fixture), fixture.name),
            session_id="pfs-budget-test",
            max_iterations=5,
            max_tool_calls=1,
        )

        events = list(agent.run("读取数据结构并继续", history=[]))

        self.assertEqual(2, completions.calls)
        self.assertIn(
            {
                "type": "policy_decision",
                "tool": "get_schema",
                "allowed": False,
                "code": "run_tool_budget_exceeded",
                "reason": "the run tool-call budget is exhausted",
            },
            events,
        )
        self.assertIn(
            {
                "type": "policy_decision",
                "tool": "get_schema",
                "allowed": True,
                "code": "allowed",
                "reason": "tool call passed the policy gate",
            },
            events,
        )
        self.assertIn(
            {
                "type": "error",
                "message": "本次分析已达到工具调用上限，已安全停止。请缩小问题范围后重试。",
            },
            events,
        )
        self.assertEqual({"type": "done"}, events[-1])

    def test_agent_hard_stops_after_actual_token_budget_is_reached(self):
        def usage_chunk():
            return SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100)

        class FakeCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return [SimpleNamespace(
                    usage=usage_chunk(),
                    choices=[SimpleNamespace(
                        finish_reason="stop",
                        delta=SimpleNamespace(
                            content="已生成分析",
                            reasoning_content=None,
                            tool_calls=None,
                        ),
                    )],
                )]

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-token-budget-test",
            session_id="pfs-token-budget-test",
            max_total_tokens=100,
        )

        events = list(agent.run("生成摘要", history=[]))

        self.assertEqual(1, completions.calls)
        self.assertIn(
            {
                "type": "policy_decision",
                "tool": "llm.run",
                "allowed": False,
                "code": "run_token_budget_exceeded",
                "reason": "the run token budget is exhausted",
            },
            events,
        )
        self.assertIn(
            {
                "type": "error",
                "message": "本次分析已达到 Token 预算上限，已安全停止。请缩小问题范围后重试。",
            },
            events,
        )
        self.assertEqual({"type": "done"}, events[-1])

    def test_delegated_agent_does_not_execute_tools_after_token_budget(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return SimpleNamespace(
                    model="pfs-delegated-budget-test",
                    usage=SimpleNamespace(
                        prompt_tokens=80,
                        completion_tokens=20,
                        total_tokens=100,
                    ),
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[SimpleNamespace(
                                id="delegated-call-1",
                                function=SimpleNamespace(
                                    name="get_schema", arguments="{}",
                                ),
                            )],
                        ),
                    )],
                )

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-delegated-budget-test",
            session_id="pfs-delegated-budget-test",
        )

        with patch.object(
            agent, "_execute_delegated_tool",
            side_effect=AssertionError("tool must not run"),
        ) as execute_tool:
            result = agent._run_delegated_llm(
                member={"role": "analyst", "instructions": ""},
                prompt="检查数据结构",
                max_tool_calls=3,
                max_total_tokens=100,
            )

        self.assertEqual(1, completions.calls)
        execute_tool.assert_not_called()
        self.assertTrue(result["usage"]["token_budget_exceeded"])
        self.assertEqual(80, result["usage"]["input_tokens"])
        self.assertEqual(20, result["usage"]["output_tokens"])
        self.assertIn("Token 预算上限", result["content"])

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
