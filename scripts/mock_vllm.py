#!/usr/bin/env python3
"""Fake vLLM server for the offline lab (stdlib only).

Env: MOCK_ID (deployment label, echoed in replies), MOCK_MODEL.
Control: POST /admin/fail {"fail": true|false} toggles 503s for /v1 routes.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

MOCK_ID = os.environ.get("MOCK_ID", "mock-1")
MODEL = os.environ.get("MOCK_MODEL", "mock-model")
PORT = int(os.environ.get("MOCK_PORT", "8000"))
_state = {"fail": False, "n": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": MOCK_ID}]})
        elif self.path == "/metrics":
            self._send(200, (
                "# HELP mock mock metrics\n# TYPE mock counter\n"
                f'mock_requests_total{{model="{MODEL}"}} {_state["n"]}\n'
                "vllm:num_requests_running 0\n"
                "vllm:num_requests_waiting 0\n"
                "vllm:gpu_cache_usage_perc 0.15\n"
                "vllm:time_to_first_token_seconds_count 1\n"
            ), "text/plain")
        elif self.path == "/admin/fail":
            self._send(200, {"fail": _state["fail"]})
        else:
            self._send(200, {"ok": True, "mock": MOCK_ID})

    def do_POST(self):
        if self.path.startswith("/admin/"):
            body = self._json_body()
            _state["fail"] = bool(body.get("fail"))
            self._send(200, {"fail": _state["fail"]})
            return
        if _state["fail"]:
            self._send(503, {"error": {"message": f"{MOCK_ID} is down", "type": "server_error"}})
            return
        body = self._json_body()
        msgs = body.get("messages", [])
        prompt = msgs[-1].get("content", "") if msgs else ""
        prompt = prompt if isinstance(prompt, str) else str(prompt)
        _state["n"] += 1
        content = f"[{MOCK_ID}] you said: {prompt[:80]}"
        self._send(200, {
            "id": f"chatcmpl-mock-{_state['n']}",
            "object": "chat.completion",
            "model": body.get("model", MODEL),
            "created": 1700000000,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": max(1, len(prompt) // 4),
                      "completion_tokens": max(1, len(content) // 4),
                      "total_tokens": max(2, (len(prompt) + len(content)) // 4)},
        })


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
