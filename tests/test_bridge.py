"""ue_editor 桥：TCP 协议收发与分包容错（本地假 server）。"""

import json
import socket
import threading

from ue5agent.mcp_servers.ue_editor.bridge import PROTOCOL_VERSION, probe_editor, send_command


def fake_plugin_server(response: dict, *, captured: list | None = None) -> int:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def handle():
        conn, _ = server.accept()
        data = conn.recv(65536)
        if captured is not None:
            captured.append(data)
        payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
        # 故意分两包发送，验证客户端的"收到完整 JSON 才返回"
        conn.sendall(payload[: len(payload) // 2])
        conn.sendall(payload[len(payload) // 2 :])
        conn.close()
        server.close()

    threading.Thread(target=handle, daemon=True).start()
    return port


def test_send_command_reassembles_split_json():
    canned = {"status": "success", "result": {"actors": [{"name": "Floor", "class": "静态网格体"}]}}
    port = fake_plugin_server(canned)
    response = send_command("get_actors_in_level", port=port, timeout=5)
    assert response["status"] == "success"
    assert response["result"]["actors"][0]["name"] == "Floor"


def test_handshake_includes_protocol_version(monkeypatch):
    """D1.1：每条命令握手都带协议版本（插件可据此做版本不匹配报错）。"""
    # 清掉环境里可能存在的 token（uv run 会加载 .env，本机 .env 配了 UE_MCP_TOKEN_FILE）
    monkeypatch.delenv("UE_MCP_TOKEN", raising=False)
    monkeypatch.delenv("UE_MCP_TOKEN_FILE", raising=False)
    captured: list = []
    port = fake_plugin_server({"status": "success", "result": {}}, captured=captured)
    send_command("ping", port=port, timeout=5)
    sent = json.loads(captured[0].decode("utf-8"))
    assert sent["protocol"] == PROTOCOL_VERSION
    assert sent["type"] == "ping"
    assert "token" not in sent  # 未配 token 时不带（与无 token 插件兼容）


def test_handshake_includes_token_when_configured(monkeypatch):
    """D1.1：配置了 UE_MCP_TOKEN 时握手出示 token。"""
    monkeypatch.setenv("UE_MCP_TOKEN", "local-secret-123")
    captured: list = []
    port = fake_plugin_server({"status": "success", "result": {}}, captured=captured)
    send_command("ping", port=port, timeout=5)
    sent = json.loads(captured[0].decode("utf-8"))
    assert sent["token"] == "local-secret-123"


def test_token_read_from_file(monkeypatch, tmp_path):
    """D1.1：UE_MCP_TOKEN_FILE 指向插件写的 token 文件时读取之。"""
    token_file = tmp_path / "bridge_token.txt"
    token_file.write_text("file-token-xyz\n", encoding="utf-8")
    monkeypatch.delenv("UE_MCP_TOKEN", raising=False)
    monkeypatch.setenv("UE_MCP_TOKEN_FILE", str(token_file))
    captured: list = []
    port = fake_plugin_server({"status": "success", "result": {}}, captured=captured)
    send_command("ping", port=port, timeout=5)
    sent = json.loads(captured[0].decode("utf-8"))
    assert sent["token"] == "file-token-xyz"


def test_probe_editor_true_when_port_listening():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert probe_editor(port=port, timeout=1.0) is True
    finally:
        server.close()


def test_probe_editor_false_when_port_closed():
    # 先占一个端口再关掉，确保它当前无人监听
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert probe_editor(port=port, timeout=0.5) is False
