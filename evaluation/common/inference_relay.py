"""Root-owned, protocol-aware inference relay for isolated coding agents.

The agent can reach only this loopback HTTP server. The relay validates each
request, replaces dummy authentication with the real upstream credential, and
is the only process allowed to reach the external model gateway.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socketserver
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit


BUFFER_SIZE = 64 * 1024
MAX_REQUEST_BYTES = 128 * 1024 * 1024
ALLOWED_ENDPOINT_SUFFIXES = (
    "/responses",
    "/responses/compact",
    "/chat/completions",
    "/messages",
    "/messages/count_tokens",
)
PROHIBITED_TOOL_PREFIXES = (
    "web_search",
    "web_fetch",
    "computer_use",
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def log_event(message: str) -> None:
    print(message, flush=True)


def allowed_endpoint_paths(base_path: str) -> set[str]:
    normalized_base = base_path.rstrip("/")
    prefixes = {normalized_base}
    if not normalized_base:
        prefixes.add("/v1")
    return {
        prefix + suffix
        for prefix in prefixes
        for suffix in ALLOWED_ENDPOINT_SUFFIXES
    }


def endpoint_allowed(path: str, allowed_paths: set[str]) -> bool:
    request_path = urlsplit(path).path.rstrip("/")
    return request_path in allowed_paths


def _contains_prohibited_tool(value: object) -> bool:
    if isinstance(value, dict):
        tool_type = value.get("type")
        if isinstance(tool_type, str) and tool_type.lower().startswith(
            PROHIBITED_TOOL_PREFIXES
        ):
            return True
        name = value.get("name")
        if isinstance(name, str) and name.lower().startswith(
            PROHIBITED_TOOL_PREFIXES
        ):
            return True
        return any(_contains_prohibited_tool(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_prohibited_tool(item) for item in value)
    return False


def _contains_remote_resource(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key.lower() in {"url", "image_url", "file_url"}
                and isinstance(item, str)
                and item.lower().startswith(("http://", "https://"))
            ):
                return True
            if _contains_remote_resource(item):
                return True
    elif isinstance(value, list):
        return any(_contains_remote_resource(item) for item in value)
    return False


def validate_inference_body(body: bytes, allowed_models: set[str]) -> dict:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")

    model = payload.get("model")
    if model not in allowed_models:
        raise ValueError(f"model is not allowed: {model!r}")
    if _contains_prohibited_tool(payload.get("tools", [])):
        raise ValueError("provider-side network tools are not allowed")
    if _contains_remote_resource(payload):
        raise ValueError("provider-side remote resources are not allowed")
    return payload


def upstream_headers(
    incoming: object,
    *,
    upstream_host: str,
    api_key: str,
    auth_mode: str,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in incoming.items():
        lowered = name.lower()
        if lowered in HOP_BY_HOP_HEADERS or lowered in {
            "host",
            "content-length",
            "authorization",
            "x-api-key",
        }:
            continue
        headers[name] = value
    headers["Host"] = upstream_host
    if auth_mode == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_mode == "x-api-key":
        headers["x-api-key"] = api_key
    else:
        raise ValueError(f"unsupported auth mode: {auth_mode}")
    return headers


class InferenceRelayHandler(BaseHTTPRequestHandler):
    server: "ThreadingInferenceHTTPServer"
    protocol_version = "HTTP/1.1"

    def do_CONNECT(self) -> None:
        self._reject(405, "CONNECT is not supported")

    def do_GET(self) -> None:
        self._reject(405, "only POST inference requests are supported")

    def do_POST(self) -> None:
        if not endpoint_allowed(self.path, self.server.allowed_paths):
            self._reject(
                403,
                f"inference endpoint is not allowed: {urlsplit(self.path).path}",
            )
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reject(411, "a valid Content-Length is required")
            return
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._reject(413, "request body is too large")
            return
        body = self.rfile.read(content_length)
        try:
            validate_inference_body(body, self.server.allowed_models)
        except ValueError as exc:
            self._reject(403, str(exc))
            return

        upstream_path = self.path
        try:
            headers = upstream_headers(
                self.headers,
                upstream_host=self.server.upstream_host,
                api_key=self.server.api_key,
                auth_mode=self.server.auth_mode,
            )
            connection = http.client.HTTPSConnection(
                self.server.upstream_host,
                self.server.upstream_port,
                timeout=120,
                context=ssl.create_default_context(),
            )
            connection.request("POST", upstream_path, body=body, headers=headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            log_event(f"upstream error path={urlsplit(self.path).path} error={exc}")
            self._reject(502, "model gateway request failed")
            return

        request_path = urlsplit(self.path).path
        log_event(f"allow status={response.status} path={request_path}")
        self.send_response(response.status, response.reason)
        has_length = False
        for name, value in response.getheaders():
            lowered = name.lower()
            if lowered in HOP_BY_HOP_HEADERS:
                continue
            if lowered == "content-length":
                has_length = True
            self.send_header(name, value)
        if not has_length:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        try:
            while True:
                chunk = response.read1(BUFFER_SIZE)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError):
            pass
        finally:
            response.close()
            connection.close()

    def _reject(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        log_event(f"reject status={status} reason={message}")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:
        return


class ThreadingInferenceHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        upstream_url: str,
        api_key: str,
        auth_mode: str,
        allowed_models: set[str],
    ):
        parsed = urlsplit(upstream_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("upstream URL must use HTTPS")
        self.upstream_host = parsed.hostname
        self.upstream_port = parsed.port or 443
        self.api_key = api_key
        self.auth_mode = auth_mode
        self.allowed_models = allowed_models
        self.allowed_paths = allowed_endpoint_paths(parsed.path)
        super().__init__(address, InferenceRelayHandler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream-url", required=True)
    parser.add_argument("--auth-mode", choices=("bearer", "x-api-key"), required=True)
    parser.add_argument("--allow-model", action="append", required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()

    api_key = os.environ.pop("CODEMEM_RELAY_API_KEY", "")
    if not api_key:
        raise SystemExit("CODEMEM_RELAY_API_KEY is required")

    with ThreadingInferenceHTTPServer(
        (args.listen_host, args.listen_port),
        upstream_url=args.upstream_url,
        api_key=api_key,
        auth_mode=args.auth_mode,
        allowed_models=set(args.allow_model),
    ) as server:
        args.ready_file.write_text("ready\n")
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
