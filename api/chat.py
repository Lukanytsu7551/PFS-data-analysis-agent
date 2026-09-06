"""Blueprint: conversation (SSE streaming) and chart serving."""

import json
import logging
import math
import os
import re
import sys
import time
import uuid
from ast import literal_eval

from flask import Blueprint, current_app, g, request, Response, jsonify

from .state import (
    session_manager,
    config_manager,
    chart_store,
    check_session_ownership,
    require_session_ownership,
)
from agent.activation import ActivationContext, INTERNAL_ACTIONS
from agent.agent import AgentRunTimeout, BusinessAgent
from agent.commands import CommandLoader, CommandType
from agent.jobs import JobCanceled

try:
    from agent.memory import (
        maybe_schedule_consolidation as _maybe_schedule_memory_consolidation,
        schedule_extraction as _schedule_memory_extraction,
    )
except ImportError:
    _maybe_schedule_memory_consolidation = None  # type: ignore[assignment]
    _schedule_memory_extraction = None  # type: ignore[assignment]
from agent.prompts import get_system_prompt
from agent.reasoning import split_reasoning_tags
from agent.retry import call_with_retry as _call_with_retry
from agent.skills import SkillLoader
from infrastructure.artifact_lifecycle import register_artifact, update_artifact_run_usage
from config.product_identity import PRODUCT_SHORT_NAME
from infrastructure.compat import (
    cloud_login_enabled,
    optional_feature_enabled,
    request_user_id,
    workspace_hidden_dir,
)
from agent.pricing import validate_cost_limit
from agent.budget import BudgetConfigurationError, load_runtime_budget
from data.jobs_store import (
    CHAT_RECOVERY_CHECKPOINT_EVENT,
    CHAT_UNSAFE_RECOVERY_ACTION,
    CHAT_UNSAFE_RECOVERY_CODE,
    RESTART_RECOVERY_ACTION,
    RESTART_RECOVERY_CODE,
    RESTART_RECOVERY_ERROR,
)


def _pfs_deterministic_chat_response(sid: str, message: str, payload: dict, sess):
    """Return an SSE response for an explicitly requested PFS turn.

    This bridge is opt-in. It never guesses a data source and never lets a
    model choose SQL, permissions, or the calculation.
    """
    if payload.get("pfs_mode") != "deterministic":
        return None
    source_id = str(payload.get("source_id") or "").strip()
    if not source_id:
        return jsonify({"error": "确定性分析需要 source_id", "code": "pfs_source_required"}), 400
    try:
        from api.pfs import _session_tabular_source, _worksheet_from_payload
        from pfs_agent.query import parse_report_question
        from pfs_agent.reporting import analyze_file, load_tabular_snapshot

        path, _source = _session_tabular_source(sid, source_id)
        display_file_name = str(getattr(_source, "name", "") or path.name)
        worksheet = _worksheet_from_payload(payload)
        snapshot = load_tabular_snapshot(
            path,
            source_id=source_id,
            date_column="month",
            worksheet=worksheet,
        )
        parsed = parse_report_question(
            message,
            snapshot.columns,
            run_id=str(payload.get("run_id") or "pfs-chat-" + uuid.uuid4().hex[:16])[:120],
        )
        result = analyze_file(
            path,
            metric=parsed.metric,
            request=parsed.request,
            source_id=source_id,
            worksheet=worksheet,
            file_name=display_file_name,
        )
        # Keep the chat bridge aligned with the direct PFS endpoints while
        # retaining only the lightweight result trace.
        result_dict = result.to_dict()
    except (TypeError, ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "pfs_query_failed"}), 400

    def _sse(obj):
        from agent.events import serialize_event

        return "data: " + json.dumps(serialize_event(obj), ensure_ascii=False) + "\n\n"

    total = result_dict.get("total", 0)
    groups = result_dict.get("groups", [])
    lines = ["已按确定性口径完成数据分析：" + parsed.interpretation, "合计：" + str(total)]
    if groups:
        lines.append("分组结果：")
        lines.extend("- " + str(item.get("dimension")) + "：" + str(item.get("value")) for item in groups)
    lines.append("本次结果保留来源快照与轻量结论留痕，可在分析口径预览中回看。")
    answer = "\n".join(lines)
    sess.add_user(message)
    sess.add_assistant(answer)
    if not session_manager.persist(sid):
        log.warning("[chat] deterministic session state was not persisted sid=%s", sid)

    def generate():
        yield _sse({"type": "text", "content": answer})
        yield _sse(
            {
                "type": "pfs_result",
                "source_id": source_id,
                "interpretation": parsed.to_dict(),
                "result": result_dict,
            }
        )
        yield _sse({"type": "done"})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)


_CHAT_REPLAYABLE_EVENTS = frozenset(
    {
        "agent_activity",
        "ask_user",
        "chart_placeholder",
        "chart_ref",
        "context_estimate",
        "data_refs",
        "dashboard_outline",
        "error",
        "excel_outline",
        "feishu_sync",
        "hook_event",
        "knowledge_refs",
        "pfs_result",
        "ppt_outline",
        "reasoning",
        "report_outline",
        "retry",
        "skill_activated",
        "skill_matched",
        "team_event",
        "text",
        "text_delta",
        "tool_audit",
        "tool_end",
        "tool_start",
        "usage",
    }
)


def _chat_replay_projection(event: dict) -> dict:
    """Keep durable chat replay events safe and bounded.

    The live SSE response may contain server-only recovery context. Replay is
    session-scoped, but still uses the same minimal projection boundary as the
    browser response and avoids retaining unbounded provider/tool payloads.
    """
    projected = {
        key: value
        for key, value in dict(event or {}).items()
        if key not in {"recovery", "request_body", "messages"}
    }
    event_type = str(projected.get("type") or "")
    if event_type in {"text", "text_delta", "reasoning"}:
        content = str(projected.get("content") or "")
        if event_type in {"text", "text_delta"}:
            content, _embedded_reasoning = split_reasoning_tags(content)
            content = re.sub(
                r"<(?:analysis|reasoning)\b[^>]*>.*?(?:</(?:analysis|reasoning)>|$)",
                "",
                content,
                flags=re.DOTALL | re.IGNORECASE,
            )
        projected["content"] = content[:120_000]
    elif event_type == "tool_audit":
        projected["content"] = str(projected.get("content") or "")[:8_000]
        projected["summary"] = str(projected.get("summary") or "")[:2_000]
        args_preview = projected.get("args_preview")
        projected["args_preview"] = args_preview if isinstance(args_preview, dict) else {}
    elif event_type == "hook_event":
        projected["output"] = str(projected.get("output") or "")[:500]
    return projected


def _chat_events_from_records(records: list[dict]) -> list[dict]:
    """Translate internal JobStore records into public chat events."""
    events: list[dict] = []
    public_error_seen = False
    for record in records:
        event_type = str(record.get("type") or "")
        if event_type == "conversation_stream_event":
            event = dict(record.get("event") or {})
            if event.get("type") in _CHAT_REPLAYABLE_EVENTS:
                event["stream_sequence"] = int(record["sequence"])
                public_error_seen = public_error_seen or event.get("type") == "error"
                events.append(event)
        elif event_type == "job_error":
            error_event = _chat_job_error_event(record)
            if not public_error_seen:
                events.append({**error_event, "stream_sequence": int(record["sequence"])})
            events.append(
                {
                    "type": "done",
                    "stream_sequence": int(record["sequence"]),
                }
            )
        elif event_type == "job_canceled":
            events.append(
                {
                    "type": "stopped",
                    "stream_sequence": int(record["sequence"]),
                }
            )
            events.append(
                {
                    "type": "done",
                    "stream_sequence": int(record["sequence"]),
                }
            )
        elif event_type == "job_done":
            events.append(
                {
                    "type": "done",
                    "stream_sequence": int(record["sequence"]),
                }
            )
    return events


def _chat_job_error_event(record: dict) -> dict:
    """Expose a safe, actionable error for a durable conversation failure.

    A process restart closes the in-memory worker, so the request cannot be
    resumed safely. Keep that distinction explicit for the browser without
    retaining or replaying the original prompt, provider payload, or tool
    arguments. Older stores only have the stable error text; recognize those
    rows as well while the new columns roll out.
    """
    error_code = str(record.get("error_code") or "")
    is_restart = (
        error_code == RESTART_RECOVERY_CODE or str(record.get("error") or "") == RESTART_RECOVERY_ERROR
    )
    if error_code == CHAT_UNSAFE_RECOVERY_CODE:
        return {
            "type": "error",
            "message": (
                "Conversation stopped after a worker interruption during a "
                "non-idempotent step; it was not replayed. Review the last "
                "visible step and start a new request."
            ),
            "code": CHAT_UNSAFE_RECOVERY_CODE,
            "recovery_action": str(record.get("recovery_action") or CHAT_UNSAFE_RECOVERY_ACTION),
            "automatic_replay": False,
        }
    if is_restart:
        return {
            "type": "error",
            "message": "Conversation interrupted by an application restart; it was not replayed.",
            "code": RESTART_RECOVERY_CODE,
            "recovery_action": str(record.get("recovery_action") or RESTART_RECOVERY_ACTION),
            "automatic_replay": False,
        }
    return {
        "type": "error",
        "message": str(record.get("error") or "Conversation failed"),
        "code": error_code or "conversation_job_failed",
    }


_CHAT_RESUME_SCHEMA_VERSION = 1
_RESUME_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|credential|password|"
    r"private[_-]?key|secret|(?:^|[_-])token(?:$|[_-]))",
    flags=re.IGNORECASE,
)


def _resume_safe_json(value, *, depth: int = 0):
    """Turn a request/state object into bounded JSON without credentials."""
    if depth > 8:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:200_000]
    if isinstance(value, dict):
        result = {}
        for raw_key, raw_value in list(value.items())[:200]:
            key = str(raw_key)[:120]
            if key.startswith("_") or _RESUME_SECRET_KEY.search(key):
                continue
            safe = _resume_safe_json(raw_value, depth=depth + 1)
            if safe is not None:
                result[key] = safe
        return result
    if isinstance(value, (list, tuple)):
        return [
            safe
            for item in list(value)[:200]
            if (safe := _resume_safe_json(item, depth=depth + 1)) is not None
        ]
    return str(value)[:200_000]


def _chat_source_resume_descriptors(sess) -> list[dict]:
    """Describe only sources that a fresh local process can safely rebuild."""
    descriptors: list[dict] = []
    active_ids = {str(item.get("id") or "") for item in sess.list_sources() if item.get("active")}
    for entry in getattr(sess, "_sources", []) or []:
        source = entry.get("source") if isinstance(entry, dict) else None
        source_id = str(entry.get("id") or "") if isinstance(entry, dict) else ""
        if source is None:
            continue
        item = {
            "source_id": source_id,
            "name": str(getattr(source, "name", "未命名") or "未命名")[:240],
            "active": source_id in active_ids,
            "type": type(source).__name__,
        }
        file_path = str(getattr(source, "file_path", "") or "").strip()
        if file_path:
            item["kind"] = "file"
            item["file_path"] = file_path[:2000]
        elif getattr(source, "_db_path", None):
            item["kind"] = "workspace_persistent"
            item["db_path"] = str(source._db_path)[:2000]
        else:
            # Do not serialize SQL/API connection strings or auth material.
            item["kind"] = "reconnect_required"
        descriptors.append(item)
    return descriptors[:50]


def _build_chat_resume_payload(sess, body: dict, message: str, workspace_id: str) -> dict:
    """Build the restart/resume envelope stored beside a conversation Job."""
    safe_body = _resume_safe_json(dict(body)) or {}
    safe_body.pop("_resume_job_id", None)
    safe_body.pop("_durable_worker_resume", None)
    state = _resume_safe_json(sess.capture_rewind_state()) or {}
    return {
        "schema_version": _CHAT_RESUME_SCHEMA_VERSION,
        "session_id": str(sess.session_id),
        "message": str(message)[:200_000],
        "body": safe_body,
        "user_id": str(getattr(sess, "owner_user_id", "") or "")[:200],
        "model_provider": str(getattr(sess, "model_provider", "") or "")[:200],
        "workspace_id": str(workspace_id or "")[:200],
        "sources": _chat_source_resume_descriptors(sess),
        "pre_turn_state": state,
    }


def _unwrap_chat_resume_payload(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("resume_request")
    if not isinstance(candidate, dict):
        candidate = payload
    if candidate.get("schema_version") != _CHAT_RESUME_SCHEMA_VERSION:
        return None
    if not isinstance(candidate.get("body"), dict):
        return None
    if not isinstance(candidate.get("pre_turn_state"), dict):
        return None
    return candidate


def _restore_chat_resume_payload(sid: str, sess, payload: dict) -> None:
    """Restore the durable part of a conversation before a resumed turn."""
    if str(payload.get("session_id") or "") != str(sid):
        raise ValueError("会话身份与续跑任务不一致，已拒绝续跑。")
    sess.restore_rewind_state(dict(payload.get("pre_turn_state") or {}))
    provider = str(payload.get("model_provider") or "").strip()
    if provider:
        sess.model_provider = provider

    from data.workspace import workspace_manager

    workspace_id = str(payload.get("workspace_id") or "").strip()
    runtime = workspace_manager.get(sid)
    if workspace_id and (runtime is None or str(runtime.workspace_id) != workspace_id):
        runtime = workspace_manager.ensure_for_job(sid, workspace_id)
    if workspace_id and runtime is None:
        raise ValueError("原工作目录已不可用，请重新挂载工作目录后再试。")
    if runtime is not None:
        sess.workspace_id = runtime.workspace_id
        try:
            from api.workspace import _register_workdir_files

            _register_workdir_files(sid, runtime)
        except Exception as exc:
            raise ValueError(f"原工作目录数据恢复失败：{exc}") from exc

    # A same-process queue worker already owns the live session sources.  A
    # fresh process has none and rebuilds file sources from stable paths.
    if sess.list_sources():
        return
    descriptors = payload.get("sources") or []
    unsupported_active = []
    restored_ids: list[str] = []
    try:
        from api.saved_sessions import _restore_ds

        for descriptor in descriptors:
            if not isinstance(descriptor, dict) or not descriptor.get("active"):
                continue
            if descriptor.get("kind") != "file":
                if descriptor.get("kind") == "workspace_persistent" and runtime is not None:
                    continue
                unsupported_active.append(str(descriptor.get("name") or "数据源"))
                continue
            restored = _restore_ds(
                {
                    "file_path": descriptor.get("file_path", ""),
                    "display_name": descriptor.get("name", ""),
                    "ds_type": descriptor.get("type", ""),
                }
            )
            if restored is None:
                unsupported_active.append(str(descriptor.get("name") or "文件数据源"))
                continue
            restored_ids.append(sess.add_source(restored))
    except Exception as exc:
        raise ValueError(f"原文件数据源恢复失败：{exc}") from exc
    if unsupported_active and not restored_ids and not runtime:
        names = "、".join(unsupported_active[:5])
        raise ValueError(f"数据源「{names}」需要重新连接，无法无损续跑。")


def _chat_job_event_response(
    runner,
    job_id: str,
    *,
    after_sequence: int = 0,
) -> Response:
    """Return an SSE relay for a Job that may execute in another process."""

    def _sse(event: dict) -> str:
        from agent.events import serialize_event

        return "data: " + json.dumps(serialize_event(event), ensure_ascii=False) + "\n\n"

    def stream():
        yield _sse({"type": "agent_activity", "message": "", "job_id": job_id})
        public_error_seen = False
        try:
            for record in runner.iter_events(job_id, after_sequence=after_sequence):
                event_type = str(record.get("type") or "")
                if event_type == "conversation_stream_event":
                    event = dict(record.get("event") or {})
                    if event.get("type") not in _CHAT_REPLAYABLE_EVENTS:
                        continue
                    event["stream_sequence"] = int(record["sequence"])
                    public_error_seen = public_error_seen or event.get("type") == "error"
                    yield _sse(event)
                elif event_type == "job_error":
                    error_event = _chat_job_error_event(record)
                    if not public_error_seen:
                        yield _sse(
                            {
                                **error_event,
                                "stream_sequence": int(record["sequence"]),
                            }
                        )
                    yield _sse({"type": "done", "stream_sequence": int(record["sequence"])})
                elif event_type == "job_canceled":
                    yield _sse({"type": "stopped", "stream_sequence": int(record["sequence"])})
                    yield _sse({"type": "done", "stream_sequence": int(record["sequence"])})
                elif event_type == "job_done":
                    yield _sse({"type": "done", "stream_sequence": int(record["sequence"])})
        except GeneratorExit:
            return

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "X-PFS-Conversation-Job": job_id,
        },
    )


def _enqueue_durable_chat_turn(
    sid: str,
    sess,
    body: dict,
    message: str,
    *,
    resume_job_id: str = "",
) -> Response:
    """Hand a complete chat turn to a reconstructable queue handler."""
    runner = sess.job_runner
    # A resume request must not replay the old restart error.  Capture the
    # cursor before queue hand-off because a same-process worker may claim the
    # task immediately and append its first events before enqueue returns.
    after_sequence = runner.last_sequence
    if resume_job_id:
        job_id = str(resume_job_id)
        job = runner.get_status(job_id)
        request_payload = _unwrap_chat_resume_payload(runner.get_request_payload(job_id))
        if job is None or job.get("type") != "conversation_analysis" or request_payload is None:
            raise ValueError("续跑任务不存在，或缺少可恢复的请求快照。")
    else:
        job_id = runner.begin_tracked(
            "conversation_analysis",
            label=message[:96],
        )
        job = runner.get_status(job_id) or {}
        request_payload = _build_chat_resume_payload(
            sess,
            body,
            message,
            str(job.get("workspace_id") or ""),
        )
    try:
        runner.enqueue_durable(
            job_id,
            "chat_turn",
            {"resume_request": request_payload},
            operation_key=(
                f"chat:{job_id}:resume:{uuid.uuid4().hex[:12]}" if resume_job_id else f"chat:{job_id}"
            ),
        )
    except Exception:
        runner.fail_tracked(job_id, "Durable chat queue submission failed.")
        raise
    return _chat_job_event_response(
        runner,
        job_id,
        after_sequence=after_sequence,
    )


_PROMPT_SUGGESTION_DIRECTIVE = """You are a prompt suggestion engine.
Predict the single next message this user is most likely to type after reading the assistant's latest answer.
Return only the message text that should be prefilled in the chat input.
Do not explain, do not quote, do not use markdown, and do not mention that this is a suggestion.
Keep it short, concrete, and directly actionable."""


def format_feishu_ask_user(event: dict) -> str:
    """Render a Web-only choice card as a replyable Feishu text message."""
    question = str(event.get("question") or "请告诉我下一步希望继续处理什么。 ").strip()
    raw_options = event.get("options")
    if isinstance(raw_options, str):
        try:
            raw_options = json.loads(raw_options)
        except (TypeError, ValueError):
            try:
                raw_options = literal_eval(raw_options)
            except (ValueError, SyntaxError):
                raw_options = []
    options = [str(item).strip() for item in (raw_options or []) if str(item).strip()]
    if not options:
        return f"{question}\n\n请直接回复你的选择或补充具体需求。"
    numbered = "\n".join(f"{index}. {option}" for index, option in enumerate(options, start=1))
    return f"{question}\n\n请直接回复序号或选项文字：\n{numbered}"


def _visible_history_for_prompt_suggestion(
    history: list,
    max_messages: int = 80,
    max_chars: int = 9000,
    max_message_chars: int = 100_000,
) -> list[dict]:
    visible: list[dict] = []
    total = 0
    for msg in reversed(history or []):
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content or msg.get("tool_calls"):
            continue
        content = re.sub(r"\s+", " ", content)[:max_message_chars]
        total += len(content)
        visible.append({"role": role, "content": content})
        if len(visible) >= max_messages or total >= max_chars:
            break
    return list(reversed(visible))


def _sanitize_prompt_suggestion(text: str, max_len: int = 220) -> str:
    suggestion = str(text or "").strip()
    if not suggestion:
        return ""
    if re.search(r"(?is)<think(?:ing)?\b", suggestion) and not re.search(
        r"(?is)</think(?:ing)?\s*>", suggestion
    ):
        return ""
    suggestion = re.sub(r"(?is)<think\b[^>]*>.*?</think\s*>", "", suggestion)
    suggestion = re.sub(r"(?is)<thinking\b[^>]*>.*?</thinking\s*>", "", suggestion)
    suggestion = re.sub(r"(?is)</?think(?:ing)?\b[^>]*>", "", suggestion)
    suggestion = re.sub(r"^```(?:\w+)?\s*", "", suggestion)
    suggestion = re.sub(r"\s*```$", "", suggestion)
    suggestion = suggestion.strip().strip("\"'“”‘’`")
    suggestion = re.sub(
        r"^(?:建议|用户下一步|下一条消息|next message|suggestion|user)\s*[:：]\s*",
        "",
        suggestion,
        flags=re.IGNORECASE,
    ).strip()
    suggestion = re.sub(r"^```(?:\w+)?\s*", "", suggestion)
    suggestion = re.sub(r"\s*```$", "", suggestion)
    suggestion = re.sub(r"^\s*[-*•]\s*", "", suggestion).strip()
    suggestion = re.sub(r"[ \t]+", " ", suggestion)
    suggestion = re.sub(r"\n{3,}", "\n\n", suggestion)
    if len(suggestion) > max_len:
        suggestion = suggestion[:max_len].rstrip("，,。.!！?？;；:：、 ")
    return suggestion


def _prompt_content_text(content) -> str:
    """Normalise OpenAI-compatible string and multipart final content."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            if key in content:
                return _prompt_content_text(content[key])
        return ""
    if isinstance(content, list):
        return "\n".join(part for part in (_prompt_content_text(item) for item in content) if part)
    return str(getattr(content, "text", "") or "")


def _final_prompt_suggestion_content(message) -> str:
    """Keep final answer, discard inline/separate provider reasoning fields."""
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    text = _prompt_content_text(content).strip()
    visible, _reasoning = split_reasoning_tags(text)
    # Other reasoning models use <analysis> / <reasoning>, including an
    # unclosed tag when output stops at the token budget.
    visible = re.sub(
        r"<(?:analysis|reasoning)\b[^>]*>.*?(?:</(?:analysis|reasoning)>|$)",
        "",
        visible,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return visible.strip()


def _prompt_suggestion_token_budget(config) -> int:
    """80% of the selected model's configured maximum completion length."""
    from LLM.llm_config_manager import auxiliary_token_limits

    _context_window, max_output_tokens = auxiliary_token_limits(config)
    return max_output_tokens


def _build_prompt_suggestion_messages(
    history: list,
    lang: str = "zh",
    *,
    max_context_tokens: int | None = None,
) -> list[dict]:
    # The history is text, so convert the model's token allowance with the
    # same conservative estimator used by the agent executor.
    max_chars = 9000 if not max_context_tokens else max(9000, int(max_context_tokens * 3.5))
    visible = _visible_history_for_prompt_suggestion(history, max_chars=max_chars)
    if len(visible) < 2:
        return []
    language_hint = (
        "Prefer Chinese unless the recent conversation is clearly in another language."
        if lang != "en"
        else "Prefer English unless the recent conversation is clearly in another language."
    )
    return [
        {"role": "system", "content": get_system_prompt()},
        *visible,
        {
            "role": "system",
            "content": f"{_PROMPT_SUGGESTION_DIRECTIVE}\n{language_hint}",
        },
    ]


def _fallback_prompt_suggestion(lang: str = "zh") -> str:
    return ""


def _build_team_context(
    sid: str, workspace_id: str = "", *, max_teams: int = 6, max_messages: int = 3
) -> str:
    try:
        from agent.tools.workspace.teams import WorkspaceTeamStore

        store = WorkspaceTeamStore(sid, workspace_id=workspace_id)
        teams = store.list()
    except Exception as exc:
        log.debug("[teams] context unavailable sid=%s workspace=%s error=%s", sid, workspace_id, exc)
        return ""
    if not teams:
        return ""

    lines = [
        "Existing workspace analyst teams are available. Use team_status when details may be stale, "
        "send_message to queue work for members, and team_delegate for parallel bounded member turns.",
        "Delegated members have limited read-only tools for schema, data queries, knowledge search, "
        "and workspace file reading. They cannot mutate data or create nested teams.",
        "For a fresh user request, do not synthesize old member results as if they were new work. "
        "If prior team messages are from an earlier turn or may be stale, create/recreate the team "
        "or call team_delegate again for the required members before producing the final answer.",
        "Never invent a Dynamic Plan ID or describe a plan as created unless team_plan_create returned it. "
        "When the user asks to create or preview a plan without executing it, call team_plan_create before replying.",
    ]
    for team in teams[:max_teams]:
        name = str(team.get("name") or "")
        if not name:
            continue
        try:
            status = store.status(name)
        except Exception:
            status = team
        description = str(status.get("description") or "").strip()
        lines.append(
            f"- Team {name}: {len(status.get('members') or [])} members; "
            f"lead_unread={status.get('lead_unread_messages', 0)}; "
            f"description={description[:160] or 'none'}"
        )
        members = []
        for member in (status.get("members") or [])[:10]:
            last_message = str(member.get("last_message") or "").replace("\n", " ")[:90]
            members.append(
                f"{member.get('name', '')}"
                f"(role={member.get('role', '') or 'analyst'}, "
                f"status={member.get('status', 'idle')}, "
                f"unread={member.get('unread_messages', 0)}"
                f"{', last=' + last_message if last_message else ''})"
            )
        if members:
            lines.append("  Members: " + "; ".join(members))
        recent = status.get("recent_messages") or []
        for message in recent[-max_messages:]:
            body = str(message.get("message") or "").replace("\n", " ")[:140]
            if body:
                lines.append(
                    f"  Message {message.get('sender', '?')} -> {message.get('recipient', '?')}: {body}"
                )
    try:
        from agent.teams.dynamic_plans import DynamicTeamPlanStore

        dynamic_plans = DynamicTeamPlanStore(sid, workspace_id=workspace_id).list()
        for plan in dynamic_plans[:8]:
            if plan.get("status") == "needs_review":
                task_ids = ", ".join(str(task.get("id")) for task in plan.get("tasks") or [])
                lines.append(
                    "  Dynamic Plan "
                    + str(plan.get("id") or "")
                    + " requires revision: "
                    + str(plan.get("review_summary") or "quality review blocked it")
                    + ". Select affected ids from ["
                    + task_ids
                    + "] and call team_delegate with team_name, review_plan_id and review_task_ids; downstream tasks are included automatically."
                )
            if plan.get("status") == "planned":
                lines.append(
                    "  Dynamic Plan "
                    + str(plan.get("id") or "")
                    + " is planned but not running. When the user confirms execution, call team_delegate with team_name and plan_id only."
                )
            failed = [task for task in plan.get("tasks") or [] if task.get("status") == "failed"]
            if failed:
                lines.append(
                    "  Dynamic Plan "
                    f"{plan['id']} has retryable failed tasks: "
                    + ", ".join(str(task.get("id")) for task in failed)
                    + ". To retry, call team_delegate with team_name, "
                    "retry_plan_id and retry_task_ids; do not supply new prompts."
                )
    except Exception as exc:
        log.debug("[teams] dynamic plan context unavailable sid=%s error=%s", sid, exc)
    return "\n".join(lines)[:5000]


class ActivationRequestError(ValueError):
    def __init__(self, message: str, code: str = "invalid_activation") -> None:
        super().__init__(message)
        self.code = code


def _resolve_activation(sess, payload: dict):
    """Resolve untrusted request names into server-owned typed definitions."""
    skill_name = str(payload.get("skill") or "").strip()
    command_name = str(payload.get("command") or "").strip().lstrip("/").lower()
    internal_action = str(payload.get("internal_action") or "").strip().lower()
    # Fallback: if the LLM auto-loaded a skill via load_analysis_skill in a
    # previous turn (e.g. dashboard), keep it active for subsequent turns
    # so guard/nudge logic works (e.g. after ask_user user-reply).
    if not skill_name and not command_name and not internal_action:
        auto = getattr(sess, "auto_loaded_skill", "") or ""
        if auto:
            skill_name = auto

    # Compatibility window for S3: current confirmation cards still send
    # internal actions through `command`. S4 will emit `internal_action`.
    if command_name in INTERNAL_ACTIONS and not internal_action:
        internal_action, command_name = command_name, ""
    try:
        activation = ActivationContext(
            skill_name=skill_name,
            command_name=command_name,
            internal_action=internal_action,
        )
    except ValueError as exc:
        raise ActivationRequestError(
            "skill、command 和 internal_action 不能同时使用。",
            "activation_conflict",
        ) from exc

    if internal_action and internal_action not in INTERNAL_ACTIONS:
        raise ActivationRequestError("未知的内部操作。", "unknown_internal_action")

    from data.workspace import workspace_manager

    runtime = workspace_manager.get(sess.session_id)
    workspace_root = runtime.workdir if runtime else None
    skill_def = None
    command_def = None
    if skill_name:
        loader = SkillLoader(
            workspace_dir=(workspace_hidden_dir(workspace_root, ".pfs") / "skills")
            if workspace_root
            else None,
        )
        skill_def = loader.load_all().get(skill_name)
        if skill_def is None:
            from agent.workflows.skills import get_session_workflow_skill

            skill_def = get_session_workflow_skill(sess.session_id, skill_name)
        if skill_def is None:
            raise ActivationRequestError(
                f"未知技能：{skill_name}",
                "unknown_skill",
            )
    elif command_name:
        loader = CommandLoader(
            workspace_dir=(workspace_hidden_dir(workspace_root, ".pfs") / "commands")
            if workspace_root
            else None,
        )
        command_def = loader.load().get(command_name)
        if command_def is None:
            raise ActivationRequestError(
                f"未知斜杠命令：/{command_name}",
                "unknown_command",
            )
        if command_def.type is not CommandType.PROMPT:
            raise ActivationRequestError(
                f"/{command_def.name} 是 {command_def.type.value} 命令，不能提交给 Agent。",
                "command_not_agent_routable",
            )
        activation = ActivationContext(command_name=command_def.name)
    return activation, skill_def, command_def


def _resolve_data_context(sess, raw) -> dict | None:
    """Validate preview-selected remote SQL tables against active sources."""
    if not isinstance(raw, dict):
        return None
    requested = raw.get("tables")
    if not isinstance(requested, list):
        requested = [raw] if raw.get("table") else []
    requested = requested[:20]
    if not requested or not hasattr(sess, "_active_entries"):
        return None

    active = sess._active_entries()
    from data.sources.sql import SQLDataSource

    active_by_id = {
        entry.get("id"): (idx, entry.get("source"))
        for idx, entry in enumerate(active, start=1)
        if isinstance(entry.get("source"), SQLDataSource)
    }
    source_tables = {}
    all_names = []
    for source_id, (_, src) in active_by_id.items():
        try:
            source_tables[source_id] = src.list_catalog_tables()
            if not source_tables[source_id]:
                source_tables[source_id] = src.list_tables()
        except Exception:
            # Compatibility for lightweight/legacy SQL source implementations.
            try:
                source_tables[source_id] = src.list_tables()
            except Exception:
                source_tables[source_id] = []
        try:
            all_names.extend(source_tables[source_id])
        except Exception:
            pass
    collision = len(all_names) != len(set(all_names))

    valid_source_ids = {
        str(item.get("source_id") or "")
        for item in requested
        if isinstance(item, dict)
        and str(item.get("table") or "").strip() in source_tables.get(str(item.get("source_id") or ""), [])
    }
    cross_source = len(valid_source_ids) > 1

    resolved = []
    seen = set()
    for item in requested:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        table = str(item.get("table") or "").strip()
        if (source_id, table) in seen or source_id not in active_by_id:
            continue
        idx, src = active_by_id[source_id]
        if table not in source_tables.get(source_id, []):
            continue
        seen.add((source_id, table))
        resolved.append(
            {
                "source_id": source_id,
                "source_name": getattr(src, "name", "未命名"),
                "table": table,
                "query_table": f"src{idx}__{table}" if (collision or cross_source) else table,
            }
        )
    return {"tables": resolved} if resolved else None


def _apply_sql_analysis_context(sess, data_context: dict | None) -> list[dict]:
    """Persist validated SQL scope and return active SQL sources still unscoped."""
    from data.sources.sql import SQLDataSource

    selected_by_source: dict[str, list[str]] = {}
    for item in (data_context or {}).get("tables", []):
        selected_by_source.setdefault(item["source_id"], []).append(item["table"])

    missing = []
    changed = False
    for entry in sess._active_entries() if hasattr(sess, "_active_entries") else []:
        src = entry.get("source")
        if not isinstance(src, SQLDataSource):
            continue
        if entry["id"] in selected_by_source:
            src.set_analysis_tables(selected_by_source[entry["id"]])
            changed = True
        if not src.get_analysis_tables():
            missing.append({"source_id": entry["id"], "source_name": getattr(src, "name", "SQL 数据库")})
    if changed:
        sess._combined_schema_cache = None
        if hasattr(sess, "_invalidate_merged_source"):
            sess._invalidate_merged_source()
    return missing


def _build_agent(
    sess,
    *,
    workspace_id: str | None = None,
    source_snapshot=None,
    hook_engine=None,
    hook_context=None,
    user_id: str = "",
) -> BusinessAgent:
    provider = sess.model_provider or config_manager.get_default_provider()
    if not provider:
        error = ValueError("未配置任何 LLM 模型，请先在「模型设置」中添加模型。")
        error.code = "model_not_configured"
        raise error
    from LLM.llm_config_manager import get_llm_client

    client = get_llm_client(provider)
    cfg = config_manager.get_config(provider)
    try:
        max_cost_usd = validate_cost_limit(os.environ.get("PFS_MAX_COST_USD"))
    except ValueError as exc:
        raise ValueError(f"PFS_MAX_COST_USD 配置无效：{exc}") from exc
    try:
        runtime_budget = load_runtime_budget()
    except BudgetConfigurationError as exc:
        raise ValueError(f"Agent 运行预算配置无效：{exc}") from exc
    # Use cached schema when available; recompute only after data source changes
    # (cache is invalidated by add_source / remove_source / toggle_source / data_source setter).
    if source_snapshot is not None:
        combined_schema = source_snapshot.combined_schema
    elif hasattr(sess, "get_combined_schema"):
        if not getattr(sess, "_combined_schema_cache", None):
            sess._combined_schema_cache = sess.get_combined_schema()
        combined_schema = sess._combined_schema_cache
    else:
        combined_schema = None
    active_sources = (
        list(source_snapshot.sources)
        if source_snapshot is not None
        else ([e["source"] for e in sess._active_entries()] if hasattr(sess, "_active_entries") else [])
    )
    if not active_sources and sess.data_source:
        active_sources = [sess.data_source]

    # 若有数据源但 schema 仍为空，尝试实时获取一次。
    # 若获取后仍为空（SQL 数据源连接断开、文件丢失等），报错提示用户重新连接，
    # 而不是让 LLM 无声地空转后输出空回复。
    if active_sources and not combined_schema:
        if source_snapshot is None:
            try:
                combined_schema = sess.get_combined_schema()
                sess._combined_schema_cache = combined_schema
            except Exception as exc:
                log.warning("[chat] schema fetch failed  sid=%s  error=%s", sess.session_id, exc)
                combined_schema = None
        if not combined_schema:
            src_names = "、".join(getattr(s, "name", "未知数据源") for s in active_sources)
            raise ValueError(
                f"数据源「{src_names}」的连接已断开（可能由服务重启引起），请在侧边栏重新连接数据源后再试。"
            )

    # Build (or reuse cached) MergedDataSource when ≥2 sources are active.
    # This enables cross-source JOIN / UNION in the agent.
    merged_source = (
        source_snapshot.merged_source
        if source_snapshot is not None
        else (sess.get_merged_source() if hasattr(sess, "get_merged_source") else None)
    )

    src_names = [getattr(s, "name", "?") for s in active_sources]
    log.debug(
        "[chat] build_agent  provider=%s  model=%s  active_sources=%s  merged=%s",
        provider,
        cfg.model,
        src_names,
        merged_source is not None,
    )

    provider_defaults = config_manager.DEFAULT_CONFIGS.get(provider, {})
    supports_prompt_cache = getattr(cfg, "supports_prompt_cache", None)
    if supports_prompt_cache is None:
        supports_prompt_cache = provider_defaults.get("supports_prompt_cache", False)
    prompt_cache_mode = getattr(cfg, "prompt_cache_mode", None)
    if not prompt_cache_mode:
        prompt_cache_mode = provider_defaults.get("prompt_cache_mode", "none")

    return BusinessAgent(
        client=client,
        model=cfg.model,
        data_source=(source_snapshot.primary if source_snapshot is not None else sess.data_source),
        combined_schema=combined_schema,
        all_sources=active_sources,
        merged_source=merged_source,
        enable_thinking=cfg.enable_thinking,
        thinking_budget=cfg.thinking_budget,
        chart_store=chart_store,
        session_chart_ids=list(getattr(sess, "chart_ids", [])),
        color_scheme=getattr(sess, "ppt_color_scheme", "mckinsey"),
        session_id=sess.session_id,
        workspace_id=workspace_id,
        user_id=user_id,
        job_runner=sess.job_runner,
        context_window=getattr(cfg, "context_window", None),
        max_output_tokens=getattr(cfg, "max_output_tokens", None),
        max_iterations=runtime_budget.max_iterations,
        max_tool_calls=runtime_budget.max_tool_calls,
        max_total_tokens=runtime_budget.max_total_tokens,
        max_run_seconds=runtime_budget.max_run_seconds,
        max_job_seconds=runtime_budget.max_job_seconds,
        input_price_per_million=getattr(cfg, "input_price_per_million", None),
        output_price_per_million=getattr(cfg, "output_price_per_million", None),
        max_cost_usd=max_cost_usd,
        hook_engine=hook_engine,
        hook_context=hook_context,
        compaction_state=getattr(sess, "compaction_state", None),
        provider=provider,
        usage_recorder=sess.record_usage,
        mcp_discovery_recorder=sess.record_discovered_mcp_tools,
        analysis_delete_operation_store=sess,
        supports_prompt_cache=supports_prompt_cache,
        prompt_cache_mode=prompt_cache_mode,
        prompt_cache_retention=(
            getattr(cfg, "prompt_cache_retention", None)
            or provider_defaults.get("prompt_cache_retention", "in_memory")
        ),
        cache_breakpoint_strategy=(
            getattr(cfg, "cache_breakpoint_strategy", None)
            or provider_defaults.get("cache_breakpoint_strategy", "stable_prefix")
        ),
    )


# ── Session lifecycle ──────────────────────────────────────────────────────


@bp.post("/api/session/new")
def new_session():
    owner_user_id = ""
    if cloud_login_enabled():
        from .auth import current_user

        auth_user = current_user()
        if auth_user:
            owner_user_id = auth_user["id"]
    sess = session_manager.create(owner_user_id=owner_user_id)
    if optional_feature_enabled("HOOKS"):
        try:
            from agent.hooks.models import HookContext
            from data.hooks_store import load_engine

            load_engine().run_hooks(
                "session_start",
                HookContext(event_name="session_start", session_id=sess.session_id),
            )
        except Exception as exc:
            log.debug("[hooks] session_start skipped sid=%s error=%s", sess.session_id, exc)
    # Governance: trigger the 24h consolidation check on every new session.
    # workspace_id is not yet known here, so only user-level records are in
    # scope; workspace-level consolidation continues to fire at turn-end.
    if _maybe_schedule_memory_consolidation is not None and bool(
        (request.get_json(silent=True) or {}).get("memory_enabled", True)
    ):
        try:
            _start_user_id = owner_user_id or request_user_id(
                request.headers,
                request.get_json(silent=True) or {},
                default="local-default",
            )
            _maybe_schedule_memory_consolidation(
                provider=config_manager.get_default_provider() or "",
                session_id=sess.session_id,
                user_id=_start_user_id,
                workspace_id="",
            )
        except Exception as exc:
            log.debug("[memory] session_start consolidation skipped sid=%s: %s", sess.session_id, exc)
    log.info("[session] created  sid=%s", sess.session_id)
    return jsonify({"session_id": sess.session_id})


@bp.get("/api/session/<sid>/ping")
def ping_session(sid: str):
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if not sess:
        log.debug("[session] ping  sid=%s  alive=False", sid)
        return jsonify({"alive": False}), 404
    from api.saved_sessions import _visible_msg_count

    cnt = _visible_msg_count(sess.history)
    log.debug("[session] ping  sid=%s  alive=True  msg_count=%d", sid, cnt)
    return jsonify({"alive": True, "msg_count": cnt})


@bp.get("/api/session/<sid>/load-current")
def load_current_session(sid: str):
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if not sess:
        log.warning("[session] load-current  sid=%s  not found", sid)
        return jsonify({"error": "session not found"}), 404
    from api.saved_sessions import _visible_msg_count

    cnt = _visible_msg_count(sess.history)
    log.info("[session] load-current  sid=%s  msg_count=%d", sid, cnt)
    return jsonify(
        {
            "history": sess.history,
            "total_input": sess.total_input_tokens,
            "total_output": sess.total_output_tokens,
            "total_cached_input": getattr(sess, "total_cached_input_tokens", 0),
            "total_cache_write": getattr(sess, "total_cache_write_tokens", 0),
            "usage_breakdowns": list(getattr(sess, "usage_breakdowns", []))[-100:],
            "msg_count": cnt,
        }
    )


@bp.get("/api/session/<sid>/token-metrics")
def get_token_metrics(sid: str):
    """Return bounded per-call Token diagnostics without prompt contents."""
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    breakdowns = list(getattr(sess, "usage_breakdowns", []) or [])[-100:]
    actual_prompt = sum(
        int(item.get("actual_prompt_tokens") or 0) for item in breakdowns if isinstance(item, dict)
    )
    estimated_prompt = sum(
        int(item.get("payload_tokens_est") or 0) for item in breakdowns if isinstance(item, dict)
    )
    estimation_error_pct = (
        round(abs(estimated_prompt - actual_prompt) / actual_prompt * 100, 2) if actual_prompt else None
    )
    total_input = int(getattr(sess, "total_input_tokens", 0) or 0)
    total_cached = int(getattr(sess, "total_cached_input_tokens", 0) or 0)
    return jsonify(
        {
            "ok": True,
            "calls_retained": len(breakdowns),
            "retention_limit": 100,
            "total_input_tokens": total_input,
            "total_output_tokens": int(getattr(sess, "total_output_tokens", 0) or 0),
            "total_cached_input_tokens": total_cached,
            "total_cache_write_tokens": int(getattr(sess, "total_cache_write_tokens", 0) or 0),
            "cache_hit_ratio": (round(total_cached / total_input, 4) if total_input else 0.0),
            "estimated_prompt_tokens_retained": estimated_prompt,
            "actual_prompt_tokens_retained": actual_prompt,
            "estimation_error_pct": estimation_error_pct,
            "breakdowns": breakdowns,
        }
    )


@bp.post("/api/session/<sid>/clear")
def clear_history(sid: str):
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get_or_create(sid)
    old_count = len(sess.history)
    sess.clear_history()
    log.info("[session] clear  sid=%s  cleared=%d entries", sid, old_count)
    return jsonify({"ok": True})


# ── Chart serving ──────────────────────────────────────────────────────────


@bp.get("/api/chart/<chart_id>")
def serve_chart(chart_id: str):
    html = chart_store.get(chart_id)
    if not html:
        log.warning("[chart] not found  chart_id=%s", chart_id)
        return "Chart not found", 404
    return Response(html, mimetype="text/html")


# ── Stop ───────────────────────────────────────────────────────────────────


@bp.post("/api/session/<sid>/stop")
def stop_session(sid: str):
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if sess:
        active_conversations = [
            job
            for job in sess.job_runner.list_jobs(
                active_only=True,
                limit=50,
                top_level_only=True,
            )
            if job.get("type") == "conversation_analysis"
        ]
        sess.cancel_requested = bool(active_conversations)
        for job in active_conversations:
            # Stop is an explicit user action. It must cancel the durable
            # background turn even when the browser has already lost its SSE
            # connection.
            sess.job_runner.cancel(job["id"])
        if optional_feature_enabled("HOOKS"):
            try:
                from agent.hooks.models import HookContext
                from data.hooks_store import load_engine

                load_engine().run_hooks(
                    "stop",
                    HookContext(event_name="stop", session_id=sid, message="stop requested"),
                )
            except Exception as exc:
                log.debug("[hooks] stop skipped sid=%s error=%s", sid, exc)
        log.info(
            "[session] stop requested  sid=%s  jobs=%s",
            sid,
            [job["id"] for job in active_conversations],
        )
    return jsonify({"ok": True})


# ── Prompt suggestion ───────────────────────────────────────────────────────


@bp.post("/api/session/<sid>/prompt-suggestion")
def prompt_suggestion(sid: str):
    log.debug("[prompt-suggestion] called sid=%s", sid)
    allowed, _uid = check_session_ownership(sid)
    if not allowed:
        return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
    sess = session_manager.get(sid)
    if not sess:
        log.warning("[prompt-suggestion] session not found sid=%s", sid)
        return jsonify({"ok": False, "suggestion": ""}), 404

    provider = sess.model_provider or config_manager.get_default_provider()
    cfg = config_manager.get_config(provider) if provider else None
    if not provider or cfg is None:
        log.warning("[prompt-suggestion] no provider/config sid=%s provider=%s", sid, provider)
        return jsonify({"ok": False, "suggestion": ""})

    from LLM.llm_config_manager import auxiliary_token_limits

    context_budget, output_budget = auxiliary_token_limits(cfg)
    lang = str((request.json or {}).get("lang") or "zh").lower()
    messages = _build_prompt_suggestion_messages(
        sess.history,
        lang=lang,
        max_context_tokens=context_budget,
    )
    if not messages:
        log.info("[prompt-suggestion] history too short sid=%s history_len=%d", sid, len(sess.history or []))
        return jsonify({"ok": False, "suggestion": ""})

    log.debug(
        "[prompt-suggestion] calling LLM sid=%s provider=%s model=%s msg_count=%d",
        sid,
        provider,
        cfg.model,
        len(messages),
    )
    try:
        from LLM.llm_config_manager import get_llm_client

        client = get_llm_client(provider)
        budget = _prompt_suggestion_token_budget(cfg)
        response = _call_with_retry(
            client.chat.completions.create,
            model=cfg.model,
            messages=messages,
            temperature=0.25,
            max_tokens=budget,
        )
        message = response.choices[0].message if response.choices else None
        raw = _final_prompt_suggestion_content(message) if message is not None else ""
        log.debug(
            "[prompt-suggestion] response sid=%s context_budget=%d output_budget=%d chars=%d has_final=%s",
            sid,
            context_budget,
            output_budget,
            len(raw),
            bool(raw),
        )
        suggestion = _sanitize_prompt_suggestion(raw)
        log.debug(
            "[prompt-suggestion] sanitized sid=%s suggestion=%r", sid, suggestion[:200] if suggestion else ""
        )
    except Exception as exc:
        log.warning("[prompt-suggestion] LLM call failed sid=%s error=%s", sid, exc)
        return jsonify({"ok": False, "suggestion": ""})

    log.info(
        "[prompt-suggestion] result sid=%s ok=%s suggestion=%r",
        sid,
        bool(suggestion),
        suggestion[:100] if suggestion else "",
    )
    return jsonify({"ok": bool(suggestion), "suggestion": suggestion})


# ── Chat SSE replay ────────────────────────────────────────────────────────


@bp.get("/api/session/<sid>/chat/<jid>/events")
@require_session_ownership
def replay_chat_events(sid: str, jid: str):
    """Replay public chat events for one tracked conversation job.

    The cursor is the session-wide JobStore sequence, not a per-response
    counter.  Non-chat lifecycle records are consumed internally and omitted
    from the response, so a reconnect can advance over them without applying
    the same UI event twice.
    """
    sess = session_manager.get_or_create(sid)
    job = sess.job_runner.get_status(jid)
    if job is None or job.get("type") != "conversation_analysis":
        return jsonify({"error": "conversation job not found", "id": jid}), 404
    try:
        after_sequence = max(0, int(request.args.get("after_sequence", "0")))
        limit = max(1, min(int(request.args.get("limit", "500")), 1000))
    except (TypeError, ValueError):
        return jsonify({"error": "after_sequence and limit must be integers"}), 400

    stored = sess.job_runner.list_events(
        after_sequence=after_sequence,
        limit=limit,
        job_id=jid,
    )
    events = _chat_events_from_records(stored)

    latest_sequence = sess.job_runner.last_sequence
    oldest_sequence = sess.job_runner.oldest_sequence
    next_sequence = stored[-1]["sequence"] if stored else after_sequence
    return jsonify(
        {
            "events": events,
            "next_sequence": next_sequence,
            "has_more": len(stored) >= limit,
            "latest_sequence": latest_sequence,
            "oldest_sequence": oldest_sequence,
            "status": job.get("status"),
            "replay_truncated": after_sequence < max(0, oldest_sequence - 1),
        }
    )


# ── Chat SSE ───────────────────────────────────────────────────────────────


@bp.post("/api/session/<sid>/chat/<jid>/resume")
@require_session_ownership
def resume_chat(sid: str, jid: str):
    """Explicitly continue a restart-recovered conversation Job once."""
    sess = session_manager.get_or_create(sid)
    runner = sess.job_runner
    job = runner.get_status(jid)
    request_payload = _unwrap_chat_resume_payload(runner.get_request_payload(jid))
    if job is None or job.get("type") != "conversation_analysis" or request_payload is None:
        return jsonify(
            {
                "error": "该对话没有可用的恢复快照。",
                "code": "chat_resume_unavailable",
            }
        ), 409
    body = dict(request_payload.get("body") or {})
    body["message"] = str(request_payload.get("message") or body.get("message") or "")
    body["_resume_job_id"] = str(jid)
    if request_payload.get("user_id") and not body.get("user_id"):
        body["user_id"] = request_payload["user_id"]
    if body.get("pfs_mode") == "deterministic":
        return jsonify(
            {
                "error": "确定性分析请从分析入口重新执行，不能重复提交原请求。",
                "code": "chat_resume_not_supported",
            }
        ), 409
    if job.get("status") == "failed":
        if not runner.reopen_for_resume(jid):
            return jsonify(
                {
                    "error": "该任务不是可续跑的重启中断任务。",
                    "code": "chat_resume_unavailable",
                }
            ), 409
    elif job.get("status") in {"succeeded", "canceled"}:
        return jsonify(
            {
                "error": "该对话任务已经结束。",
                "code": "chat_resume_terminal",
            }
        ), 409

    # Re-enter the normal chat path with the stored body. Copy the outer auth
    # session into the short-lived request context so cloud mode retains the
    # original user identity without persisting a cookie or credential.
    from flask import session as flask_session

    auth_state = dict(flask_session)
    resume_after_sequence = runner.last_sequence
    body["_chat_event_after_sequence"] = resume_after_sequence
    with current_app.test_request_context(
        f"/api/session/{sid}/chat",
        method="POST",
        json=body,
        headers={"X-PFS-Resume": "1"},
    ):
        flask_session.update(auth_state)
        return chat_stream(sid)


@bp.post("/api/session/<sid>/chat")
def chat_stream(sid: str):
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return jsonify(
            {
                "error": "请求正文必须是 JSON 对象",
                "code": "invalid_request_body",
            }
        ), 400
    raw_message = d.get("message")
    if raw_message is not None and not isinstance(raw_message, str):
        return jsonify(
            {
                "error": "消息必须是字符串",
                "code": "invalid_message_type",
            }
        ), 400
    message = (raw_message or "").strip()
    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    sess = session_manager.get_or_create(sid)
    resume_job_id = str(d.get("_resume_job_id") or "").strip()
    durable_worker_resume = bool(d.get("_durable_worker_resume"))
    try:
        chat_event_after_sequence = max(0, int(d.get("_chat_event_after_sequence") or 0))
    except (TypeError, ValueError):
        chat_event_after_sequence = 0
    is_internal_feishu = bool(getattr(g, "feishu_inbound", False))
    user_id = request_user_id(request.headers, d, default="local-default")
    # Cloud mode: use authenticated user and enforce token quota
    if cloud_login_enabled():
        from .auth import current_user
        from data.auth_store import check_quota

        auth_user = current_user()
        if not auth_user and not is_internal_feishu:
            return jsonify({"error": "请先登录", "needs_auth": True}), 401
        user_id = (
            str(getattr(sess, "owner_user_id", "") or "feishu-bot") if is_internal_feishu else auth_user["id"]
        )
        # Ownership check: refuse access when another user owns this session
        owner = getattr(sess, "owner_user_id", "")
        if owner and owner != user_id:
            log.warning(
                "[security] session ownership mismatch sid=%s owner=%s requester=%s", sid, owner, user_id
            )
            return jsonify({"error": "无权访问此会话", "code": "forbidden"}), 403
        if not owner:
            sess.owner_user_id = user_id
        quota = check_quota(user_id)
        if quota["exceeded"]:
            return jsonify(
                {
                    "error": "今日免费额度已用尽（{} tokens）。您可以在设置中配置自己的 API Key 继续使用，"
                    "或明天再试。已用 {}/{}".format(quota["limit"], quota["used"], quota["limit"]),
                    "code": "quota_exceeded",
                    "quota": quota,
                }
            ), 403
    pfs_response = _pfs_deterministic_chat_response(sid, message, d, sess)
    if pfs_response is not None:
        return pfs_response

    # A durable queue worker calls this same entry point with a reconstructed
    # request. The public request path only enqueues a JSON envelope and never
    # captures process-local snapshots or Flask objects before hand-off.
    if sess.job_runner.durable_queue_enabled and not durable_worker_resume and not is_internal_feishu:
        try:
            return _enqueue_durable_chat_turn(
                sid,
                sess,
                d,
                message,
                resume_job_id=resume_job_id,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "chat_queue_unavailable"}), 409
        except Exception as exc:
            log.exception("[chat] durable queue submission failed sid=%s", sid)
            return jsonify(
                {
                    "error": f"无法提交可恢复对话：{exc}",
                    "code": "chat_queue_submission_failed",
                }
            ), 500

    resume_payload = None
    if resume_job_id:
        resume_payload = _unwrap_chat_resume_payload(sess.job_runner.get_request_payload(resume_job_id))
        resume_job = sess.job_runner.get_status(resume_job_id)
        if resume_job is None or resume_job.get("type") != "conversation_analysis" or resume_payload is None:
            return jsonify(
                {
                    "error": "续跑任务不存在，或缺少可恢复的请求快照。",
                    "code": "chat_resume_unavailable",
                }
            ), 409
        message = str(resume_payload.get("message") or message).strip()
        try:
            _restore_chat_resume_payload(sid, sess, resume_payload)
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "chat_resume_unavailable"}), 409
    try:
        activation, active_skill, active_command = _resolve_activation(sess, d)
    except ActivationRequestError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    if activation.kind == "none":
        added_tools = sess.record_discovered_tools(message)
        if added_tools:
            log.info("[tools] discovered sid=%s tools=%s", sid, added_tools)
    sess.cancel_requested = False
    _command_usage_before = (
        sess.total_input_tokens,
        sess.total_output_tokens,
        sess.total_cached_input_tokens,
    )
    data_context = _resolve_data_context(sess, d.get("data_context"))
    missing_sql_scope = _apply_sql_analysis_context(sess, data_context)
    if missing_sql_scope:
        return jsonify(
            {
                "error": "请先在数据预览中为 SQL 数据库选择一张或多张分析表。",
                "code": "sql_table_selection_required",
                "sources": missing_sql_scope,
            }
        ), 400
    from filehistory import FileHistoryError, for_session as file_history_for_session

    file_history = file_history_for_session(sid)
    file_history_snapshot_id = ""
    if file_history is not None:
        try:
            snapshot = file_history.begin_snapshot(message, sess.capture_rewind_state())
            file_history_snapshot_id = str(snapshot.get("id") or "")
        except FileHistoryError as exc:
            return jsonify({"error": str(exc), "code": "file_history_unavailable"}), 500
    runner = sess.job_runner
    recovery_state: dict = {}
    if resume_job_id:
        conversation_job_id = resume_job_id
        conversation_job = runner.get_status(conversation_job_id) or {}
        if conversation_job.get("status") in {"succeeded", "failed", "canceled"}:
            return jsonify(
                {
                    "error": "该续跑任务已结束，请发起新的对话。",
                    "code": "chat_resume_terminal",
                }
            ), 409
    else:
        conversation_job_id = runner.begin_tracked(
            "conversation_analysis",
            label=message[:96],
        )
    conversation_job = sess.job_runner.get_status(conversation_job_id) or {}
    if resume_job_id:
        recovery_state = runner.get_last_recovery_checkpoint(conversation_job_id)
    recovery_prefix = (
        str(recovery_state.get("partial_content") or "")[:120_000]
        if str(recovery_state.get("phase") or "") == "model_call" and bool(recovery_state.get("replay_safe"))
        else ""
    )
    fixed_workspace_id = str(conversation_job.get("workspace_id") or "")
    from data.workspace import workspace_manager

    fixed_workspace_runtime = (
        workspace_manager.get_by_workspace(fixed_workspace_id) if fixed_workspace_id else None
    )
    try:
        source_snapshot = sess.acquire_data_source_snapshot()
    except Exception as exc:
        sess.job_runner.fail_tracked(
            conversation_job_id,
            f"Data source snapshot failed: {exc}",
        )
        if file_history is not None and file_history_snapshot_id:
            try:
                file_history.finalize_snapshot(file_history_snapshot_id, "failed")
            except FileHistoryError:
                log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
        return jsonify({"error": f"无法固定当前数据源：{exc}"}), 500
    sess.record_activation(activation, message, conversation_job_id)
    sess.job_runner.append_tracked_event(
        conversation_job_id,
        {
            "type": "conversation_activation",
            "job_id": conversation_job_id,
            "activation": activation.to_record(),
        },
    )

    from api.saved_sessions import _visible_msg_count

    _turn_start = time.monotonic()
    log.info(
        "[chat] turn start  sid=%s  activation=%s:%r  history=%d msgs  msg=%.80r",
        sid,
        activation.kind,
        activation.name or "(none)",
        _visible_msg_count(sess.history),
        message,
    )

    if not resume_job_id:
        request_payload = _build_chat_resume_payload(
            sess,
            d,
            message,
            fixed_workspace_id,
        )
        try:
            runner.persist_request_payload(
                conversation_job_id,
                request_payload,
                durable_handler=("chat_turn" if runner.durable_queue_enabled else ""),
            )
        except (TypeError, ValueError) as exc:
            runner.fail_tracked(
                conversation_job_id,
                f"Conversation recovery snapshot failed: {exc}",
            )
            return jsonify(
                {
                    "error": "无法保存本次对话的恢复快照。",
                    "code": "chat_resume_snapshot_failed",
                }
            ), 500

    def _persist_stream_event(event: dict) -> dict:
        """Persist one browser-safe event before it is sent or discarded."""
        if not isinstance(event, dict):
            return event
        if event.get("stream_sequence"):
            return event
        event_type = str(event.get("type") or "")
        if event_type not in _CHAT_REPLAYABLE_EVENTS:
            return event
        persisted = runner.append_tracked_event(
            conversation_job_id,
            {
                "type": "conversation_stream_event",
                "event": _chat_replay_projection(event),
            },
        )
        if persisted is None:
            return event
        return {
            **event,
            "stream_sequence": int(persisted["sequence"]),
        }

    def _persist_recovery_checkpoint(checkpoint: dict) -> bool:
        """Persist bounded, server-only state before a replay boundary."""
        if not isinstance(checkpoint, dict):
            return False
        phase = str(checkpoint.get("phase") or "unknown")[:80]
        allowed_phases = {
            "preflight",
            "model_call",
            "tool_call",
            "tool_complete",
            "workflow_create",
            "finalizing",
        }
        if phase not in allowed_phases:
            return False
        payload = {
            "phase": phase,
            "replay_safe": bool(checkpoint.get("replay_safe")),
            "run_id": str(checkpoint.get("run_id") or "")[:160],
        }
        try:
            payload["iteration"] = max(0, int(checkpoint.get("iteration") or 0))
        except (TypeError, ValueError):
            payload["iteration"] = 0
        if "provider" in checkpoint:
            payload["provider"] = str(checkpoint.get("provider") or "")[:160]
        if "model" in checkpoint:
            payload["model"] = str(checkpoint.get("model") or "")[:160]
        if "partial_content" in checkpoint:
            payload["partial_content"] = str(checkpoint.get("partial_content") or "")[:120_000]
        if "tool_names" in checkpoint:
            payload["tool_names"] = [
                str(item or "")[:120] for item in list(checkpoint.get("tool_names") or ())[:16]
            ]
        if "tool_calls" in checkpoint:
            payload["tool_calls"] = [
                {
                    "id": str(item.get("id") or "")[:160],
                    "name": str(item.get("name") or "")[:160],
                    "arguments": str(item.get("arguments") or "{}")[:24_000],
                }
                for item in list(checkpoint.get("tool_calls") or ())[:16]
                if isinstance(item, dict)
            ]
        if "tool_results" in checkpoint:
            payload["tool_results"] = [
                {
                    "id": str(item.get("id") or "")[:160],
                    "name": str(item.get("name") or "")[:160],
                    "content": str(item.get("content") or "")[:24_000],
                }
                for item in list(checkpoint.get("tool_results") or ())[:16]
                if isinstance(item, dict)
            ]
        if "pending_tool_count" in checkpoint:
            try:
                payload["pending_tool_count"] = max(0, int(checkpoint.get("pending_tool_count") or 0))
            except (TypeError, ValueError):
                payload["pending_tool_count"] = 0
        persisted = runner.append_tracked_event(
            conversation_job_id,
            {"type": CHAT_RECOVERY_CHECKPOINT_EVENT, **payload},
        )
        return persisted is not None

    def _sse(obj, *, persist: bool = True) -> str:
        from agent.events import serialize_event

        public_event = _persist_stream_event(obj) if persist else obj
        return f"data: {json.dumps(serialize_event(public_event), ensure_ascii=False)}\n\n"

    def generate(job_context=None):
        stream_closed = False

        command_metric_recorded = False

        def _record_prompt_command_metric(outcome: str, error_code: str = "") -> None:
            nonlocal command_metric_recorded
            if command_metric_recorded or active_command is None:
                return
            command_metric_recorded = True
            sess.record_command_metric(
                command=active_command.name,
                command_type=active_command.type.value,
                outcome=outcome,
                duration_ms=int((time.monotonic() - _turn_start) * 1000),
                error_code=error_code,
                input_tokens=max(0, sess.total_input_tokens - _command_usage_before[0]),
                output_tokens=max(0, sess.total_output_tokens - _command_usage_before[1]),
                cached_input_tokens=max(
                    0,
                    sess.total_cached_input_tokens - _command_usage_before[2],
                ),
            )

        hook_engine = None
        hook_context = None
        if optional_feature_enabled("HOOKS"):
            try:
                from agent.hooks.models import HookContext
                from data.hooks_store import load_engine

                candidate_hook_engine = load_engine()
                # The Hooks surface is available by default, but an empty or
                # globally disabled configuration has no side effects. Keep
                # the engine absent in that case so safe chat recovery can
                # still resume a persisted model prefix exactly once.
                if (
                    candidate_hook_engine.enabled
                    and any(
                        bool(getattr(hook, "enabled", True))
                        for hook in candidate_hook_engine.hooks
                    )
                ):
                    hook_engine = candidate_hook_engine
                if hook_engine is not None:
                    hook_context = HookContext(
                        event_name="turn_start",
                        session_id=sid,
                        turn_id=conversation_job_id,
                        workspace_id=fixed_workspace_id,
                        workspace_name=(
                            fixed_workspace_runtime.to_dict().get("name", "")
                            if fixed_workspace_runtime is not None
                            else ""
                        ),
                        workspace_path=(
                            fixed_workspace_runtime.to_dict().get("path", "")
                            if fixed_workspace_runtime is not None
                            else ""
                        ),
                        message=message,
                        model_provider=sess.model_provider or config_manager.get_default_provider() or "",
                    )
            except Exception as exc:
                log.warning("[hooks] disabled for turn sid=%s error=%s", sid, exc)

        try:
            agent = _build_agent(
                sess,
                workspace_id=fixed_workspace_id,
                source_snapshot=source_snapshot,
                hook_engine=hook_engine,
                hook_context=hook_context,
                user_id=user_id,
            )
        except ValueError as exc:
            log.error("[chat] build_agent failed  sid=%s  error=%s", sid, exc)
            runner.fail_tracked(conversation_job_id, str(exc))
            if file_history is not None and file_history_snapshot_id:
                try:
                    file_history.finalize_snapshot(file_history_snapshot_id, "failed")
                except FileHistoryError:
                    log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
            source_snapshot.release()
            error_code = str(getattr(exc, "code", "agent_build_failed"))
            _record_prompt_command_metric("error", error_code)
            yield _sse({"type": "error", "message": str(exc), "code": error_code})
            yield _sse({"type": "done"})
            return

        # A configured HookEngine may execute arbitrary side effects around
        # this turn. Do not seed the final answer with a prefix from a
        # previously safe run if Hooks became active before the retry; that
        # would duplicate the already-visible prefix while the Agent correctly
        # fails closed.
        effective_recovery_prefix = recovery_prefix if hook_engine is None else ""
        collected: list[str] = [effective_recovery_prefix] if effective_recovery_prefix else []
        collected_reasoning: list[str] = []
        turn_chart_ids: list[str] = []
        completed_normally = False
        tool_calls_in_turn: list[str] = []
        step_count = 0
        pending_steps: dict[str, list[dict]] = {}
        artifact_signatures: set[str] = set()
        stream_error = ""
        stream_error_code = ""
        stream_recovery_action = ""
        run_usage = {
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "priced_cost_usd": 0.0,
            "price_unknown": False,
            "providers": set(),
            "models": set(),
        }

        def _artifact_run_cost(status: str = "running") -> dict:
            calls = int(run_usage["model_calls"] or 0)
            price_unknown = bool(run_usage["price_unknown"])
            if calls == 0:
                source = "provider_usage_unavailable"
                amount = None
                estimated = None
            elif price_unknown:
                source = "provider_usage_price_unknown"
                amount = None
                estimated = None
            else:
                source = "provider_usage_configured_pricing"
                amount = round(float(run_usage["priced_cost_usd"] or 0.0), 8)
                estimated = True
            return {
                "amount": amount,
                "currency": "USD",
                "estimated": estimated,
                "source": source,
                "model_calls": calls,
                "input_tokens": int(run_usage["input_tokens"] or 0),
                "output_tokens": int(run_usage["output_tokens"] or 0),
                "cached_input_tokens": int(run_usage["cached_input_tokens"] or 0),
                "providers": sorted(run_usage["providers"]),
                "models": sorted(run_usage["models"]),
                "status": status,
                # This amount is derived from provider usage and configured
                # rates. It is never presented as a reconciled provider bill.
                "billing_verified": False,
            }

        agent._artifact_metadata = {
            **dict(getattr(agent, "_artifact_metadata", None) or {}),
            "run_id": conversation_job_id,
            "cost": _artifact_run_cost(),
        }

        def _append_parent_artifact(artifact: dict) -> None:
            if not isinstance(artifact, dict) or not artifact:
                return
            signature = json.dumps(artifact, ensure_ascii=False, sort_keys=True, default=str)
            if signature in artifact_signatures:
                return
            artifact_signatures.add(signature)
            runner.append_tracked_event(
                conversation_job_id,
                {
                    "type": "artifact_created",
                    "job_id": conversation_job_id,
                    "artifact": artifact,
                },
            )

        def _collect_downloads(content: str) -> None:
            for name, url in re.findall(r"\[([^\]]+)\]\((/api/output/[^)]+)\)", content or ""):
                _append_parent_artifact(
                    {
                        "type": "file",
                        "name": name.replace("📥", "").strip(),
                        "url": url,
                    }
                )

        def _append_tool_result_summary(event: dict) -> None:
            tool = str(event.get("tool") or "").strip()
            if not tool:
                return
            if event.get("artifacts"):
                return
            result_tools = {
                "query_data",
                "create_analysis_table",
                "delete_analysis_tables",
                "run_analysis",
                "profile_data",
                "clean_data",
                "export_excel",
                "export_report",
                "generate_ppt",
                "generate_dashboard",
                "workspace_read_file",
                "workspace_write_file",
                "workspace_edit_file",
                "workspace_delete_file",
                "workspace_move_file",
                "workspace_bash",
                "workspace_command",
                "task_create",
                "task_update",
                "team_create",
                "team_delete",
                "team_list",
                "team_status",
                "send_message",
                "agent_delegate",
                "workflow_create",
                "workflow_list",
                "workflow_start",
                "workflow_status",
            }
            if tool not in result_tools:
                return
            content = str(event.get("content") or "").strip()
            summary = str(event.get("summary") or "").strip()
            error = str(event.get("error") or "").strip()
            label = {
                "query_data": "query_data 查询结果",
                "create_analysis_table": "创建分析表结果",
                "delete_analysis_tables": "删除分析表结果",
                "run_analysis": "分析计算结果",
                "profile_data": "数据概况结果",
                "clean_data": "数据清洗结果",
                "export_excel": "Excel 导出结果",
                "export_report": "分析文档生成结果",
                "generate_ppt": "PPT 生成结果",
                "generate_dashboard": "看板生成结果",
            }.get(tool, f"{tool} 结果")
            if error:
                label = f"{label}（失败）"
            _append_parent_artifact(
                {
                    "type": "tool_result_summary",
                    "tool": tool,
                    "name": label,
                    "summary": summary or content[:500],
                    "ok": bool(event.get("ok", True)),
                }
            )

        def _start_step(event: dict) -> None:
            nonlocal step_count
            step_count += 1
            tool = str(event.get("tool") or "unknown")
            step = {
                "step_id": f"step-{step_count}",
                "tool": tool,
                "display": event.get("display") or tool,
                "started_monotonic": time.monotonic(),
            }
            pending_steps.setdefault(tool, []).append(step)
            runner.append_tracked_event(
                conversation_job_id,
                {
                    "type": "conversation_step_started",
                    "job_id": conversation_job_id,
                    "step_id": step["step_id"],
                    "tool": tool,
                    "display": step["display"],
                    "step_number": step_count,
                },
            )
            runner.update_tracked(
                conversation_job_id,
                min(95, step_count * 4),
                f"已执行 {step_count} 个步骤",
            )

        def _finish_step(tool: str, ok: bool = True, error: str = "", elapsed=None) -> None:
            queue = pending_steps.get(tool) or []
            if not queue:
                return
            step = queue.pop(0)
            duration = elapsed
            if duration is None:
                duration = time.monotonic() - step["started_monotonic"]
            runner.append_tracked_event(
                conversation_job_id,
                {
                    "type": "conversation_step_finished",
                    "job_id": conversation_job_id,
                    "step_id": step["step_id"],
                    "tool": tool,
                    "display": step["display"],
                    "step_number": int(step["step_id"].split("-")[-1]),
                    "status": "succeeded" if ok else "failed",
                    "elapsed_seconds": round(float(duration or 0), 3),
                    "error": error or "",
                },
            )

        # Every data-backed conversation exposes the same readable schema
        # snapshot without forcing the model to call get_schema each turn.
        schema_text = str(source_snapshot.combined_schema or "")
        if schema_text:
            from agent.tools.results import persist_large_tool_result

            _preview, schema_artifact, _budget = persist_large_tool_result(
                sid,
                "get_schema",
                schema_text,
                runtime=fixed_workspace_runtime,
                threshold=1,
                deduplicate=True,
            )
            if schema_artifact:
                schema_artifact["name"] = "get_schema 数据结构"
                sess.record_tool_audit({"recovery": {}, "artifacts": [schema_artifact]})
                _append_parent_artifact(schema_artifact)

        ppt_title = d.get("ppt_title", "")
        ppt_slides = d.get("ppt_slides") or []
        excel_tables = d.get("excel_tables") or []
        excel_filename = d.get("excel_filename", "")
        report_title = d.get("report_title", "")
        report_sections = d.get("report_sections") or []
        dashboard_name = d.get("dashboard_name", "")
        dashboard_widgets = d.get("dashboard_widgets") or []

        # Per-session temporary instruction — only injected when enabled.
        active_temp_prompt = (
            getattr(sess, "temp_prompt", "") if getattr(sess, "temp_prompt_enabled", False) else ""
        )
        if hook_engine and hook_context:
            for notification in hook_engine.run_hooks("user_prompt_submit", hook_context):
                yield _sse(notification.to_event())
            for notification in hook_engine.run_hooks("turn_start", hook_context):
                yield _sse(notification.to_event())
            hook_prompts = hook_engine.drain_prompt_messages()
            if hook_prompts:
                hook_prompt_text = "[Hook Prompt]\n" + "\n\n".join(hook_prompts)
                active_temp_prompt = (
                    f"{active_temp_prompt}\n\n{hook_prompt_text}" if active_temp_prompt else hook_prompt_text
                )
        workspace_status = (
            {"mounted": True, **fixed_workspace_runtime.to_dict()}
            if fixed_workspace_runtime is not None
            else {"mounted": False}
        )
        recovery_context = sess.build_recovery_context(workspace_status)
        teams_enabled = bool(d.get("teams_enabled"))
        auto_match_skill = d.get("auto_match_skill", True)
        memory_enabled = d.get("memory_enabled", True)
        team_context = _build_team_context(sid, fixed_workspace_id) if teams_enabled else ""

        conversation_scope = runner.conversation_scope(conversation_job_id)
        conversation_scope.__enter__()
        try:
            for event in agent.run(
                message,
                list(sess.history),
                activation=activation,
                run_id=conversation_job_id,
                active_skill=active_skill,
                active_command=active_command,
                last_reasoning=getattr(sess, "last_reasoning", ""),
                last_prompt_tokens=getattr(sess, "last_prompt_tokens", 0),
                ppt_title=ppt_title,
                ppt_slides=ppt_slides,
                excel_tables=excel_tables,
                excel_filename=excel_filename,
                report_title=report_title,
                report_sections=report_sections,
                dashboard_name=dashboard_name,
                dashboard_widgets=dashboard_widgets,
                temp_prompt=active_temp_prompt,
                data_context=data_context,
                recovery_context=recovery_context,
                team_context=team_context,
                teams_enabled=teams_enabled,
                auto_match_skill=auto_match_skill,
                memory_enabled=memory_enabled,
                discovered_tools=frozenset(getattr(sess, "discovered_tools", []) or []),
                discovered_mcp_tools=list(getattr(sess, "discovered_mcp_tools", []) or []),
                mcp_catalog_version_seen=str(getattr(sess, "mcp_catalog_version", "") or ""),
                tool_result_artifacts=list(getattr(sess, "recent_artifacts", []) or []),
                recovery_state=recovery_state,
                recovery_checkpoint=_persist_recovery_checkpoint,
                cancel_check=(job_context.check_canceled if job_context is not None else None),
            ):
                current_job = runner.get_status(conversation_job_id) or {}
                if sess.cancel_requested or current_job.get("status") in {"canceling", "canceled"}:
                    log.info("[chat] cancelled by user  sid=%s", sid)
                    if current_job.get("status") != "canceled":
                        runner.cancel_tracked(conversation_job_id)
                    sess.cancel_requested = False
                    yield _sse({"type": "stopped"})
                    return

                event = _persist_stream_event(event)
                etype = event.get("type")
                if etype == "tool_start":
                    _start_step(event)
                elif etype == "tool_audit":
                    sess.record_tool_audit(event)
                    _finish_step(
                        str(event.get("tool") or "unknown"),
                        bool(event.get("ok", True)),
                        str(event.get("error") or ""),
                        event.get("elapsed_seconds"),
                    )
                    for artifact in event.get("artifacts") or []:
                        _append_parent_artifact(artifact)
                    _append_tool_result_summary(event)
                    _collect_downloads(str(event.get("content") or ""))
                    # Recovery metadata is server-only; do not expose full SQL
                    # or future internal context fields through browser SSE.
                    event = {key: value for key, value in event.items() if key != "recovery"}
                elif etype == "tool_end":
                    _finish_step(str(event.get("tool") or "unknown"))
                elif etype == "artifact_created" and event.get("artifact"):
                    _append_parent_artifact(event["artifact"])
                elif etype == "skill_activated":
                    sess.auto_loaded_skill = event.get("name", "")
                elif etype == "retry":
                    runner.append_tracked_event(
                        conversation_job_id,
                        {
                            "type": "model_retry",
                            "job_id": conversation_job_id,
                            "provider": str(event.get("provider") or "")[:120],
                            "model": str(event.get("model") or "")[:160],
                            "attempt": max(1, int(event.get("attempt") or 1)),
                            "max_retries": max(1, int(event.get("max_retries") or 1)),
                            "wait_seconds": max(0.0, float(event.get("wait_seconds") or 0)),
                            "reason": str(event.get("reason") or "transport_error")[:80],
                            "error_type": str(event.get("error_type") or "")[:120],
                        },
                    )
                elif etype == "error":
                    stream_error = str(event.get("message") or "Conversation failed")
                    stream_error_code = str(event.get("code") or "").strip()[:120]
                    stream_recovery_action = str(event.get("recovery_action") or "").strip()[:120]
                elif etype == "ask_user" and is_internal_feishu:
                    # The Web client renders a selectable card for this event.
                    # Feishu has no such card in this integration, so turn it
                    # into an ordinary replyable message instead of ending the
                    # mobile turn with an empty bot message.
                    event = {
                        "type": "text",
                        "content": format_feishu_ask_user(event),
                    }
                    etype = "text"
                elif etype == "hook_event":
                    runner.append_tracked_event(
                        conversation_job_id,
                        {
                            "type": "hook_event",
                            "job_id": conversation_job_id,
                            "hook_id": event.get("hook_id", ""),
                            "event": event.get("event", ""),
                            "ok": bool(event.get("ok", True)),
                            "output": str(event.get("output") or "")[:500],
                        },
                    )

                # Provider/fallback safety net. BusinessAgent normally separates
                # <think> during streaming, but never let an embedded block leak
                # into the final answer if a compatibility path returns it whole.
                if etype == "text":
                    _persist_recovery_checkpoint(
                        {
                            "phase": "finalizing",
                            "replay_safe": False,
                            "run_id": conversation_job_id,
                            "partial_content": str(event.get("content") or "")[:120_000],
                        }
                    )
                    visible_text, embedded_reasoning = split_reasoning_tags(event.get("content", ""))
                    if embedded_reasoning:
                        collected_reasoning.append(embedded_reasoning)
                        yield _sse(
                            _persist_stream_event(
                                {
                                    "type": "reasoning",
                                    "content": embedded_reasoning,
                                }
                            )
                        )
                    event = {**event, "content": visible_text}

                if etype == "chart_html":
                    cid = uuid.uuid4().hex
                    chart_store[cid] = event["html"]
                    register_artifact(
                        chart_store.path_for(cid),
                        artifact_type="chart",
                        session_id=sid,
                        workspace_id=fixed_workspace_id,
                        artifact_id=f"chart:{cid}",
                        metadata={
                            "run_id": conversation_job_id,
                            "cost": _artifact_run_cost(),
                        },
                    )
                    if not hasattr(sess, "chart_ids"):
                        sess.chart_ids = []
                    sess.chart_ids.append(cid)
                    turn_chart_ids.append(cid)
                    chart_title = str(
                        event.get("title") or event.get("chart_type") or f"图表 {len(turn_chart_ids)}"
                    ).strip()
                    chart_type = str(event.get("chart_type") or "").strip()
                    log.info("[chat] chart generated  sid=%s  chart_id=%s", sid, cid)
                    yield _sse(
                        _persist_stream_event(
                            {
                                "type": "chart_ref",
                                "chart_id": cid,
                                "title": chart_title,
                                "chart_type": chart_type,
                            }
                        )
                    )
                    _append_parent_artifact(
                        {
                            "type": "chart",
                            "name": chart_title,
                            "chart_type": chart_type,
                            "url": f"/api/chart/{cid}",
                            "chart_id": cid,
                        }
                    )
                elif etype == "chart_placeholder":
                    pass
                elif etype == "ppt_scheme":
                    sess.ppt_color_scheme = event.get("scheme", "mckinsey")
                elif etype == "usage":
                    run_usage["model_calls"] += max(1, int(event.get("model_calls") or 1))
                    run_usage["input_tokens"] += max(0, int(event.get("prompt_tokens") or 0))
                    run_usage["output_tokens"] += max(0, int(event.get("completion_tokens") or 0))
                    run_usage["cached_input_tokens"] += max(0, int(event.get("cached_input_tokens") or 0))
                    provider_name = str(
                        event.get("provider") or getattr(agent, "_provider", "") or ""
                    ).strip()
                    model_name = str(event.get("model") or getattr(agent, "model", "") or "").strip()
                    if provider_name:
                        run_usage["providers"].add(provider_name)
                    if model_name:
                        run_usage["models"].add(model_name)
                    if event.get("cost_usd") is None:
                        run_usage["price_unknown"] = True
                    else:
                        run_usage["priced_cost_usd"] += max(0.0, float(event["cost_usd"]))
                    agent._artifact_metadata["cost"] = _artifact_run_cost()
                    update_artifact_run_usage(
                        session_id=sid,
                        run_id=conversation_job_id,
                        cost=agent._artifact_metadata["cost"],
                    )
                    sess.record_usage(
                        event.get("prompt_tokens", 0),
                        event.get("completion_tokens", 0),
                        breakdown=event.get("prompt_breakdown"),
                        cached_input_tokens=event.get("cached_input_tokens", 0),
                        cache_write_tokens=event.get("cache_write_tokens", 0),
                        cost_usd=event.get("cost_usd"),
                    )
                    # Cloud mode: record per-user daily token usage
                    if cloud_login_enabled():
                        from data.auth_store import add_usage

                        _total = (event.get("prompt_tokens", 0) or 0) + (
                            event.get("completion_tokens", 0) or 0
                        )
                        if _total > 0:
                            add_usage(user_id, _total)
                    cfg = config_manager.get_config(sess.model_provider)
                    enriched = {
                        **event,
                        "max_output_tokens": cfg.max_output_tokens if cfg else None,
                        "session_total_input": sess.total_input_tokens,
                        "session_total_output": sess.total_output_tokens,
                        "session_total_cost_usd": round(sess.total_cost_usd, 8),
                        "cost_currency": "USD",
                    }
                    if not enriched.get("context_window"):
                        enriched["context_window"] = cfg.context_window if cfg else None
                    yield _sse(enriched)
                elif etype == "history_compacted":
                    compacted_history = event.get("history")
                    if isinstance(compacted_history, list):
                        sess.history = compacted_history
                    # Internal state mutation; the browser only needs the
                    # surrounding compaction activity events.
                else:
                    yield _sse(event)

                if etype == "text":
                    collected.append(event.get("content", ""))
                elif etype == "reasoning":
                    collected_reasoning.append(event.get("content", ""))
                elif etype == "tool_history":
                    msgs = event.get("messages", [])
                    sess.add_tool_messages(msgs)
                    names = [
                        m.get("tool_calls", [{}])[0].get("function", {}).get("name", "")
                        for m in msgs
                        if m.get("role") == "assistant" and m.get("tool_calls")
                    ]
                    tool_calls_in_turn.extend(n for n in names if n)
                elif etype == "tool_start":
                    pass  # already logged by agent.py

            for tool, queue in list(pending_steps.items()):
                while queue:
                    _finish_step(tool, ok=not bool(stream_error), error=stream_error)
            completed_normally = True
            final_answer = "".join(collected).strip()
            if not final_answer:
                # Never post a title-only response to Feishu (or retain an
                # empty assistant turn) when a provider/tool produced no text.
                # Details remain in server logs; the user gets an actionable,
                # non-sensitive recovery message.
                final_answer = (
                    "本次处理未生成可发送的正文。请直接重试一次；"
                    "若仍未收到结果，请在网页对话查看任务状态后再继续。"
                )
                log.warning(
                    "[chat] empty final answer sid=%s internal_feishu=%s stream_error=%s",
                    sid,
                    is_internal_feishu,
                    bool(stream_error),
                )
            sess.add_user(message)
            sess.add_assistant(
                final_answer,
                reasoning="".join(collected_reasoning),
                chart_ids=turn_chart_ids,
            )
            if is_internal_feishu:
                # Web-originated turns already arrive in the browser through
                # this request's SSE.  Only mobile/desktop Feishu turns need
                # the incremental session bridge below.
                sess.record_feishu_inbound_event("user", message)
                sess.record_feishu_inbound_event("assistant", final_answer)
            if bool(getattr(sess, "feishu_bot_enabled", False)) and getattr(sess, "feishu_chat_id", ""):
                yield _sse(_persist_stream_event({"type": "feishu_sync", "status": "sending"}))
                try:
                    from data.feishu_bot_service import send_conversation_turn, send_text

                    if is_internal_feishu:
                        send_text(
                            f"🤖 {PRODUCT_SHORT_NAME} Agent\n" + final_answer,
                            receive_id=sess.feishu_chat_id,
                            receive_id_type="chat_id",
                        )
                    else:
                        send_conversation_turn(
                            chat_id=sess.feishu_chat_id,
                            user_message=message,
                            assistant_message=final_answer,
                        )
                    yield _sse(_persist_stream_event({"type": "feishu_sync", "status": "sent"}))
                except Exception as exc:
                    # A collaboration-side delivery failure must never turn a
                    # completed Web analysis into a failed conversation.
                    log.warning("[feishu] conversation sync failed sid=%s: %s", sid, exc)
                    yield _sse(_persist_stream_event({"type": "feishu_sync", "status": "failed"}))
            if hook_engine and hook_context:
                end_context = hook_context.child(
                    event_name="turn_end",
                    final_answer=final_answer,
                    elapsed_seconds=time.monotonic() - _turn_start,
                )
                for notification in hook_engine.run_hooks("turn_end", end_context):
                    yield _sse(notification.to_event())
            _collect_downloads(final_answer)
            if stream_error:
                if stream_error_code == "agent_run_timeout":
                    runner.timeout_tracked(
                        conversation_job_id,
                        stream_error,
                        error_code=stream_error_code,
                        recovery_action=stream_recovery_action,
                    )
                else:
                    runner.fail_tracked(
                        conversation_job_id,
                        stream_error,
                        error_code=stream_error_code,
                        recovery_action=stream_recovery_action,
                    )
            else:
                if not session_manager.persist(sid):
                    log.warning(
                        "[chat] completed session state was not persisted sid=%s",
                        sid,
                    )
                runner.succeed_tracked(
                    conversation_job_id,
                    {
                        "answer": final_answer,
                        "step_count": step_count,
                        "chart_ids": turn_chart_ids,
                        "activation": activation.to_record(),
                    },
                )
                if not sess.cancel_requested:
                    try:
                        if memory_enabled and _schedule_memory_extraction is not None:
                            _schedule_memory_extraction(
                                provider=sess.model_provider or config_manager.get_default_provider() or "",
                                session_id=sid,
                                user_id=user_id,
                                workspace_id=fixed_workspace_id,
                                user_message=message,
                                assistant_message=final_answer,
                                runner=runner,
                            )
                        # Governance piggybacks on turn end: the 24h lock-file
                        # gate makes this a cheap stat() on most turns.
                        if memory_enabled and _maybe_schedule_memory_consolidation is not None:
                            _maybe_schedule_memory_consolidation(
                                provider=sess.model_provider or config_manager.get_default_provider() or "",
                                session_id=sid,
                                user_id=user_id,
                                workspace_id=fixed_workspace_id,
                            )
                    except Exception as exc:
                        log.warning(
                            "[memory] extraction scheduling failed sid=%s: %s",
                            sid,
                            exc,
                        )

            elapsed = time.monotonic() - _turn_start
            reply_preview = "".join(collected)[:120].replace("\n", " ")
            log.info(
                "[chat] turn done  sid=%s  elapsed=%.2fs  tools=%s  charts=%d  "
                "total_in=%d  total_out=%d  reply=%.120r",
                sid,
                elapsed,
                tool_calls_in_turn or "none",
                len(turn_chart_ids),
                sess.total_input_tokens,
                sess.total_output_tokens,
                reply_preview,
            )

        except JobCanceled:
            # A cooperative stop is a terminal cancellation, not an internal
            # Agent error.  Let the JobRunner/finally path persist canceled and
            # release the fixed snapshots without emitting a misleading error.
            log.info("[chat] Agent observed cancellation  sid=%s", sid)
            return
        except AgentRunTimeout:
            # Team delegation and other bounded child paths can raise the
            # internal deadline signal directly.  Keep the HTTP contract
            # identical to the main streaming loop instead of misclassifying
            # a controlled timeout as an internal error.
            timeout_message = "分析超过运行时间上限，已安全终止。请缩小问题范围后重试。"
            log.warning("[chat] Agent deadline reached  sid=%s", sid)
            runner.timeout_tracked(
                conversation_job_id,
                timeout_message,
                error_code="agent_run_timeout",
                recovery_action="retry_with_smaller_scope",
            )
            yield _sse(
                {
                    "type": "error",
                    "message": timeout_message,
                    "code": "agent_run_timeout",
                    "recovery_action": "retry_with_smaller_scope",
                }
            )
            return
        except Exception as exc:
            log.exception("[chat] unhandled agent error  sid=%s", sid)
            runner.fail_tracked(conversation_job_id, f"{type(exc).__name__}: {exc}")
            if hook_engine and hook_context:
                error_context = hook_context.child(event_name="error", error=str(exc))
                for notification in hook_engine.run_hooks("error", error_context):
                    yield _sse(notification.to_event())
            yield _sse({"type": "error", "message": f"内部错误：{exc}", "code": "agent_runtime_failed"})

        finally:
            conversation_scope.__exit__(None, None, None)
            generator_exception = sys.exc_info()[1]
            stream_closed = stream_closed or isinstance(generator_exception, GeneratorExit)
            current = runner.get_status(conversation_job_id)
            if stream_closed:
                if current and current.get("status") not in {"succeeded", "failed", "canceled"}:
                    runner.cancel_tracked(conversation_job_id)
                sess.cancel_requested = False
            elif current and current.get("status") not in {"succeeded", "failed", "canceled"}:
                if sess.cancel_requested:
                    runner.cancel_tracked(conversation_job_id)
                    sess.cancel_requested = False
                elif not completed_normally:
                    runner.fail_tracked(conversation_job_id, "Conversation stream ended before completion.")
            if file_history is not None and file_history_snapshot_id:
                current = runner.get_status(conversation_job_id) or {}
                try:
                    file_history.finalize_snapshot(
                        file_history_snapshot_id,
                        str(current.get("status") or "interrupted"),
                    )
                except FileHistoryError:
                    log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
            source_snapshot.release()
            current = runner.get_status(conversation_job_id) or {}
            status = str(current.get("status") or "")
            final_cost = _artifact_run_cost(status or "interrupted")
            agent._artifact_metadata["cost"] = final_cost
            update_artifact_run_usage(
                session_id=sid,
                run_id=conversation_job_id,
                cost=final_cost,
            )
            _record_prompt_command_metric(
                "success" if status == "succeeded" else "error",
                "" if status == "succeeded" else (status or "stream_incomplete"),
            )
            if not stream_closed:
                yield _sse({"type": "done"})

    def _run_generation(_ctx) -> None:
        """Exhaust the turn in the detached chat worker.

        ``generate`` persists every public SSE event through ``_sse``.  The
        HTTP response below only reads the durable event stream, so closing
        the response cannot close the Agent generator or release its snapshot
        halfway through a tool call.
        """
        try:
            for _chunk in generate(_ctx):
                pass
        except Exception as exc:
            # Setup errors before the inner Agent try/finally (for example a
            # schema artifact write) must not strand the source lease or file
            # history snapshot. The normal path is idempotent with this guard.
            log.exception("[chat] detached generation escaped sid=%s", sid)
            runner.fail_tracked(conversation_job_id, f"{type(exc).__name__}: {exc}")
            if file_history is not None and file_history_snapshot_id:
                try:
                    file_history.finalize_snapshot(file_history_snapshot_id, "failed")
                except FileHistoryError:
                    log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
            source_snapshot.release()
            raise

    def _stream_job_events():
        """Relay durable public events while leaving the job independent."""
        public_error_seen = False
        # Flush the response immediately while the worker performs Agent
        # construction and data/tool setup. This transport-only event is not
        # persisted because it carries no user-visible result.
        yield _sse(
            {
                "type": "agent_activity",
                "message": "",
                "job_id": conversation_job_id,
            },
            persist=False,
        )
        try:
            for record in runner.iter_events(
                conversation_job_id,
                after_sequence=chat_event_after_sequence,
            ):
                event_type = str(record.get("type") or "")
                if event_type == "conversation_stream_event":
                    event = dict(record.get("event") or {})
                    if event.get("type") not in _CHAT_REPLAYABLE_EVENTS:
                        continue
                    event["stream_sequence"] = int(record["sequence"])
                    if event.get("type") == "error":
                        public_error_seen = True
                    yield _sse(event)
                elif event_type == "job_error":
                    error_event = _chat_job_error_event(record)
                    if not public_error_seen:
                        yield _sse(
                            {
                                **error_event,
                                "stream_sequence": int(record["sequence"]),
                            }
                        )
                    yield _sse(
                        {
                            "type": "done",
                            "stream_sequence": int(record["sequence"]),
                        }
                    )
                elif event_type == "job_canceled":
                    yield _sse(
                        {
                            "type": "stopped",
                            "stream_sequence": int(record["sequence"]),
                        }
                    )
                    yield _sse(
                        {
                            "type": "done",
                            "stream_sequence": int(record["sequence"]),
                        }
                    )
                elif event_type == "job_done":
                    yield _sse(
                        {
                            "type": "done",
                            "stream_sequence": int(record["sequence"]),
                        }
                    )
        except GeneratorExit:
            # The client may leave; the detached worker must keep running.
            return

    def _cleanup_unstarted_turn() -> None:
        """Release resources if stop wins before the worker starts."""
        if file_history is not None and file_history_snapshot_id:
            try:
                file_history.finalize_snapshot(file_history_snapshot_id, "canceled")
            except FileHistoryError:
                log.exception("[filehistory] early snapshot finalize failed sid=%s", sid)
        source_snapshot.release()
        sess.cancel_requested = False

    try:
        runner.submit_tracked(
            conversation_job_id,
            _run_generation,
            on_cancel_before_start=_cleanup_unstarted_turn,
        )
    except Exception as exc:
        log.exception("[chat] detached worker submission failed sid=%s", sid)
        runner.fail_tracked(conversation_job_id, f"{type(exc).__name__}: {exc}")
        if file_history is not None and file_history_snapshot_id:
            try:
                file_history.finalize_snapshot(file_history_snapshot_id, "failed")
            except FileHistoryError:
                log.exception("[filehistory] snapshot finalize failed sid=%s", sid)
        source_snapshot.release()

    return Response(
        _stream_job_events(),
        mimetype="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "X-PFS-Conversation-Job": conversation_job_id,
        },
    )
