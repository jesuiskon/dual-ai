"""Servidor HTTP local da interface. Só biblioteca padrão.

Escuta apenas em 127.0.0.1. Toda rota /api exige o token da sessão; o
cabeçalho Host é conferido em toda requisição e o Origin em toda escrita,
para que nenhuma outra página aberta no navegador consiga ler o token ou
disparar ações.
"""

from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from dualai.actions import ActionError
from dualai.web.service import Service, ServiceError
from dualai.workspace import WorkspaceError

DEFAULT_PORT = 8765
STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "icon.svg": "image/svg+xml",
}
TOKEN_PLACEHOLDER = "__DUALAI_TOKEN__"
TOKEN_HEADER = "X-Dualai-Token"
MAX_BODY_BYTES = 1_000_000
PAGE_POLICY = "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False  # no Windows, reutilizar deixaria duas instâncias na mesma porta
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "DualAI"

    def log_message(self, format, *args):  # noqa: A002 - assinatura da classe base
        pass

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    # --- respostas -----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # --- entrada -------------------------------------------------------------

    def _handle(self, method: str) -> None:
        server = self.server
        if self.headers.get("Host", "") not in server.allowed_hosts:
            return self._error(403, "origem não permitida")
        url = urlsplit(self.path)
        try:
            if not url.path.startswith("/api/"):
                return self._static(method, url.path)
            given = self.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(given.encode("utf-8"), server.token.encode("utf-8")):
                return self._error(401, "sessão inválida; recarregue a página")
            body = {}
            if method == "POST":
                origin = self.headers.get("Origin")
                if origin is not None and origin not in server.allowed_origins:
                    return self._error(403, "origem não permitida")
                body = self._body()
            query = {name: values[0] for name, values in parse_qs(url.query).items()}
            result = self._route(method, url.path, query, body)
            if result is None:
                return self._error(404, "rota não encontrada")
            self._json(200, result)
        except ServiceError as error:
            self._error(error.status, str(error))
        except (ActionError, WorkspaceError) as error:
            self._error(400, str(error))
        except Exception as error:  # nunca devolver detalhes internos ao navegador
            print(f"erro interno: {type(error).__name__}: {error}", file=sys.stderr)
            self._error(500, "erro interno do Dual AI; veja o terminal")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ServiceError(400, "requisição inválida") from None
        if length > MAX_BODY_BYTES:
            raise ServiceError(413, "requisição grande demais")
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise ServiceError(400, "requisição inválida: o corpo não é JSON") from None
        if not isinstance(data, dict):
            raise ServiceError(400, "requisição inválida: o corpo precisa ser um objeto")
        return data

    def _route(self, method: str, path: str, query: dict, body: dict):
        service: Service = self.server.service
        parts = [unquote(part) for part in path.split("/")[2:]]
        if method == "GET":
            if parts == ["state"]:
                return service.state()
            if parts == ["runs"]:
                return {"runs": service.list_runs(query.get("repo"))}
            if len(parts) == 2 and parts[0] == "runs":
                return service.run_detail(query.get("repo"), parts[1])
            return None
        if parts == ["repos"]:
            return service.select_repo(body.get("path"))
        if parts == ["repos", "pick"]:
            return service.pick_repo()
        if parts == ["repos", "settings"]:
            return service.save_settings(body.get("repo"), body.get("test_cmd"))
        if parts == ["runs"]:
            return service.start_run(body.get("repo"), body.get("task"), fake=body.get("fake") is True)
        if parts == ["runs", "interrupt"]:
            return service.interrupt()
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "approve":
            return service.approve(body.get("repo"), parts[1])
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "discard":
            return service.discard(body.get("repo"), parts[1])
        return None

    def _static(self, method: str, path: str) -> None:
        if method == "GET" and path == "/":
            page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            body = page.replace(TOKEN_PLACEHOLDER, self.server.token).encode("utf-8")
            return self._send(200, body, "text/html; charset=utf-8", {"Content-Security-Policy": PAGE_POLICY})
        name = path[len("/static/"):] if path.startswith("/static/") else ""
        if method == "GET" and name in STATIC_FILES:
            return self._send(200, (STATIC_DIR / name).read_bytes(), STATIC_FILES[name])
        self._error(404, "não encontrado")


def make_server(service: Service, port: int = DEFAULT_PORT) -> LocalServer:
    server = LocalServer(("127.0.0.1", port), Handler)
    actual = server.server_address[1]
    server.service = service
    server.token = secrets.token_urlsafe(32)
    server.allowed_hosts = {f"127.0.0.1:{actual}", f"localhost:{actual}"}
    server.allowed_origins = {f"http://{host}" for host in server.allowed_hosts}
    return server


def serve(port: int = DEFAULT_PORT, open_browser: bool = True, config_path: Path | None = None) -> int:
    service = Service(config_path or Path.home() / ".dualai" / "ui.json")
    try:
        server = make_server(service, port)
    except OSError as error:
        print(f"erro: não foi possível abrir a porta {port} ({error}). "
              "O Dual AI já está aberto? Se não, use --port com outro número.", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Dual AI aberto em {url}")
    print("Fechar a aba do navegador não encerra nada. Para encerrar, pressione Ctrl+C nesta janela.")
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando o Dual AI…")
    finally:
        service.shutdown()
        server.server_close()
    return 0
