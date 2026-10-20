"""Checks the Ollama and OpenAI-compatible clients against tiny local mock servers."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from travel_planner.llm import OllamaClient, OpenAICompatClient


class _Handler(BaseHTTPRequestHandler):
    received: list = []

    def log_message(self, *args):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": "qwen2.5:7b"}]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.received.append((self.path, payload))
        if self.path == "/api/chat":
            self._send({"message": {"role": "assistant", "content": '{"ok": true}'}})
        elif self.path == "/v1/chat/completions":
            self._send({"choices": [{"message": {"role": "assistant", "content": '{"ok": 1}'}}]})


def _server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_ollama_client_round_trip():
    srv = _server()
    url = f"http://127.0.0.1:{srv.server_port}"
    client = OllamaClient("qwen2.5:7b", url, timeout=5, temperature=0.1)
    assert client.available()
    assert client.chat_json("sys", "user") == {"ok": True}
    path, payload = _Handler.received[-1]
    assert path == "/api/chat" and payload["format"] == "json" and payload["model"] == "qwen2.5:7b"
    srv.shutdown()


def test_openai_compatible_client_round_trip():
    srv = _server()
    client = OpenAICompatClient("llama3.1:8b", f"http://127.0.0.1:{srv.server_port}/v1", "x", 5, 0.1)
    assert client.chat_json("sys", "user") == {"ok": 1}
    path, payload = _Handler.received[-1]
    assert path == "/v1/chat/completions" and payload["response_format"] == {"type": "json_object"}
    srv.shutdown()


def test_ollama_reports_missing_model():
    srv = _server()
    client = OllamaClient("llama3.1:8b", f"http://127.0.0.1:{srv.server_port}", 5, 0.1)
    assert not client.available()     # server up, but model not pulled
    srv.shutdown()
