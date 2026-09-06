import unittest
import time
from types import SimpleNamespace
from unittest.mock import patch

from agent.agent import AgentRunTimeout, BusinessAgent
from agent.compaction import compact_history
from agent.jobs import JobCanceled
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
        events = list(agent.run("生成摘要", history=[], run_id="run-cost-budget"))
        self.assertEqual(1, completions.calls)
        usage = next(event for event in events if event.get("type") == "usage")
        self.assertEqual("run-cost-budget", usage["run_id"])
        self.assertEqual("pfs-cost-budget-test", usage["model"])
        self.assertEqual(1, usage["model_calls"])
        self.assertEqual(0.001, usage["cost_usd"])
        self.assertEqual(0.001, usage["run_total_cost_usd"])
        self.assertIn("run_cost_budget_exceeded", [event.get("code") for event in events])
        self.assertEqual({"type": "done"}, events[-1])

    def test_agent_run_timeout_is_a_structured_failure(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
        agent = BusinessAgent(
            client=client,
            model="pfs-timeout-test",
            session_id="pfs-timeout-test",
            max_run_seconds=1,
        )

        clock_calls = [0]

        def monotonic_after_run_start():
            clock_calls[0] += 1
            return 0 if clock_calls[0] == 1 else 2

        with patch("agent.agent.time.monotonic", side_effect=monotonic_after_run_start):
            events = list(agent.run("hello", history=[]))

        self.assertEqual(
            {
                "type": "error",
                "message": "分析超过运行时间上限，已安全终止。请缩小问题范围后重试。",
                "code": "agent_run_timeout",
                "recovery_action": "retry_with_smaller_scope",
            },
            events[0],
        )
        self.assertEqual({"type": "done"}, events[-1])

    def test_agent_bounds_provider_request_timeout_to_remaining_run_budget(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return [SimpleNamespace(
                    usage=None,
                    choices=[SimpleNamespace(
                        finish_reason="stop",
                        delta=SimpleNamespace(
                            content="已完成",
                            reasoning_content=None,
                            tool_calls=None,
                        ),
                    )],
                )]

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-request-timeout-test",
            session_id="pfs-request-timeout-test",
            max_run_seconds=10,
        )

        events = list(agent.run("hello", history=[]))

        self.assertIn({"type": "done"}, events)
        self.assertEqual(1, len(completions.calls))
        request_timeout = completions.calls[0]["timeout"]
        self.assertGreater(request_timeout, 0)
        self.assertLessEqual(request_timeout, 10)

    def test_compaction_propagates_abort_before_summarizer(self):
        class FailingCompletions:
            def create(self, **_kwargs):
                raise AssertionError("summarizer should not start after abort")

        def abort():
            raise JobCanceled("compaction-stop")

        history = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "补充"},
            {"role": "assistant", "content": "结果"},
        ]
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FailingCompletions())
        )

        with self.assertRaises(JobCanceled):
            compact_history(history, client, "pfs-compaction-stop", abort_check=abort)

    def test_compaction_uses_remaining_timeout_for_summarizer(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(
                    usage=None,
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="压缩后的摘要")
                    )],
                )

        completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        history = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "补充"},
            {"role": "assistant", "content": "结果"},
        ]

        compacted, did_compact = compact_history(
            history,
            client,
            "pfs-compaction-timeout",
            request_timeout=lambda: 7.5,
        )

        self.assertTrue(did_compact)
        self.assertEqual("压缩后的摘要", compacted[0]["content"].split("\n\n", 1)[1].split("\n\n", 1)[0])
        self.assertEqual(1, len(completions.calls))
        self.assertEqual(7.5, completions.calls[0]["timeout"])

    def test_compaction_does_not_drop_timeout_for_legacy_client(self):
        class LegacyCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if "timeout" in kwargs:
                    raise TypeError("unexpected keyword argument 'timeout'")
                return SimpleNamespace(
                    usage=None,
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content="不应在无界请求中完成",
                    ))],
                )

        completions = LegacyCompletions()
        history = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "补充"},
            {"role": "assistant", "content": "结果"},
        ]
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        compacted, did_compact = compact_history(
            history,
            client,
            "pfs-compaction-legacy-client-test",
            request_timeout=3.0,
        )
        self.assertEqual(history, compacted)
        self.assertFalse(did_compact)
        self.assertEqual(1, len(completions.calls))
        self.assertIn("timeout", completions.calls[0])

    def test_background_job_timeout_is_a_durable_failure(self):
        class FakeRunner:
            def __init__(self):
                self.status = {
                    "id": "job-timeout-test",
                    "status": "running",
                    "error": "",
                }
                self.cancel_calls = 0
                self.failure = None

            def create(self, *_args, **_kwargs):
                return self.status["id"]

            def iter_events(self, _jid, timeout=None):
                self.timeout = timeout
                yield from ()

            def get_status(self, _jid):
                return dict(self.status)

            def timeout_tracked(self, _jid, error, *, error_code="", recovery_action=""):
                self.cancel_calls += 1
                self.failure = (error, error_code, recovery_action)
                self.status.update({
                    "status": "failed",
                    "error": error,
                    "error_code": error_code,
                    "recovery_action": recovery_action,
                })
                return True

        runner = FakeRunner()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace())),
            model="pfs-job-timeout-test",
            session_id="pfs-job-timeout-test",
            job_runner=runner,
            max_job_seconds=7,
        )
        iterator = agent._run_as_job(lambda _ctx: None, "analysis", "timeout")
        try:
            next(iterator)
        except StopIteration as stopped:
            result = stopped.value
        else:
            self.fail("_run_as_job should return after the child deadline")

        self.assertEqual(7, runner.timeout)
        self.assertEqual(1, runner.cancel_calls)
        self.assertEqual("failed", result["status"])
        self.assertEqual("job_timeout", result["error_code"])
        self.assertEqual("retry_with_smaller_scope", result["recovery_action"])
        self.assertEqual(
            (result["error"], "job_timeout", "retry_with_smaller_scope"),
            runner.failure,
        )

    def test_child_job_timeout_is_capped_by_parent_agent_deadline(self):
        class FakeRunner:
            def __init__(self):
                self.status = {
                    "id": "job-parent-deadline-test",
                    "status": "running",
                    "error": "",
                }
                self.timeout = None
                self.failure = None

            def create(self, *_args, **_kwargs):
                return self.status["id"]

            def iter_events(self, _jid, timeout=None, **_kwargs):
                self.timeout = timeout
                yield from ()

            def get_status(self, _jid):
                return dict(self.status)

            def timeout_tracked(self, _jid, error, *, error_code="", recovery_action=""):
                self.failure = (error, error_code, recovery_action)
                self.status.update({
                    "status": "failed",
                    "error": error,
                    "error_code": error_code,
                    "recovery_action": recovery_action,
                })
                return True

        runner = FakeRunner()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace())),
            model="pfs-parent-deadline-test",
            session_id="pfs-parent-deadline-test",
            job_runner=runner,
            max_job_seconds=30,
        )
        agent._run_deadline_ts = time.monotonic() + 0.2

        iterator = agent._run_as_job(lambda _ctx: None, "analysis", "deadline")
        with self.assertRaises(StopIteration) as stopped:
            next(iterator)

        result = stopped.exception.value
        self.assertIsNotNone(runner.timeout)
        self.assertLessEqual(runner.timeout, 0.2001)
        self.assertEqual("agent_run_timeout", result["error_code"])
        self.assertEqual("agent_run_timeout", runner.failure[1])

    def test_child_job_is_not_created_after_parent_deadline(self):
        class FakeRunner:
            def __init__(self):
                self.create_calls = 0

            def create(self, *_args, **_kwargs):
                self.create_calls += 1
                return "should-not-start"

        runner = FakeRunner()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace())),
            model="pfs-expired-parent-test",
            session_id="pfs-expired-parent-test",
            job_runner=runner,
        )
        agent._run_deadline_ts = time.monotonic() - 1

        with self.assertRaises(AgentRunTimeout):
            list(agent._run_as_job(lambda _ctx: None, "analysis", "expired"))
        self.assertEqual(0, runner.create_calls)

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

    def test_delegated_agent_checks_abort_before_provider_call(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                raise AssertionError("provider must not be called after cancellation")

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-delegated-cancel-test",
            session_id="pfs-delegated-cancel-test",
        )

        def abort():
            raise JobCanceled("delegate-stop")

        with self.assertRaises(JobCanceled):
            agent._run_delegated_llm(
                member={"role": "analyst", "instructions": ""},
                prompt="检查数据",
                abort_check=abort,
            )
        self.assertEqual(0, completions.calls)

    def test_delegated_agent_caps_provider_timeout_to_parent_deadline(self):
        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured["timeout"] = kwargs.get("timeout")
                return SimpleNamespace(
                    model="pfs-delegated-deadline-test",
                    usage=None,
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content="已完成委托分析", tool_calls=[],
                    ))],
                )

        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(
                completions=FakeCompletions(),
            )),
            model="pfs-delegated-deadline-test",
            session_id="pfs-delegated-deadline-test",
        )
        agent._run_deadline_ts = time.monotonic() + 0.2
        result = agent._run_delegated_llm(
            member={"role": "analyst", "instructions": ""},
            prompt="检查数据",
            timeout_seconds=30,
        )
        self.assertEqual("已完成委托分析", result["content"])
        self.assertGreater(captured["timeout"], 0)
        self.assertLessEqual(captured["timeout"], 0.2001)

    def test_delegated_agent_does_not_drop_timeout_for_legacy_client(self):
        class LegacyCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if "timeout" in kwargs:
                    raise TypeError("unexpected keyword argument 'timeout'")
                return SimpleNamespace(
                    model="pfs-legacy-client-test",
                    usage=None,
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content="不应在无界请求中完成", tool_calls=[],
                    ))],
                )

        completions = LegacyCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(
                completions=completions,
            )),
            model="pfs-legacy-client-test",
            session_id="pfs-legacy-client-test",
        )

        with self.assertRaisesRegex(TypeError, "timeout"):
            agent._run_delegated_llm(
                member={"role": "analyst", "instructions": ""},
                prompt="检查数据",
            )
        self.assertEqual(1, len(completions.calls))
        self.assertIn("timeout", completions.calls[0])

    def test_delegated_agent_raises_when_parent_deadline_already_expired(self):
        class FakeCompletions:
            def create(self, **_kwargs):
                raise AssertionError("expired delegation must not call provider")

        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(
                completions=FakeCompletions(),
            )),
            model="pfs-delegated-expired-test",
            session_id="pfs-delegated-expired-test",
        )
        agent._run_deadline_ts = time.monotonic() - 1
        with self.assertRaises(AgentRunTimeout):
            agent._run_delegated_llm(
                member={"role": "analyst", "instructions": ""},
                prompt="检查数据",
                timeout_seconds=30,
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
                "code": "run_tool_budget_exceeded",
                "recovery_action": "retry_with_smaller_scope",
            },
            events,
        )
        self.assertEqual({"type": "done"}, events[-1])

    def test_agent_hard_stops_repeated_logical_tool_errors_as_failure(self):
        fixture = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pfs_sales.csv"

        class FakeCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return [SimpleNamespace(
                    usage=None,
                    choices=[SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            content=None,
                            reasoning_content=None,
                            tool_calls=[SimpleNamespace(
                                index=0,
                                id=f"invalid-query-{self.calls}",
                                function=SimpleNamespace(
                                    name="query_data",
                                    arguments='{"sql":"SELECT missing FROM pfs_sales"}',
                                ),
                            )],
                        ),
                    )],
                )]

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-tool-error-budget-test",
            data_source=CSVDataSource(str(fixture), fixture.name),
            session_id="pfs-tool-error-budget-test",
            max_iterations=8,
            max_tool_calls=20,
        )

        events = list(agent.run("继续", history=[]))

        self.assertEqual(3, completions.calls)
        self.assertIn({
            "type": "error",
            "message": "连续工具调用失败，已终止。请检查数据源连接或简化查询。",
            "code": "agent_tool_errors_exhausted",
            "recovery_action": "check_tool_error_and_retry",
        }, events)
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
                "code": "run_token_budget_exceeded",
                "recovery_action": "retry_with_smaller_scope",
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
        retries = []

        def flaky_call(value):
            calls.append(value)
            if len(calls) < 3:
                raise RuntimeError("upstream returned 503")
            return "ok"

        with patch("agent.retry.time.sleep") as sleep:
            result = call_with_retry(
                flaky_call, "payload", max_retries=2, on_retry=retries.append,
            )

        self.assertEqual("ok", result)
        self.assertEqual(["payload", "payload", "payload"], calls)
        self.assertEqual([3.0, 6.0], [call.args[0] for call in sleep.call_args_list])
        self.assertEqual([1, 2], [item["attempt"] for item in retries])
        self.assertTrue(all(item["reason"] == "provider_unavailable" for item in retries))

    def test_abort_check_stops_retry_before_next_provider_attempt(self):
        calls = []
        checks = []

        def flaky_call():
            calls.append(True)
            raise RuntimeError("upstream returned 503")

        def abort_check():
            checks.append(True)
            if len(checks) >= 2:
                raise RuntimeError("job canceled")

        with patch("agent.retry.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "job canceled"):
                call_with_retry(
                    flaky_call,
                    max_retries=3,
                    abort_check=abort_check,
                )

        self.assertEqual(1, len(calls))
        sleep.assert_not_called()
        self.assertGreaterEqual(len(checks), 2)

    def test_agent_cancellation_check_unwinds_before_provider_request(self):
        class FakeCompletions:
            def __init__(self):
                self.calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                return []

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-cancel-before-request-test",
            session_id="pfs-cancel-before-request-test",
        )

        def cancel_check():
            raise JobCanceled("pfs-cancel-before-request-test")

        with self.assertRaises(JobCanceled):
            list(agent.run("不要发出请求", history=[], cancel_check=cancel_check))
        self.assertEqual(0, completions.calls)

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
            provider="kimi",
            api_key="test-key",
            base_url="https://example.invalid/v1",
            model="fallback-model",
            context_window=32_000,
            max_output_tokens=2_000,
            supports_prompt_cache=True,
            prompt_cache_mode="kimi",
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
            kwargs.pop("on_retry", None)
            return fn(*args, **kwargs)

        with patch(
            "LLM.llm_config_manager.get_llm_client_with_fallback",
            return_value=(fallback, "kimi", fallback_config),
        ), patch("agent.agent._call_with_retry", side_effect=invoke_without_sleep):
            events = list(agent.run("请返回一句话", history=[]))

        self.assertEqual(1, len(primary_completions.calls))
        self.assertEqual(1, len(fallback_completions.calls))
        self.assertEqual("fallback-model", fallback_completions.calls[0]["model"])
        self.assertNotIn("extra_body", fallback_completions.calls[0])
        self.assertTrue(
            fallback_completions.calls[0]["prompt_cache_key"].startswith("pfs-")
        )
        self.assertEqual("kimi", agent._provider)
        self.assertEqual("fallback-model", agent.model)
        self.assertIn(
            "当前模型暂时不可用，已切换备用模型，正在重试…",
            [event.get("message") for event in events if event.get("type") == "agent_activity"],
        )
        self.assertIn(
            {"type": "text", "content": "fallback answer"},
            events,
        )

    def test_agent_recovers_from_mid_stream_transport_failure_without_duplicate_text(self):
        def text_chunk(text, finish_reason=None):
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    finish_reason=finish_reason,
                    delta=SimpleNamespace(
                        content=text,
                        reasoning_content=None,
                        tool_calls=None,
                    ),
                )],
            )

        class InterruptedStream:
            def __iter__(self):
                yield SimpleNamespace(
                    usage=SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=2,
                        total_tokens=12,
                    ),
                    choices=[],
                )
                yield text_chunk("已")
                raise RuntimeError("connection reset by peer")

        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return InterruptedStream()
                return [
                    text_chunk("已"),
                    text_chunk("恢复", finish_reason="stop"),
                    SimpleNamespace(
                        usage=SimpleNamespace(
                            prompt_tokens=12,
                            completion_tokens=4,
                            total_tokens=16,
                        ),
                        choices=[],
                    ),
                ]

        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-stream-recovery-test",
            session_id="pfs-stream-recovery-test",
        )

        def invoke_without_sleep(fn, *args, **kwargs):
            kwargs.pop("on_retry", None)
            kwargs.pop("max_retries", None)
            return fn(*args, **kwargs)

        with patch("agent.agent._call_with_retry", side_effect=invoke_without_sleep):
            events = list(agent.run("生成一句摘要", history=[]))

        self.assertEqual(2, len(completions.calls))
        usage = next(event for event in events if event.get("type") == "usage")
        self.assertEqual(2, usage["model_calls"])
        self.assertEqual(22, usage["prompt_tokens"])
        self.assertEqual(6, usage["completion_tokens"])
        self.assertEqual(28, usage["total_tokens"])
        self.assertEqual(
            ["已", "恢复"],
            [event["content"] for event in events if event.get("type") == "text_delta"],
        )
        self.assertIn(
            "[STREAM RECOVERY]",
            completions.calls[1]["messages"][-1]["content"],
        )
        self.assertIn(
            {"type": "retry", "reason": "stream_interrupted", "attempt": 1,
             "max_retries": 2, "wait_seconds": 0, "error_type": "RuntimeError",
             "provider": "", "model": "pfs-stream-recovery-test"},
            events,
        )
        self.assertIn({"type": "text", "content": "已恢复"}, events)
        self.assertEqual({"type": "done"}, events[-1])

    def test_agent_switches_provider_after_stream_recovery_exhaustion(self):
        def text_chunk(text, finish_reason=None):
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    finish_reason=finish_reason,
                    delta=SimpleNamespace(
                        content=text,
                        reasoning_content=None,
                        tool_calls=None,
                    ),
                )],
            )

        class InterruptedStream:
            def __init__(self, *, usage=None):
                self.usage = usage

            def __iter__(self):
                if self.usage is not None:
                    yield SimpleNamespace(usage=self.usage, choices=[])
                yield text_chunk("已")
                raise RuntimeError("connection reset by peer")

        class FakeCompletions:
            def __init__(self, response_factory):
                self.calls = []
                self.response_factory = response_factory

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return self.response_factory(len(self.calls))

        primary_completions = FakeCompletions(
            lambda call_number: InterruptedStream(
                usage=(
                    SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=2,
                        total_tokens=12,
                    )
                    if call_number == 1
                    else None
                )
            )
        )
        fallback_completions = FakeCompletions(
            lambda _call_number: [
                SimpleNamespace(
                    usage=SimpleNamespace(
                        prompt_tokens=20,
                        completion_tokens=4,
                        total_tokens=24,
                    ),
                    choices=[],
                ),
                text_chunk("已完成", finish_reason="stop"),
            ]
        )
        primary = SimpleNamespace(
            chat=SimpleNamespace(completions=primary_completions)
        )
        fallback = SimpleNamespace(
            chat=SimpleNamespace(completions=fallback_completions)
        )
        fallback_config = LLMConfig(
            provider="kimi",
            api_key="test-key",
            base_url="https://example.invalid/v1",
            model="fallback-model",
            context_window=32_000,
            max_output_tokens=2_000,
            input_price_per_million=10,
            output_price_per_million=20,
        )
        agent = BusinessAgent(
            client=primary,
            model="primary-model",
            session_id="pfs-stream-fallback-test",
            provider="deepseek",
            input_price_per_million=1,
            output_price_per_million=2,
        )

        def invoke_without_sleep(fn, *args, **kwargs):
            kwargs.pop("on_retry", None)
            kwargs.pop("max_retries", None)
            return fn(*args, **kwargs)

        with patch(
            "LLM.llm_config_manager.get_llm_client_with_fallback",
            return_value=(fallback, "kimi", fallback_config),
        ), patch("agent.agent._call_with_retry", side_effect=invoke_without_sleep):
            events = list(agent.run("生成一句摘要", history=[]))

        self.assertEqual(3, len(primary_completions.calls))
        self.assertEqual(1, len(fallback_completions.calls))
        self.assertEqual("fallback-model", fallback_completions.calls[0]["model"])
        self.assertIn(
            "[STREAM RECOVERY]",
            fallback_completions.calls[0]["messages"][-1]["content"],
        )
        self.assertEqual(
            ["已", "完成"],
            [event["content"] for event in events if event.get("type") == "text_delta"],
        )
        usage = next(event for event in events if event.get("type") == "usage")
        self.assertEqual("kimi", usage["provider"])
        self.assertEqual("fallback-model", usage["model"])
        self.assertEqual(4, usage["model_calls"])
        self.assertEqual(30, usage["prompt_tokens"])
        self.assertEqual(6, usage["completion_tokens"])
        self.assertEqual(36, usage["total_tokens"])
        self.assertAlmostEqual(0.000294, usage["cost_usd"])
        self.assertIn(
            {
                "type": "retry",
                "provider": "kimi",
                "model": "fallback-model",
                "attempt": 3,
                "max_retries": 2,
                "wait_seconds": 0,
                "reason": "provider_switch",
                "error_type": "RuntimeError",
            },
            events,
        )
        self.assertIn(
            {
                "type": "agent_activity",
                "message": "当前模型流式连接中断，已切换备用模型，正在恢复当前分析…",
            },
            events,
        )
        self.assertEqual({"type": "done"}, events[-1])

    def test_agent_dispatches_complete_tool_call_when_provider_omits_tool_finish_reason(self):
        def tool_chunk(*, call_id=None, finish_reason=None):
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    finish_reason=finish_reason,
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[SimpleNamespace(
                            index=0,
                            id=call_id,
                            function=SimpleNamespace(name="get_schema", arguments="{}"),
                        )],
                    ),
                )],
            )

        def text_chunk(text):
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(
                    finish_reason="stop",
                    delta=SimpleNamespace(
                        content=text,
                        reasoning_content=None,
                        tool_calls=None,
                    ),
                )],
            )

        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return [tool_chunk(call_id="schema-call")]
                return [text_chunk("已读取数据结构")]

        source = CSVDataSource(
            str(Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pfs_sales.csv"),
            "pfs_sales.csv",
        )
        completions = FakeCompletions()
        agent = BusinessAgent(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model="pfs-multiturn-tool-test",
            data_source=source,
            session_id="pfs-multiturn-tool-test",
            max_iterations=3,
        )

        events = list(agent.run("读取数据结构并总结", history=[]))

        self.assertEqual(2, len(completions.calls))
        self.assertTrue(any(
            event.get("type") == "tool_start"
            and event.get("tool") == "get_schema"
            and event.get("display") == "读取数据结构"
            for event in events
        ))
        self.assertTrue(any(
            event.get("type") == "tool_audit" and event.get("tool") == "get_schema"
            for event in events
        ))
        second_messages = completions.calls[1]["messages"]
        self.assertEqual("assistant", second_messages[-2]["role"])
        self.assertEqual("tool", second_messages[-1]["role"])
        self.assertEqual("schema-call", second_messages[-1]["tool_call_id"])
        self.assertIn({"type": "text", "content": "已读取数据结构"}, events)
        self.assertEqual({"type": "done"}, events[-1])


if __name__ == "__main__":
    unittest.main()
