"""
End-to-end JSON-RPC stdio protocol test for persistent-chrome FastMCP server.

Verifies:
1. Subprocess spawning with stdio communication.
2. 'initialize' request and serverInfo verification (server name: persistent-chrome).
3. 'notifications/initialized' notification delivery.
4. 'tools/list' request and verification of all 7 persistent Chrome tools.
5. 'tools/call' request for 'persistent_chrome_status' returning valid JSON status content.
6. Negative case: sending invalid JSON string returns an error without crashing the server process.
7. Clean subprocess shutdown without leaving orphaned processes.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

EXPECTED_TOOLS: Set[str] = {
    "persistent_chrome_open",
    "persistent_chrome_snapshot",
    "persistent_chrome_click",
    "persistent_chrome_type",
    "persistent_chrome_screenshot",
    "persistent_chrome_status",
    "persistent_chrome_close",
}


class MCPStdioClient:
    """Managed stdio client for testing MCP servers over JSON-RPC 2.0 pipes."""

    def __init__(self, repo_root: Optional[Path] = None, timeout: float = 15.0):
        self.repo_root = repo_root or Path(__file__).resolve().parent.parent
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen] = None
        self.stdout_queue: queue.Queue[Optional[str]] = queue.Queue()
        self.stderr_lines: List[str] = []
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Spawn the FastMCP server subprocess with stdio pipes."""
        env = os.environ.copy()
        src_dir = str(self.repo_root / "src")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{src_dir}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else src_dir
        )

        self.proc = subprocess.Popen(
            [sys.executable, "-m", "playwright_chrome.mcp_server"],
            cwd=str(self.repo_root),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
        )

        def _drain_stdout():
            try:
                for line in iter(self.proc.stdout.readline, ""):
                    self.stdout_queue.put(line)
            except Exception:
                pass
            finally:
                self.stdout_queue.put(None)

        def _drain_stderr():
            try:
                for line in iter(self.proc.stderr.readline, ""):
                    self.stderr_lines.append(line)
            except Exception:
                pass

        self._stdout_thread = threading.Thread(
            target=_drain_stdout, name="MCPStdoutReader", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=_drain_stderr, name="MCPStderrReader", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def send_raw(self, line: str) -> None:
        """Send a raw text line over stdin."""
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("MCP server subprocess is not running")
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def send_json(self, data: Dict[str, Any]) -> None:
        """Send a JSON-RPC message serialized over stdin."""
        self.send_raw(json.dumps(data))

    def read_line(self, timeout: Optional[float] = None) -> str:
        """Read next newline-delimited message from stdout with timeout."""
        wait_time = timeout if timeout is not None else self.timeout
        try:
            line = self.stdout_queue.get(timeout=wait_time)
            if line is None:
                raise EOFError("Subprocess stdout stream closed (EOF)")
            return line.strip()
        except queue.Empty:
            raise TimeoutError(
                f"Timed out after {wait_time}s waiting for server stdout response"
            )

    def read_json(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Read and parse next JSON-RPC message from stdout."""
        line = self.read_line(timeout=timeout)
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse JSON response: {line!r}") from exc

    def is_alive(self) -> bool:
        """Check whether the subprocess is still actively running."""
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def close(self) -> Optional[int]:
        if self.proc is None:
            return None

        if self.proc.stdin and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except Exception:
                pass

        exit_code: Optional[int] = None
        try:
            exit_code = self.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                self.proc.terminate()
                exit_code = self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.kill()
                    exit_code = self.proc.wait(timeout=2.0)
                except Exception:
                    exit_code = self.proc.poll()

        if self.proc.stdout and not self.proc.stdout.closed:
            try:
                self.proc.stdout.close()
            except Exception:
                pass
        if self.proc.stderr and not self.proc.stderr.closed:
            try:
                self.proc.stderr.close()
            except Exception:
                pass

        if self._stdout_thread and self._stdout_thread.is_alive():
            self._stdout_thread.join(timeout=1.0)
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=1.0)

        return exit_code


class TestMCPStdioProtocol(unittest.TestCase):
    """End-to-end JSON-RPC stdio protocol test suite for persistent-chrome FastMCP."""

    def setUp(self):
        self.client = MCPStdioClient()
        self.client.start()

    def tearDown(self):
        if self.client:
            self.client.close()

    def test_full_mcp_stdio_lifecycle(self):
        """
        Verify end-to-end stdio protocol workflow:
        1. initialize request -> serverInfo name "persistent-chrome"
        2. notifications/initialized delivery
        3. tools/list request -> all 7 tools present
        4. tools/call request for persistent_chrome_status -> valid JSON status content
        5. Negative test -> invalid JSON returns error and does not crash server
        6. Clean shutdown
        """
        # --- Step 1: initialize request ---
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "test-stdio-client",
                    "version": "1.0.0",
                },
            },
        }
        self.client.send_json(init_request)
        init_response = self.client.read_json()

        self.assertEqual(init_response.get("jsonrpc"), "2.0")
        self.assertEqual(init_response.get("id"), 1)
        self.assertIn("result", init_response)
        result = init_response["result"]

        server_info = result.get("serverInfo", {})
        self.assertEqual(
            server_info.get("name"),
            "persistent-chrome",
            f"Expected server name 'persistent-chrome', got {server_info.get('name')!r}",
        )
        self.assertEqual(result.get("protocolVersion"), "2024-11-05")
        self.assertIn("capabilities", result)
        self.assertIn("tools", result["capabilities"])
        print("[PASS] Step 1: initialize handshake succeeded with server 'persistent-chrome'")

        # --- Step 2: notifications/initialized ---
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        self.client.send_json(initialized_notification)
        print("[PASS] Step 2: notifications/initialized sent")

        # --- Step 3: tools/list request ---
        tools_list_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        self.client.send_json(tools_list_request)
        tools_list_response = self.client.read_json()

        self.assertEqual(tools_list_response.get("jsonrpc"), "2.0")
        self.assertEqual(tools_list_response.get("id"), 2)
        self.assertIn("result", tools_list_response)
        tools_result = tools_list_response["result"]
        self.assertIn("tools", tools_result)

        tools = tools_result["tools"]
        tool_names = {t["name"] for t in tools}
        for expected_tool in EXPECTED_TOOLS:
            self.assertIn(
                expected_tool,
                tool_names,
                f"Expected tool '{expected_tool}' missing from tools/list",
            )
        self.assertEqual(
            tool_names,
            EXPECTED_TOOLS,
            f"Tools mismatch. Expected {EXPECTED_TOOLS}, found {tool_names}",
        )
        print(f"[PASS] Step 3: tools/list verified all 7 tools: {sorted(tool_names)}")

        # --- Step 4: tools/call request for persistent_chrome_status ---
        status_call_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "persistent_chrome_status",
                "arguments": {},
            },
        }
        self.client.send_json(status_call_request)
        status_response = self.client.read_json()

        self.assertEqual(status_response.get("jsonrpc"), "2.0")
        self.assertEqual(status_response.get("id"), 3)
        self.assertIn("result", status_response)
        call_result = status_response["result"]
        self.assertIn("content", call_result)
        content_items = call_result["content"]
        self.assertGreaterEqual(len(content_items), 1)

        first_content = content_items[0]
        self.assertEqual(first_content.get("type"), "text")
        status_text = first_content.get("text", "")
        self.assertTrue(len(status_text) > 0, "status text content must not be empty")

        # Parse and verify valid JSON text payload
        status_data = json.loads(status_text)
        self.assertIsInstance(status_data, dict)
        expected_status_keys = {
            "profile_path",
            "cdp_active",
            "session_active",
            "connection_mode",
            "current_url",
            "page_title",
            "cookies_count",
        }
        self.assertTrue(
            expected_status_keys.issubset(status_data.keys()),
            f"Status JSON missing expected keys. Expected: {expected_status_keys}, Found: {set(status_data.keys())}",
        )
        print(f"[PASS] Step 4: tools/call persistent_chrome_status returned valid JSON: {status_data}")

        # --- Step 5: Negative test case with invalid JSON ---
        invalid_json_line = "{invalid_json_syntax: true,"
        self.client.send_raw(invalid_json_line)
        error_response = self.client.read_json()

        # FastMCP / JSON-RPC error verification:
        # Standard JSON-RPC 2.0 defines code -32700 (Parse error).
        # FastMCP stdio transport in Python MCP SDK sends an error notification
        # (method="notifications/message", level="error") or a JSON-RPC error object.
        is_parse_error_code = (
            isinstance(error_response.get("error"), dict)
            and error_response["error"].get("code") == -32700
        )
        is_mcp_error_notification = (
            error_response.get("method") == "notifications/message"
            and isinstance(error_response.get("params"), dict)
            and error_response["params"].get("level") == "error"
        )
        self.assertTrue(
            is_parse_error_code or is_mcp_error_notification,
            f"Expected Parse error (-32700) or error notification, received: {error_response}",
        )

        # Confirm the server process did NOT crash
        self.assertTrue(
            self.client.is_alive(),
            "Server process crashed unexpectedly after receiving invalid JSON string",
        )

        # Confirm server is still responsive by sending another request
        recovery_request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/list",
            "params": {},
        }
        self.client.send_json(recovery_request)
        recovery_response = self.client.read_json()
        self.assertEqual(recovery_response.get("id"), 4)
        self.assertIn("result", recovery_response)
        print("[PASS] Step 5: Negative test handled gracefully; server remained alive and responsive")

        # --- Step 6: Clean shutdown ---
        exit_code = self.client.close()
        self.assertFalse(self.client.is_alive(), "Subprocess should no longer be alive")
        print(f"[PASS] Step 6: Clean subprocess shutdown verified (exit code: {exit_code})")


def run_standalone_test() -> int:
    """Run test directly with descriptive output and exit code."""
    print("=" * 70)
    print("Starting End-to-End MCP JSON-RPC Stdio Protocol Test")
    print("=" * 70)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMCPStdioProtocol)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("=" * 70)
        print("ALL TESTS PASSED SUCCESSFULLY (Exit Code 0)")
        print("=" * 70)
        return 0
    else:
        print("=" * 70)
        print(f"TESTS FAILED: {len(result.failures)} failures, {len(result.errors)} errors")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(run_standalone_test())
