"""编辑器桥 TCP 客户端（UnrealMCP 插件协议：JSON {type, params} ↔ JSON 响应）。

纯逻辑模块，可对假 socket server 单测；server.py 只做接线。
"""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any

DEFAULT_PORT = 55557
_DEFAULT_RETRIES = 2  # 偶发超时/瞬断的重试次数（连发高频请求时桥可能短暂忙）
_RETRY_BACKOFF = 0.3  # 重试前退避（秒），给编辑器喘息，避免连发轰炸把桥打爆


def probe_editor(*, host: str | None = None, port: int | None = None, timeout: float = 2.0) -> bool:
    """探测编辑器桥端口是否可连接（只握手不发命令，适合状态检查与就绪轮询）。"""
    host = host or os.environ.get("UE_MCP_HOST", "127.0.0.1")
    port = port or int(os.environ.get("UE_MCP_PORT", DEFAULT_PORT))
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _send_once(
    command: str, params: dict[str, Any] | None, host: str, port: int, timeout: float
) -> dict[str, Any]:
    payload = json.dumps({"type": command, "params": params or {}}).encode("utf-8")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
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


def send_command(
    command: str,
    params: dict[str, Any] | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    timeout: float = 30.0,
    retries: int = _DEFAULT_RETRIES,
) -> dict[str, Any]:
    """发送一条命令并等待完整 JSON 响应（插件以关连接或完整 JSON 作为结束）。

    高频连发时桥可能偶发超时/瞬断（而非真正掉线）。对这类**瞬时**错误做带退避的
    重试，把"临时忙"和"真断线"区分开，避免上层（judge）把偶发超时误判成编辑器未连接。
    连接被拒（编辑器真没开）不重试，直接抛出。
    """
    host = host or os.environ.get("UE_MCP_HOST", "127.0.0.1")
    port = port or int(os.environ.get("UE_MCP_PORT", DEFAULT_PORT))
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _send_once(command, params, host, port, timeout)
        except ConnectionRefusedError:
            raise  # 编辑器未开/桥未监听：重试无意义，立即上抛
        except (TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))  # 线性退避，给桥喘息
                continue
            raise
    raise last_exc if last_exc else ConnectionError("send_command 未知失败")
