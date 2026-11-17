import http.client
import json
import threading
import time
from urllib.parse import quote

import pytest
from conftest import PASS

from dualai import cli
from dualai.web.server import make_server
from dualai.web.service import Service


@pytest.fixture
def web(tmp_path):
    service = Service(tmp_path / "ui.json", fake_delay_s=0)
    server = make_server(service, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()
    service.shutdown()


def call(server, method, path, body=None, token=True, headers=None):
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    send = {"Host": f"127.0.0.1:{port}"}
    if token:
        send["X-Dualai-Token"] = server.token if token is True else token
    payload = None
    if body is not None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        send["Content-Type"] = "application/json"
    send.update(headers or {})
    connection.request(method, path, body=payload, headers=send)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw.decode("utf-8", "replace")
    return response.status, data, {name.lower(): value for name, value in response.getheaders()}


def test_page_carries_the_session_token_and_a_strict_policy(web):
    status, page, headers = call(web, "GET", "/", token=False)
    assert status == 200 and web.token in page and "__DUALAI_TOKEN__" not in page
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store" and headers["x-content-type-options"] == "nosniff"


def test_api_needs_the_session_token(web):
    assert call(web, "GET", "/api/state", token=False)[0] == 401
    assert call(web, "GET", "/api/state", token="errado")[0] == 401
    assert call(web, "GET", "/api/state")[0] == 200


def test_requests_for_another_host_are_refused(web):
    for path in ("/", "/api/state"):
        assert call(web, "GET", path, headers={"Host": "evil.example"})[0] == 403


def test_writes_from_another_origin_are_refused(web):
    port = web.server_address[1]
    assert call(web, "POST", "/api/runs/interrupt", body={}, headers={"Origin": "http://evil.example"})[0] == 403
    status, data, _ = call(web, "POST", "/api/runs/interrupt", body={}, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert (status, data) == (200, {"interrupting": False})


def test_only_listed_static_files_are_served(web):
    status, _, headers = call(web, "GET", "/static/app.css", token=False)
    assert status == 200 and headers["content-type"].startswith("text/css")
    assert call(web, "GET", "/static/app.js", token=False)[0] == 200
    for path in ("/static/../service.py", "/static/server.py", "/static/", "/outra"):
        assert call(web, "GET", path, token=False)[0] == 404


def test_unknown_routes_and_bad_bodies_are_plain_errors(web):
    assert call(web, "GET", "/api/nada")[0] == 404
    assert call(web, "POST", "/api/nada", body={})[0] == 404
    status, data, _ = call(web, "POST", "/api/repos", body=b"{nao e json")
    assert status == 400 and "error" in data
    status, data, _ = call(web, "POST", "/api/runs", body={"repo": "C:/nao/selecionado", "task": "t", "fake": True})
    assert status == 400 and "selecionado" in data["error"]


def test_whole_flow_over_http(web, repo):
    path = str(repo)
    assert call(web, "POST", "/api/repos", body={"path": path})[1]["current"]["name"] == "meu repo"
    assert call(web, "POST", "/api/repos/settings", body={"repo": path, "test_cmd": PASS})[1]["current"]["test_cmd"] == PASS
    status, started, _ = call(web, "POST", "/api/runs", body={"repo": path, "task": "Tarefa pela página", "fake": True})
    assert status == 200
    run_url = f"/api/runs/{started['run_id']}?repo={quote(path)}"
    deadline = time.monotonic() + 30
    detail = call(web, "GET", run_url)[1]
    while detail["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        detail = call(web, "GET", run_url)[1]
    assert (detail["status"], detail["can_decide"]) == ("done", True)
    assert call(web, "GET", f"/api/runs?repo={quote(path)}")[1]["runs"][0]["id"] == started["run_id"]
    status, decided, _ = call(web, "POST", f"/api/runs/{started['run_id']}/approve", body={"repo": path})
    assert (status, decided["decision"]) == (200, "approved") and (repo / "DUALAI_FAKE.txt").exists()


def test_ui_command_passes_its_options_to_the_server(monkeypatch, tmp_path):
    seen = {}

    def serve(port, open_browser, config_path):
        seen.update(port=port, open_browser=open_browser, config_path=config_path)
        return 0

    monkeypatch.setattr(cli, "serve", serve)
    assert cli.main(["ui", "--port", "9999", "--no-browser", "--config", str(tmp_path / "x.json")]) == 0
    assert seen == {"port": 9999, "open_browser": False, "config_path": tmp_path / "x.json"}
    assert cli.main(["ui"]) == 0 and (seen["port"], seen["open_browser"], seen["config_path"]) == (8765, True, None)
