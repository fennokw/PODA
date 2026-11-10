"""PODA-owned llama.cpp inference, with private loopback workers and no external model service."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
from threading import Event, Lock
import time
from typing import Any, AsyncIterator, Iterator

import httpx

from . import config, llama_install, model_catalog
from .db import get_setting

_DEFAULTS = {"backend": "auto", "cache_k": "f16", "cache_v": "f16", "flash_attention": "auto",
             "speculation": "ngram-simple", "context_limit": 32768, "gpu_layers": -1}


class InferenceError(RuntimeError):
    pass


def settings() -> dict:
    try:
        saved = json.loads(get_setting("llama_settings", "{}"))
        return _DEFAULTS | saved if isinstance(saved, dict) else dict(_DEFAULTS)
    except (OSError, ValueError, sqlite3.Error):
        return dict(_DEFAULTS)


def installed_models(timeout: float = 0) -> list[str]:
    return [item["id"] for item in model_catalog.inventory()]


def tags(timeout: float = 0) -> dict:
    return {"models": [model_catalog.summary(item) | {"name": item["id"]} for item in model_catalog.inventory()]}


def status() -> dict:
    setup = settings()
    backend = setup["backend"]
    binary = llama_install.executable(backend)
    return {"reachable": binary is not None, "models": installed_models(), "version": llama_install.RELEASE if binary else None,
            "provider": "llama.cpp", "backend": backend,
            "error": None if binary else "Install the local llama.cpp runtime in System."}


def model_supports_tools(model: str) -> bool:
    return bool((model_catalog.installed(model) or {}).get("tools"))


def vision_model(preferred: str) -> str | None:
    assigned = get_setting("model_profile_vision")
    candidates = [item["id"] for item in model_catalog.inventory() if item.get("vision")]
    return next((item for item in [assigned, preferred, *candidates] if item in candidates), None)


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@dataclass
class Worker:
    model: str
    context: int
    signature: tuple
    speculation: str
    process: subprocess.Popen
    temporary: tempfile.TemporaryDirectory
    url: str
    token: str

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def close(self) -> None:
        if self.process.poll() is None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.temporary.cleanup()


class Slot:
    def __init__(self, embedding: bool = False):
        self.embedding = embedding
        self.lock = Lock()
        self.worker: Worker | None = None

    def stop(self) -> None:
        if self.worker:
            self.worker.close()
            self.worker = None

    def acquire(self, model: str, context: int, stop: Event | None = None) -> Worker:
        deadline = time.monotonic() + 240
        while not self.lock.acquire(timeout=.2):
            if (stop and stop.is_set()) or time.monotonic() >= deadline:
                raise InferenceError("The local model is busy or the request was stopped.")
        try:
            if stop and stop.is_set():
                raise InferenceError("Inference stopped.")
            return self._ready(model, context, stop)
        except BaseException:
            self.lock.release()
            raise

    def _ready(self, model: str, context: int, stop: Event | None) -> Worker:
        item = model_catalog.installed(model)
        if not item:
            raise InferenceError(f"Model {model} is missing. Download or import a GGUF in System.")
        if self.embedding != (item["role"] == "embedding"):
            raise InferenceError("The selected model cannot perform this kind of inference.")
        preferences = settings() if not self.embedding else _DEFAULTS
        context = min(preferences["context_limit"], max(1024, int(context)))
        backend = preferences["backend"]
        spec = "none" if self.embedding or item.get("vision") else preferences["speculation"]
        signature = (model, backend, spec, preferences["cache_k"], preferences["cache_v"],
                     preferences["flash_attention"], preferences["gpu_layers"], preferences["context_limit"])
        if (self.worker and self.worker.signature == signature and self.worker.context >= context
                and self.worker.process.poll() is None):
            return self.worker
        self.stop()
        binary = llama_install.executable(backend)
        if not binary:
            raise InferenceError("Install PODA’s llama.cpp runtime in System before using a model.")
        try:
            worker = self._spawn(binary, item, context, signature, spec, preferences, stop)
        except InferenceError:
            if spec == "none" or (stop and stop.is_set()):
                raise
            # Some GGUF architectures lack a compatible speculative path.
            worker = self._spawn(binary, item, context, signature, "none", preferences, stop)
        self.worker = worker
        return worker

    def _spawn(self, binary: Path, item: dict, context: int, signature: tuple, spec: str,
               preferences: dict, stop: Event | None) -> Worker:
        config.ensure_dirs()
        temporary = tempfile.TemporaryDirectory(prefix="poda-llama-", dir=config.DATA_DIR)
        key_path = Path(temporary.name) / "key"
        token = secrets.token_urlsafe(32)
        with os.fdopen(os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as key:
            key.write(token + "\n")
        port = _port()
        gpu = 0 if self.embedding or preferences["backend"] == "cpu" else preferences["gpu_layers"]
        args = [sys.executable, str(Path(__file__).with_name("llama_worker.py")), str(binary), "--host", "127.0.0.1", "--port", str(port), "--api-key-file", str(key_path),
                "--model", str(item["path"]), "--alias", item["id"], "--ctx-size", str(context), "--parallel", "1",
                "--no-webui", "--n-gpu-layers", "all" if gpu == -1 else str(gpu)]
        if self.embedding:
            args += ["--embedding", "--pooling", "mean", "--batch-size", str(context), "--ubatch-size", str(context)]
        else:
            args += ["--jinja", "--metrics", "--flash-attn", preferences["flash_attention"],
                     "--cache-type-k", preferences["cache_k"], "--cache-type-v", preferences["cache_v"],
                     "--spec-type", spec]
            if spec == "ngram-simple":
                args += ["--spec-ngram-simple-size-n", "6", "--spec-ngram-simple-size-m", "12"]
            if item.get("projector"):
                args += ["--mmproj", str(item["projector"])]
        env = {key: os.environ[key] for key in ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR",
                                               "TEMP", "TMP", "TMPDIR", "LANG", "LOCALAPPDATA") if key in os.environ}
        try:
            # Keep prompts and tool arguments out of engine logs.
            process = subprocess.Popen(args, cwd=binary.parent, env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if config.WINDOWS else 0)
        except OSError as exc:
            temporary.cleanup()
            raise InferenceError("PODA could not launch the packaged llama.cpp binary.") from exc
        worker = Worker(item["id"], context, signature, spec, process, temporary, f"http://127.0.0.1:{port}", token)
        try:
            deadline = time.monotonic() + 150
            with httpx.Client(trust_env=False, timeout=1) as client:
                while time.monotonic() < deadline:
                    if stop and stop.is_set():
                        raise InferenceError("Inference stopped while the model was loading.")
                    if process.poll() is not None:
                        raise InferenceError("llama.cpp could not load this GGUF with the selected settings. "
                                             "Check the model and projector, or lower the context and GPU layers in System.")
                    try:
                        if client.get(worker.url + "/health", headers=worker.headers).is_success:
                            return worker
                    except httpx.TransportError:
                        pass
                    time.sleep(.2)
            raise InferenceError("llama.cpp did not finish loading the model. Try a smaller GGUF or context.")
        except BaseException:
            worker.close()
            raise

    @contextmanager
    def lease(self, model: str, context: int, stop: Event | None = None):
        worker = self.acquire(model, context, stop)
        try:
            yield worker
        finally:
            self.lock.release()


_chat = Slot()
_embed = Slot(embedding=True)


def ps() -> list[dict]:
    return [{"name": worker.model, "context_length": worker.context, "speculation": worker.speculation}
            for slot in (_chat, _embed) if (worker := slot.worker) and worker.process.poll() is None]


def shutdown() -> None:
    from . import inference_bridge
    inference_bridge.stop()
    for slot in (_chat, _embed):
        slot.stop()


def _messages(messages: list[dict]) -> list[dict]:
    pending = deque()
    output = []
    for message in messages:
        current = dict(message)
        if images := current.pop("images", None):
            current["content"] = [{"type": "text", "text": current.get("content") or ""},
                                  *({"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}}
                                    for image in images)]
        if calls := current.get("tool_calls"):
            converted = []
            for call in calls:
                function = dict(call.get("function") or {})
                args = function.get("arguments")
                function["arguments"] = args if isinstance(args, str) else json.dumps(args or {})
                identifier = call.get("id") or "call_" + secrets.token_hex(6)
                pending.append(identifier)
                converted.append({"id": identifier, "type": "function", "function": function})
            current["tool_calls"] = converted
        if current.get("role") == "tool" and pending:
            identifier = pending.popleft()
            current.setdefault("tool_call_id", identifier)
        output.append(current)
    return output


def _payload(messages: list[dict], model: str, options: dict, tools: list | None,
             think: bool | None, format: str | dict | None, streaming: bool) -> dict:
    payload = {"messages": _messages(messages), "model": model, "stream": streaming,
               "temperature": options.get("temperature", .1), "max_tokens": options.get("num_predict", 1600)}
    if tools:
        payload["tools"] = tools
    if think is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": think}
        if not think:
            payload["reasoning_effort"] = "none"
        elif budget := options.get("reasoning_budget"):
            payload["reasoning_budget_tokens"] = budget
    if format:
        payload["response_format"] = {"type": "json_object", **({"schema": format} if isinstance(format, dict) else {})}
    if streaming:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _raise(response: httpx.Response) -> None:
    if not response.is_success:
        try:
            detail = response.json().get("error", {})
            if isinstance(detail, dict):
                detail = detail.get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        raise InferenceError(f"llama.cpp HTTP {response.status_code}. {str(detail)[:240]}".strip())


def _translate(chunk: dict, *, stream: bool) -> dict:
    choice = (chunk.get("choices") or [{}])[0]
    message = dict(choice.get("delta" if stream else "message") or {})
    if reasoning := message.pop("reasoning_content", None):
        message["thinking"] = reasoning
    calls = []
    for call in message.get("tool_calls") or []:
        function = dict(call.get("function") or {})
        if not stream and isinstance(function.get("arguments"), str):
            try:
                function["arguments"] = json.loads(function["arguments"])
            except ValueError:
                pass
        calls.append({**call, "function": function})
    if calls:
        message["tool_calls"] = calls
    timings = chunk.get("timings") or {}
    usage = chunk.get("usage") or {}
    return {"message": message, "done_reason": choice.get("finish_reason"),
            "eval_count": timings.get("predicted_n", usage.get("completion_tokens", 0)),
            "eval_duration": int((timings.get("predicted_ms") or 0) * 1e6),
            "prompt_eval_count": timings.get("prompt_n", usage.get("prompt_tokens", 0)),
            "prompt_eval_duration": int((timings.get("prompt_ms") or 0) * 1e6),
            "cached_prompt_tokens": timings.get("cache_n", 0)}


def _chunk(line: str) -> dict | None:
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return {"done": True}
    if not data:
        return None
    value = json.loads(data)
    if value.get("error"):
        raise InferenceError("llama.cpp stopped before completing the response.")
    return _translate(value, stream=True)


def _complete_sync(payload: dict, worker: Worker, timeout: float, max_elapsed=None, stop=None) -> Iterator[dict]:
    try:
        with httpx.Client(trust_env=False, timeout=httpx.Timeout(timeout, connect=5)) as client:
            if not payload["stream"]:
                response = client.post(worker.url + "/v1/chat/completions", json=payload, headers=worker.headers)
                _raise(response)
                yield _translate(response.json(), stream=False)
                return
            began = time.monotonic()
            with client.stream("POST", worker.url + "/v1/chat/completions", json=payload, headers=worker.headers) as response:
                if not response.is_success:
                    response.read()
                    _raise(response)
                for line in response.iter_lines():
                    if stop and stop.is_set():
                        raise InferenceError("Tool planning stopped.")
                    if max_elapsed and time.monotonic() - began >= max_elapsed:
                        raise InferenceError("Tool planning reached its time limit.")
                    if part := _chunk(line):
                        yield part
                        if part.get("done"):
                            return
            raise InferenceError("llama.cpp closed the response before completing it.")
    except (httpx.HTTPError, ValueError) as exc:
        raise InferenceError("The local llama.cpp worker stopped responding or returned invalid data.") from exc


def chat(messages: list[dict], model: str, options: dict | None = None, tools=None,
         timeout: float = 600, think=None, format=None, *, streaming=False, max_elapsed=None, stop=None) -> dict:
    options = options or {}
    payload = _payload(messages, model, options, tools, think, format, streaming)
    with _chat.lease(model, options.get("num_ctx", 8192), stop) as worker:
        pieces = _complete_sync(payload, worker, timeout, max_elapsed, stop)
        if not streaming:
            return next(pieces)
        text = []
        calls: dict[int, dict] = {}
        final = {}
        for part in pieces:
            if part.get("done"):
                break
            final.update({key: value for key, value in part.items() if key != "message" and value})
            message = part["message"]
            text.append(message.get("content") or "")
            for index, call in enumerate(message.get("tool_calls") or []):
                assembled = calls.setdefault(call.get("index", index), {"type": "function", "function": {"name": "", "arguments": ""}})
                if call.get("id"):
                    assembled["id"] = call["id"]
                for key in ("name", "arguments"):
                    assembled["function"][key] += call["function"].get(key) or ""
        for call in calls.values():
            args = call["function"]["arguments"]
            try:
                call["function"]["arguments"] = json.loads(args) if args else {}
            except ValueError:
                pass
        return {**final, "message": {"role": "assistant", "content": "".join(text), "tool_calls": list(calls.values())}}


async def chat_stream(messages: list[dict], model: str, options: dict | None = None, tools=None,
                      think=None, timeout: float = 900, format=None) -> AsyncIterator[dict]:
    options = options or {}
    stop = Event()
    load = asyncio.create_task(asyncio.to_thread(_chat.acquire, model, options.get("num_ctx", 8192), stop))
    try:
        worker = await asyncio.shield(load)
    except asyncio.CancelledError:
        stop.set()

        def release_if_acquired(task):
            if not task.cancelled() and task.exception() is None:
                _chat.lock.release()

        load.add_done_callback(release_if_acquired)
        raise
    try:
        payload = _payload(messages, model, options, tools, think, format, True)
        terminal = {}
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(timeout, connect=5)) as client:
            async with client.stream("POST", worker.url + "/v1/chat/completions", json=payload, headers=worker.headers) as response:
                if not response.is_success:
                    await response.aread()
                    _raise(response)
                async for line in response.aiter_lines():
                    if not (part := _chunk(line)):
                        continue
                    if part.get("done"):
                        yield {**terminal, "message": {}, "done": True, "speculation": worker.speculation}
                        return
                    if part.get("done_reason"):
                        terminal["done_reason"] = part["done_reason"]
                    terminal.update({key: value for key, value in part.items() if key != "message" and value})
                    if part["message"]:
                        yield part
        raise InferenceError("llama.cpp ended the response before completing it.")
    except httpx.ReadTimeout as exc:
        raise InferenceError("llama.cpp stopped sending progress. Try a smaller model or context.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise InferenceError("PODA lost its connection to the local llama.cpp worker.") from exc
    finally:
        _chat.lock.release()


def embed(texts: list[str], model: str | None = None, timeout: float = 180, *, purpose: str = "search_document") -> list[list[float]]:
    if not texts:
        return []
    name = model or config.EMBED_MODEL
    try:
        with _embed.lease(name, 8192) as worker, httpx.Client(trust_env=False, timeout=timeout) as client:
            response = client.post(worker.url + "/v1/embeddings", headers=worker.headers, json={
                "model": name, "input": [f"{purpose}: {text[:16000]}" for text in texts],
                "encoding_format": "float", "truncate": True,
            })
            _raise(response)
            values = sorted(response.json()["data"], key=lambda item: item["index"])
            if len(values) != len(texts):
                raise InferenceError("llama.cpp returned the wrong number of embeddings.")
            return [entry["embedding"] for entry in values]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise InferenceError("The local embedding worker could not process this text.") from exc


def _spec_metrics(worker: Worker) -> dict[str, int]:
    prefix = "llamacpp:spec_decode_num_"
    try:
        response = httpx.get(worker.url + "/metrics", headers=worker.headers, timeout=2, trust_env=False)
        return {name.removeprefix(prefix).removesuffix("_total"): int(float(value))
                for name, value in (line.split() for line in response.text.splitlines() if line.startswith(prefix))
                if "{" not in name}
    except (httpx.HTTPError, ValueError):
        return {}


def benchmark(model: str, num_ctx: int, prompt: str | None = None, num_predict: int = 200) -> dict[str, Any]:
    began = time.perf_counter()
    payload = _payload([{"role": "user", "content": prompt or "Explain hash collisions in three bullets."}],
                       model, {"num_predict": num_predict}, None, False, None, True)
    with _chat.lease(model, num_ctx) as worker:
        loaded = time.perf_counter()
        before = _spec_metrics(worker)
        first = None
        final = {}
        for part in _complete_sync(payload, worker, 120):
            if first is None and part.get("message", {}).get("content"):
                first = time.perf_counter()
            final.update({k: v for k, v in part.items() if v})
        after = _spec_metrics(worker)
        count = final.get("eval_count") or 0
        ns = final.get("eval_duration") or 0
        prompt_count = final.get("prompt_eval_count") or 0
        prompt_ns = final.get("prompt_eval_duration") or 0
        return {"model": model, "num_ctx": worker.context, "load_s": round(loaded - began, 2),
                "first_token_s": round(first - began, 2) if first else None, "total_s": round(time.perf_counter() - began, 2),
                "gen_tok_s": round(count * 1e9 / ns, 1) if ns else None,
                "prompt_tok_s": round(prompt_count * 1e9 / prompt_ns, 1) if prompt_ns else None,
                "eval_tokens": count, "memory_bytes": None, "context_reported": worker.context,
                "speculation": worker.speculation,
                "speculative_tokens": {key: max(0, value - before.get(key, 0)) for key, value in after.items()}}
