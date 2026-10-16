"""API de l'interface graphique : sécurité (jeton, Host) et parcours export -> import -> undo."""

import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from conftest import SID, build_session
from cctransfert.gui.server import make_server
from cctransfert.profile import Profile
from cctransfert.store import find_session_files


@pytest.fixture
def gui(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cfg = home / ".claude"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CLAUDE_CODE_PROJECT_DIR_NAME", raising=False)
    prof = Profile("linux", str(home), str(cfg), "/tmp/claude-test", hostname="h", user="u")
    root = str(home / "Projets" / "MonProjet")
    os.makedirs(root)
    slug = build_session(prof, root)
    monkeypatch.chdir(root)
    httpd, app, url = make_server()
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = "/".join(url.split("/")[:3])  # http://127.0.0.1:<port>
    assert url.startswith(base + "/cctransfert/#t=")
    token = url.split("#t=")[1]

    def call(path, body=None, method=None, token_=token, host=None, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method or ("POST" if data is not None else "GET"))
        if token_:
            req.add_header("X-CCT-Token", token_)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if host:
            req.add_header("Host", host)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    yield {"call": call, "prof": prof, "home": home, "root": root, "slug": slug, "base": base}
    httpd.shutdown()
    httpd.server_close()
    app.cleanup()


def test_securite_jeton_et_host(gui):
    call = gui["call"]
    assert call("/api/sessions", token_=None)[0] == 403
    assert call("/api/sessions", token_="mauvais")[0] == 403
    assert call("/api/sessions", host="evil.example:80")[0] == 403
    assert call("/api/sessions")[0] == 200
    # la page elle-même est servie sans jeton (sous /cctransfert/), avec une CSP stricte
    with urllib.request.urlopen(gui["base"] + "/cctransfert/") as r:
        assert r.status == 200 and "script-src 'self'" in r.headers["Content-Security-Policy"]
    with urllib.request.urlopen(gui["base"] + "/cctransfert/app.js") as r:
        assert b"api(" in r.read()
    with urllib.request.urlopen(gui["base"] + "/cctransfert/icon-256.png") as r:
        assert r.headers["Content-Type"] == "image/png"


def test_parcours_export_import_undo(gui, tmp_path):
    call = gui["call"]
    st, sessions = call("/api/sessions")
    (s,) = sessions["sessions"]
    assert s["title"] == "Mon titre é" and s["root"] == gui["root"]
    st, det = call("/api/session?id=" + SID)
    assert st == 200 and det["messages"] > 0 and det["kinds"]["file-history"]["count"] == 1 and det["history"] == 1

    out_dir = str(tmp_path / "exports")
    st, ex = call("/api/export", {"id": SID, "out_dir": out_dir, "with_memory": True, "with_history": True})
    assert st == 200 and os.path.isfile(ex["path"]) and ex["name"].endswith(".CCTransfert")

    st, b = call("/api/bundle", {"path": ex["path"]})
    assert st == 200 and b["verified"] and b["manifest"]["session"]["id"] == SID
    # envoi du fichier par glisser-déposer (nom non ASCII encodé)
    with open(ex["path"], "rb") as f:
        st, up = call("/api/upload", raw=f.read(), headers={"X-Filename": "projet%20%C3%A9t%C3%A9.CCTransfert"})
    assert st == 200 and up["name"] == "projet été.CCTransfert"

    target = str(gui["home"] / "Autre" / "MonProjet")
    # même ID ailleurs sur la machine : conflit structuré
    st, err = call("/api/preview", {"path": ex["path"], "target": target})
    assert st == 409 and err["code"] == "conflict_other"
    st, pre = call("/api/preview", {"path": ex["path"], "target": target, "new_id": True})
    assert st == 200 and pre["dry_run"] and not os.path.exists(target) and pre["report"]["counts"]
    st, res = call("/api/import", {"path": ex["path"], "target": target, "new_id": True, "new_sid": pre["new_id"]})
    assert st == 200 and os.path.isdir(target) and res["new_id"] == pre["new_id"] != SID
    assert res["commands"]["resume"].endswith("claude -r")
    assert find_session_files(gui["prof"], res["new_id"])

    st, j = call("/api/journals")
    assert j["journals"][0]["undone"] is False
    st, u = call("/api/undo", {})
    assert st == 200 and u["errors"] == []
    assert not find_session_files(gui["prof"], res["new_id"])
    assert call("/api/undo", {})[0] == 404


def test_relocate_fs_doctor(gui, tmp_path):
    call = gui["call"]
    target = str(gui["home"] / "Ailleurs" / "MonProjet")
    st, pre = call("/api/relocate", {"id": SID, "target": target, "dry_run": True})
    assert st == 200 and pre["dry_run"] and find_session_files(gui["prof"], SID) == [gui["slug"]]
    st, res = call("/api/relocate", {"id": SID, "target": target})
    assert st == 200 and find_session_files(gui["prof"], SID) == [res["slug"]]
    st, fs = call("/api/fs?path=" + str(gui["home"]))
    assert st == 200 and any(e["name"] == "Projets" for e in fs["entries"])
    st, d = call("/api/doctor")
    assert st == 200 and d["sessions"] == 1
    st, s = call("/api/state")
    assert s["cwd"] == gui["root"]


def test_cycle_de_vie_plusieurs_fenetres(monkeypatch):
    from cctransfert.gui import server as srv

    app = srv.App("t")
    now = [1000.0]
    monkeypatch.setattr(srv.time, "time", lambda: now[0])
    app.started = now[0]
    assert not app.should_stop(True)  # on attend la fenêtre
    app.api_ping({}, {"page": "A"})
    app.api_ping({}, {"page": "B"})
    app.api_bye({"page": "A"}, {})
    now[0] += 10
    assert not app.should_stop(True)  # la fenêtre B est encore ouverte
    app.api_bye({"page": "B"}, {})
    now[0] += 2
    assert not app.should_stop(True)  # délai de grâce (rechargement)
    now[0] += 5
    assert app.should_stop(True)
    app2 = srv.App("t")
    app2.started = now[0]
    now[0] += srv.FIRST_PING_TIMEOUT + 1
    assert app2.should_stop(True) and not app2.should_stop(False)  # --no-open : on attend
    app2.api_quit({}, {})
    assert app2.should_stop(False)
