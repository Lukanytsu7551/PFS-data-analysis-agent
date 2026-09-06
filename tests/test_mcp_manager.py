import asyncio
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from agent.jobs import JobCanceled
from agent.mcp_manager import (
    MCPManager,
    MCPServerConnection,
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
)


class PfsMcpReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.manager = MCPManager()
        self.manager.start()

    def tearDown(self):
        loop = self.manager._loop
        thread = self.manager._thread
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread:
            thread.join(timeout=1)
        if loop and not loop.is_closed():
            loop.close()

    def test_submit_propagates_abort_and_cancels_coroutine(self):
        started = threading.Event()
        canceled = threading.Event()

        async def blocked():
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                canceled.set()
                raise

        abort_calls = [0]

        def abort_check():
            abort_calls[0] += 1
            if abort_calls[0] == 1:
                started.wait(timeout=1)
            if abort_calls[0] >= 2:
                raise JobCanceled("mcp-stop")

        with self.assertRaises(JobCanceled):
            self.manager._submit(blocked(), timeout=5, abort_check=abort_check)

        self.assertTrue(started.is_set())
        self.assertTrue(canceled.wait(timeout=1))

    def test_submit_turns_deadline_into_timeout_and_cancels_future(self):
        canceled = threading.Event()

        async def blocked():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                canceled.set()
                raise

        with self.assertRaises(TimeoutError):
            self.manager._submit(blocked(), timeout=0.05)

        self.assertTrue(canceled.wait(timeout=1))

    def test_submit_closes_coroutine_when_event_loop_is_not_running(self):
        manager = MCPManager()

        async def never_started():
            await asyncio.sleep(60)

        coroutine = never_started()
        with self.assertRaisesRegex(RuntimeError, "event loop not running"):
            manager._submit(coroutine)
        self.assertIsNone(coroutine.cr_frame)

    def test_call_tool_does_not_convert_agent_cancellation_to_mcp_error(self):
        class FakeConnection:
            def __init__(self):
                self.started = threading.Event()
                self.canceled = threading.Event()

            async def call_tool(self, _tool_name, _args, timeout=60):
                del timeout
                self.started.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    self.canceled.set()
                    raise

        connection = FakeConnection()
        self.manager._connections["fake"] = connection
        abort_calls = [0]

        def abort_check():
            abort_calls[0] += 1
            if abort_calls[0] == 1:
                connection.started.wait(timeout=1)
            if abort_calls[0] >= 2:
                raise JobCanceled("mcp-call-stop")

        with self.assertRaises(JobCanceled):
            self.manager.call_tool(
                "mcp__fake__wait",
                {},
                timeout=5,
                abort_check=abort_check,
            )

        self.assertTrue(connection.canceled.wait(timeout=1))

    def test_connection_bounds_transport_wait_even_without_sync_manager(self):
        canceled = threading.Event()

        class FakeTransport:
            async def send_request(self, _method, _params, timeout=None):
                del timeout
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    canceled.set()
                    raise

            async def close(self):
                return None

        connection = MCPServerConnection(SimpleNamespace(server_id="fake"))
        connection.status = STATUS_CONNECTED
        connection._transport = FakeTransport()

        result = asyncio.run(connection.call_tool("wait", {}, timeout=0.05))

        self.assertIn("MCP ERROR", result)
        self.assertTrue(canceled.wait(timeout=1))

    def test_connection_bounds_reconnect_wait_before_call(self):
        connection = MCPServerConnection(SimpleNamespace(server_id="fake"))

        result = asyncio.run(connection.call_tool("wait", {}, timeout=0.05))

        self.assertIn("调用前重连超过时间上限", result)

    def test_stdio_transport_completes_real_local_mcp_handshake_and_tool_call(self):
        server_code = """
import json
import sys

for raw_line in sys.stdin:
    message = json.loads(raw_line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {}}
    elif method == "tools/list":
        result = {
            "tools": [{
                "name": "echo",
                "description": "Return the supplied message",
                "inputSchema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            }]
        }
    elif method == "tools/call":
        message_arg = message.get("params", {}).get("arguments", {}).get("message", "")
        result = {"content": [{"type": "text", "text": "echo:" + message_arg}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
"""
        with TemporaryDirectory(prefix="pfs-mcp-stdio-") as temp_dir:
            server_path = Path(temp_dir) / "server.py"
            server_path.write_text(server_code, encoding="utf-8")
            config = SimpleNamespace(
                server_id="local-stdio",
                transport="stdio",
                command=sys.executable,
                args=[str(server_path)],
                env={},
            )
            connection = MCPServerConnection(config)

            async def exercise():
                connected = await connection.connect()
                connected_status = connection.status
                tools = connection.get_openai_schemas()
                result = await connection.call_tool(
                    "echo", {"message": "hello"}, timeout=3,
                )
                await connection.disconnect()
                return connected, connected_status, tools, result, connection.status

            connected, connected_status, tools, result, status = asyncio.run(exercise())

        self.assertTrue(connected)
        self.assertEqual(STATUS_CONNECTED, connected_status)
        self.assertEqual(STATUS_DISCONNECTED, status)
        self.assertEqual("echo", tools[0]["function"]["name"].split("__")[-1])
        self.assertEqual("echo:hello", result)


if __name__ == "__main__":
    unittest.main()
