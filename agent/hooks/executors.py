"""Hook action executors."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
import urllib.request
from typing import Any

from ..errors import AgentRunTimeout
from ..jobs import JobCanceled
from .models import Action, ActionResult, HookContext


log = logging.getLogger(__name__)


def execute_action(
    action: Action,
    ctx: HookContext,
    *,
    allow_command: bool = False,
    abort_check=None,
) -> ActionResult:
    if abort_check is not None:
        abort_check()
    if action.type == "prompt":
        return ActionResult(output=ctx.expand(action.message), success=True)
    if action.type == "http":
        return _execute_http(action, ctx, abort_check=abort_check)
    if action.type == "command":
        if not allow_command:
            return ActionResult(output="command hooks are disabled", success=False)
        return _execute_command(action, ctx, abort_check=abort_check)
    return ActionResult(output=f"unsupported hook action: {action.type}", success=False)


def _terminate_process(process: subprocess.Popen) -> None:
    """Best-effort termination for a canceled or timed-out Hook command."""
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.communicate(timeout=0.5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_bounded_command(args: list[str], *, timeout: float, abort_check=None):
    """Run a Hook command while polling the caller's cancellation contract."""
    bounded_timeout = max(0.001, float(timeout or 10))
    deadline = time.monotonic() + bounded_timeout
    process: subprocess.Popen | None = None
    try:
        if abort_check is not None:
            abort_check()
        process = subprocess.Popen(
            args,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        while process.poll() is None:
            if abort_check is not None:
                abort_check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, bounded_timeout)
            try:
                process.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                continue
        stdout, stderr = process.communicate()
        return process.returncode, stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        if process is not None:
            _terminate_process(process)
        raise
    except BaseException:
        if process is not None:
            _terminate_process(process)
        raise


def _execute_command(action: Action, ctx: HookContext, *, abort_check=None) -> ActionResult:
    command = ctx.expand(action.command)
    try:
        args = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return ActionResult(output=f"invalid command syntax: {exc}", success=False)
    if not args:
        return ActionResult(output="command is empty", success=False)
    try:
        returncode, stdout, stderr = _run_bounded_command(
            args,
            timeout=action.timeout,
            abort_check=abort_check,
        )
    except subprocess.TimeoutExpired:
        return ActionResult(output=f"command timed out after {action.timeout}s", success=False)
    output = (stdout or stderr or "").strip()
    return ActionResult(output=output[:4000], success=returncode == 0)


def _run_bounded_http(operation, *, timeout: float | None, abort_check=None):
    """Run a Hook HTTP read while allowing cancellation to close its response."""
    if timeout is None and abort_check is None:
        return operation(lambda _response: None)

    state_lock = threading.Lock()
    active_response = [None]
    result: dict[str, Any] = {}

    def set_active(response) -> None:
        with state_lock:
            active_response[0] = response

    def clear_active() -> None:
        with state_lock:
            active_response[0] = None

    def close_active() -> None:
        with state_lock:
            response = active_response[0]
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                log.debug("[hooks] response close during cancellation failed", exc_info=True)

    def worker() -> None:
        try:
            result["value"] = operation(set_active)
        except BaseException as exc:
            result["error"] = exc
        finally:
            clear_active()

    thread = threading.Thread(target=worker, name="pfs-hook-http", daemon=True)
    thread.start()
    deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))

    def stop_fetch() -> None:
        close_active()
        thread.join(timeout=1.0)
        if thread.is_alive():
            log.warning("[hooks] HTTP worker did not stop after response close")

    while thread.is_alive():
        try:
            if abort_check is not None:
                abort_check()
        except BaseException:
            stop_fetch()
            raise
        if deadline is not None and time.monotonic() >= deadline:
            stop_fetch()
            raise AgentRunTimeout
        wait = 0.05
        if deadline is not None:
            wait = min(wait, max(0.001, deadline - time.monotonic()))
        thread.join(timeout=wait)

    error = result.get("error")
    if error is not None:
        raise error
    if abort_check is not None:
        abort_check()
    return result.get("value")


def _execute_http(action: Action, ctx: HookContext, *, abort_check=None) -> ActionResult:
    if abort_check is not None:
        abort_check()
    url = ctx.expand(action.url)
    if not url.startswith(("http://", "https://")):
        return ActionResult(output="http action only supports http:// and https:// URLs", success=False)
    method = (action.method or "POST").upper()
    body = ctx.expand(action.body)
    data = None
    headers = {"Content-Type": "application/json", **ctx.expand(action.headers)}
    if body is not None and method not in {"GET", "HEAD"}:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        request = urllib.request.Request(url, data=data, headers=headers, method=method)

        def fetch_response(set_active):
            with urllib.request.urlopen(request, timeout=action.timeout) as response:
                set_active(response)
                try:
                    output = response.read(4096).decode("utf-8", errors="replace")
                    status = getattr(response, "status", 200)
                finally:
                    set_active(None)
            return output, status

        output, status = _run_bounded_http(
            fetch_response,
            timeout=action.timeout,
            abort_check=abort_check,
        )
        return ActionResult(output=output, success=200 <= status < 300)
    except (JobCanceled, AgentRunTimeout):
        raise
    except Exception as exc:
        return ActionResult(output=str(exc), success=False)
