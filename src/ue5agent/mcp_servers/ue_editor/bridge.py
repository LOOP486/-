"""编辑器桥 TCP 客户端（UnrealMCP 插件协议：JSON {type, params} ↔ JSON 响应）。

纯逻辑模块，可对假 socket server 单测；server.py 只做接线。
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any

DEFAULT_PORT = 55557


def send_command(
    command: str,
    params: dict[str, Any] | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """发送一条命令并等待完整 JSON 响应（插件以关连接或完整 JSON 作为结束）。"""
    host = host or os.environ.get("UE_MCP_HOST", "127.0.0.1")
    port = port or int(os.environ.get("UE_MCP_PORT", DEFAULT_PORT))
    payload = json.dumps({"type": command, "params": params or {}}).encode("utf-8")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(payload)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            try:
                return json.loads(data.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue  # JSON 还没收完整
    raise ConnectionError("桥连接关闭但未收到完整 JSON 响应")
