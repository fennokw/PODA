"""Authenticated OpenAI-compatible loopback bridge for PODA's managed Hindsight process."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from threading import Lock, Thread

import httpx

from . import config, inference

_lock = Lock()
_server: ThreadingHTTPServer | None = None
_token = ""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _reply(self, status: int, value: dict):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.connection.settimeout(15)
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + _token):
            self._reply(401, {"error": {"message": "Private PODA inference endpoint."}})
            return
        if self.path not in {"/v1/chat/completions", "/v1/embeddings"}:
            self._reply(404, {"error": {"message": "Unknown inference endpoint."}})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2 * 1024 * 1024:
                raise ValueError("Invalid request size.")
            body = json.loads(self.rfile.read(size))
            if self.path.endswith("/embeddings"):
                if body.get("model") != config.EMBED_MODEL:
                    raise ValueError("Unknown embedding model.")
                text = body["input"]
                if isinstance(text, str):
                    text = [text]
                if not isinstance(text, list) or not all(isinstance(item, str) for item in text):
                    raise ValueError("Embedding inputs must be text.")
                values = inference.embed(text)
                self._reply(200, {"object": "list", "model": config.EMBED_MODEL,
                                  "data": [{"object": "embedding", "index": index, "embedding": value}
                                           for index, value in enumerate(values)],
                                  "usage": {"prompt_tokens": 0, "total_tokens": 0}})
                return
            messages = body["messages"]
            if not isinstance(messages, list) or any(
                not isinstance(message.get("content"), (str, type(None))) for message in messages
            ):
                raise ValueError("The memory service accepts text messages only.")
            payload = {key: body[key] for key in ("messages", "model", "tools", "tool_choice", "temperature",
                                                  "response_format", "max_tokens") if key in body}
            payload.update({"stream": False, "reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}})
            with inference._chat.lease(body["model"], 8192) as worker, httpx.Client(trust_env=False, timeout=240) as client:
                response = client.post(worker.url + self.path, headers=worker.headers, json=payload)
                self._reply(response.status_code, response.json())
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            self._reply(400, {"error": {"message": str(exc)[:240]}})
        except (httpx.HTTPError, inference.InferenceError):
            self._reply(502, {"error": {"message": "The local llama.cpp model could not process this memory request."}})


def start() -> tuple[str, str]:
    global _server, _token
    with _lock:
        if _server is None:
            _token = secrets.token_urlsafe(32)
            _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            Thread(target=_server.serve_forever, name="poda-memory-inference", daemon=True).start()
        return f"http://127.0.0.1:{_server.server_port}", _token


def token_for(base: str) -> str:
    if _server and base == f"http://127.0.0.1:{_server.server_port}":
        return _token
    return "poda-local"


def stop() -> None:
    global _server, _token
    with _lock:
        if _server:
            _server.shutdown()
            _server.server_close()
            _server = None
        _token = ""
