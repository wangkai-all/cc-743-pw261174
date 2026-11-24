"""Dependency-free HTTP API and static file server."""

from __future__ import annotations

import json
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from . import store
from .model import StateError
from .verify import run_verification

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

_VERIFICATION_CACHE: Dict[str, Any] | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "AdmissionsMatcher/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter tests
        if self.headers.get("x-test-quiet"):
            return
        super().log_message(fmt, *args)

    # ---------- response helpers ----------
    def send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str, **extra: Any) -> None:
        self.send_json({"error": message, **extra}, status=status)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > 2_000_000:
            raise StateError("请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"JSON 解析失败：{exc}")
        if not isinstance(data, dict):
            raise StateError("请求体必须是 JSON 对象")
        return data

    # ---------- routing ----------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self.send_json({"ok": True})
            elif path == "/api/state":
                self.send_json(store.state_for_api(store.load_state()))
            elif path == "/api/rounds":
                state = store.load_state()
                self.send_json({"rounds": store.state_for_api(state)["rounds"]})
            elif path.startswith("/api/rounds/"):
                no = int(path.rsplit("/", 1)[-1])
                rec = next((r for r in store.load_state().get("rounds", []) if r["round_no"] == no), None)
                if not rec:
                    self.send_error_json(404, "轮次不存在")
                else:
                    self.send_json(rec)
            elif path == "/api/explain":
                cid = (query.get("candidate_id") or [""])[0]
                round_raw = (query.get("round") or [None])[0]
                round_no = int(round_raw) if round_raw else None
                if not cid:
                    raise StateError("缺少 candidate_id")
                self.send_json(store.explain(round_no, cid))
            elif path == "/api/verify":
                global _VERIFICATION_CACHE
                if _VERIFICATION_CACHE is None:
                    _VERIFICATION_CACHE = run_verification()
                self.send_json(_VERIFICATION_CACHE)
            else:
                self.serve_static(path)
        except StateError as exc:
            self.send_error_json(400, str(exc))
        except Exception as exc:  # pragma: no cover - HTTP boundary
            traceback.print_exc()
            self.send_error_json(500, "服务器内部错误", detail=str(exc))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            data = self.read_json()
            if path == "/api/preview":
                action = data.get("action") or data
                policy = data.get("policy")
                self.send_json(store.preview(action, policy))
            elif path == "/api/commit":
                action = data.get("action") or {}
                policy = data.get("policy") or "protect_promises"
                rec = store.commit(action, policy)
                self.send_json(rec, status=201)
            elif path == "/api/reset":
                scenario = data.get("scenario", "demo")
                state = store.reset_state(scenario)
                self.send_json(store.state_for_api(state), status=201)
            elif path == "/api/verify/run":
                report = run_verification(exhaustive_cases=int(data.get("cases", 24)))
                self.send_json(report)
            else:
                self.send_error_json(404, "未知 API")
        except StateError as exc:
            self.send_error_json(400, str(exc))
        except Exception as exc:  # pragma: no cover
            traceback.print_exc()
            self.send_error_json(500, "服务器内部错误", detail=str(exc))

    def serve_static(self, path: str) -> None:
        if path == "/" or path == "":
            file_path = STATIC_DIR / "index.html"
        else:
            rel = path.lstrip("/")
            # HTML references assets under /static/..., while STATIC_DIR itself
            # is the static directory.
            if rel.startswith("static/"):
                rel = rel[len("static/") :]
            file_path = (STATIC_DIR / rel).resolve()
            try:
                file_path.relative_to(STATIC_DIR.resolve())
            except ValueError:
                self.send_error_json(403, "禁止访问")
                return
        if not file_path.is_file():
            # Missing asset URLs should fail loudly instead of receiving the
            # HTML shell with a 200 response.  Non-asset paths still use the
            # single-page fallback.
            if path.startswith("/static/"):
                self.send_error_json(404, "静态资源不存在")
                return
            file_path = STATIC_DIR / "index.html"
        if not file_path.is_file():
            self.send_error_json(404, "static/index.html 不存在")
            return
        body = file_path.read_bytes()
        ctype = CONTENT_TYPES.get(file_path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = "0.0.0.0"
    port = 8000
    import os

    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Admissions matching app running at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
