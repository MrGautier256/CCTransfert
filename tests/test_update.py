"""Mise à jour automatique, contre un faux GitHub local (aucun accès réseau)."""

import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cctransfert import update as up

NEW = b"nouvelle version de l'executable\n"


@pytest.fixture
def fake_github(monkeypatch):
    state = {"version": "9.9.0", "bad_sum": False, "calls": 0}
    exe_name = "CCTransfert-9.9.0-windows-x64.exe" if os.name == "nt" else "CCTransfert-9.9.0-linux-x86_64"

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            base = "http://127.0.0.1:{}".format(self.server.server_address[1])
            if self.path == "/repos/moi/cct/releases/latest":
                state["calls"] += 1
                names = [exe_name, "cctransfert-9.9.0-py3-none-any.whl", up.SUMS_NAME]
                body = json.dumps({"tag_name": "v" + state["version"], "html_url": base + "/rel",
                                   "body": "- nouveauté A\n- nouveauté B",
                                   "assets": [{"name": n, "browser_download_url": base + "/dl/" + n, "size": 1} for n in names]}).encode()
            elif self.path.startswith("/dl/"):
                name = self.path[4:]
                if name == up.SUMS_NAME:
                    digest = "0" * 64 if state["bad_sum"] else hashlib.sha256(NEW).hexdigest()
                    body = "{}  {}\n{}  cctransfert-9.9.0-py3-none-any.whl\n".format(digest, exe_name, "1" * 64).encode()
                else:
                    body = NEW
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(up, "API", "http://127.0.0.1:{}".format(httpd.server_address[1]))
    monkeypatch.setattr(up, "REPO", "moi/cct")
    yield state
    httpd.shutdown()


def test_detecte_une_nouvelle_version_et_respecte_le_cache(fake_github, monkeypatch):
    monkeypatch.setattr(up, "install_kind", lambda: "git")
    r = up.check(force=True)
    assert r["available"] and r["latest"]["version"] == "9.9.0" and not r["can_auto"]
    assert r["instructions"].startswith("git -C")
    calls = fake_github["calls"]
    up.check()  # vérification automatique : le cache d'un jour évite un nouvel appel
    assert fake_github["calls"] == calls
    up.save_state(skipped="9.9.0")
    assert up.check()["skipped"] is True
    up.save_state(auto_check=False)
    assert up.check()["latest"] is None  # désactivée : aucune vérification automatique
    fake_github["version"] = "0.0.1"
    assert up.check(force=True)["available"] is False


def test_installe_et_remplace_l_executable(fake_github, monkeypatch, tmp_path):
    exe = tmp_path / ("CCTransfert.exe" if os.name == "nt" else "CCTransfert")
    exe.write_bytes(b"ancienne version\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert up.install_kind() in ("exe", "binary")
    res = up.apply()
    assert res["version"] == "9.9.0" and res["restart"] == [str(exe), "gui"]
    assert exe.read_bytes() == NEW
    if os.name != "nt":
        assert os.access(str(exe), os.X_OK)


def test_refuse_un_fichier_dont_l_empreinte_ne_correspond_pas(fake_github, monkeypatch, tmp_path):
    exe = tmp_path / ("CCTransfert.exe" if os.name == "nt" else "CCTransfert")
    exe.write_bytes(b"ancienne version\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    fake_github["bad_sum"] = True
    with pytest.raises(up.UpdateError, match="sha256"):
        up.apply()
    assert exe.read_bytes() == b"ancienne version\n"  # rien n'a été remplacé


def test_comparaison_de_versions():
    assert up.is_newer("v1.10.0", "1.9.9") and not up.is_newer("1.0.0", "1.0.0")
    assert up.parse_version("v2.3.4-beta") == (2, 3, 4) and up.parse_version("n/a") is None
