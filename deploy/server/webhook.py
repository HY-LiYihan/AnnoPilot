#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import shlex
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Dict
from urllib.parse import urlparse


DEPLOY_LOCK = threading.Lock()
MAX_BODY_BYTES = int(os.getenv("ROSETTA_MAX_BODY_BYTES", "65536"))


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def json_bytes(payload: Dict[str, Any], status: str) -> bytes:
    return json.dumps({"status": status, **payload}, ensure_ascii=False).encode("utf-8")


def tail(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


class DeployHandler(BaseHTTPRequestHandler):
    server_version = "RosettaDeployWebhook/1.0"

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/healthz":
            self.respond(200, {"ok": True}, "ok")
            return
        self.respond(404, {"error": "not_found"}, "error")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/deploy/rosetta":
            self.respond(404, {"error": "not_found"}, "error")
            return

        try:
            body = self.read_body()
            self.verify_signature(body)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON payload must be an object")
            self.verify_payload(payload)
        except Exception as exc:  # noqa: BLE001 - return explicit webhook failure reason.
            self.respond(400, {"error": str(exc)}, "error")
            return

        if not DEPLOY_LOCK.acquire(blocking=False):
            self.respond(409, {"error": "deployment_already_running"}, "error")
            return

        try:
            result = self.run_deploy(payload)
        except subprocess.TimeoutExpired as exc:
            self.respond(
                504,
                {
                    "error": "deployment_timeout",
                    "stdout": tail(str(exc.stdout or "")),
                    "stderr": tail(str(exc.stderr or "")),
                },
                "error",
            )
            return
        except Exception as exc:  # noqa: BLE001 - convert webhook runtime failures to JSON.
            self.respond(500, {"error": str(exc)}, "error")
            return
        finally:
            DEPLOY_LOCK.release()

        response = {
            "returncode": result.returncode,
            "stdout": tail(result.stdout),
            "stderr": tail(result.stderr),
        }
        if result.returncode == 0:
            self.respond(200, response, "ok")
        else:
            self.respond(500, response, "error")

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("missing request body")
        if length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        return self.rfile.read(length)

    def verify_signature(self, body: bytes) -> None:
        secret = env("ROSETTA_WEBHOOK_SECRET")
        if not secret:
            raise ValueError("server missing ROSETTA_WEBHOOK_SECRET")

        provided = self.headers.get("X-Rosetta-Signature", "").strip()
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        expected_header = f"sha256={expected}"
        if not hmac.compare_digest(provided, expected_header):
            raise ValueError("invalid signature")

    def verify_payload(self, payload: Dict[str, Any]) -> None:
        allowed_repo = env("ROSETTA_ALLOWED_REPOSITORY")
        if allowed_repo:
            repository = str(payload.get("repository", ""))
            if repository.lower() != allowed_repo.lower():
                raise ValueError("repository is not allowed")

        allowed_ref = env("ROSETTA_ALLOWED_REF", "main")
        if allowed_ref:
            ref = str(payload.get("ref", ""))
            allowed_refs = {allowed_ref, f"refs/heads/{allowed_ref}"}
            if ref not in allowed_refs:
                raise ValueError("ref is not allowed")

        mode = str(payload.get("mode", "image"))
        if mode not in {"image", "source"}:
            raise ValueError("mode must be image or source")

        max_age = int(env("ROSETTA_MAX_PAYLOAD_AGE_SECONDS", "900"))
        if max_age > 0:
            issued_at = int(payload.get("issued_at", 0))
            now = int(time.time())
            if issued_at <= 0 or issued_at > now + 60 or now - issued_at > max_age:
                raise ValueError("payload timestamp is outside the allowed window")

    def run_deploy(self, payload: Dict[str, Any]) -> subprocess.CompletedProcess:
        command = shlex.split(env("ROSETTA_DEPLOY_COMMAND", "/opt/rosetta/bin/deploy.sh"))
        if not command:
            raise ValueError("empty ROSETTA_DEPLOY_COMMAND")

        deploy_env = os.environ.copy()
        deploy_env["ROSETTA_DEPLOY_MODE"] = str(payload.get("mode", env("ROSETTA_DEPLOY_MODE", "image")))
        deploy_env["ROSETTA_DEPLOY_SHA"] = str(payload.get("sha", ""))
        deploy_env["ROSETTA_DEPLOY_REF"] = str(payload.get("ref", ""))
        deploy_env["ROSETTA_DEPLOY_REPOSITORY"] = str(payload.get("repository", ""))

        timeout = int(env("ROSETTA_DEPLOY_TIMEOUT_SECONDS", "900"))
        return subprocess.run(
            command,
            env=deploy_env,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )

    def respond(self, status_code: int, payload: Dict[str, Any], status: str) -> None:
        body = json_bytes(payload, status)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib hook name.
        print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {self.client_address[0]} {format % args}", flush=True)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main() -> None:
    host = env("ROSETTA_WEBHOOK_HOST", "127.0.0.1")
    port = int(env("ROSETTA_WEBHOOK_PORT", "9010"))
    server = ThreadingHTTPServer((host, port), DeployHandler)
    print(f"Rosetta deploy webhook listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
