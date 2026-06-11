"""ue_editor 桥：TCP 协议收发与分包容错（本地假 server）。"""

import json
import socket
import threading

from ue5agent.mcp_servers.ue_editor.bridge import send_command


def fake_plugin_server(response: dict) -> int:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def handle():
        conn, _ = server.accept()
        conn.recv(65536)
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
