"""Lanceur du projet (CCTransfert.pyw) : démarre l'interface détachée, ou signale l'échec."""

import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "CCTransfert.pyw")


def _env(tmp_path):
    env = dict(os.environ)
    env.update({"CCTRANSFERT_HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
                "CCT_NO_DIALOG": "1", "HOME": str(tmp_path)})
    env.pop("PYTHONPATH", None)
    return env


def test_lanceur_demarre_l_interface_detachee(tmp_path):
    r = subprocess.run([sys.executable, LAUNCHER, "--no-open"], env=_env(tmp_path), timeout=30)
    assert r.returncode == 0
    inst = tmp_path / "home" / "gui-instance.json"
    for _ in range(50):
        if inst.exists():
            break
        time.sleep(0.1)
    d = json.loads(inst.read_text(encoding="utf-8"))
    base = "http://127.0.0.1:{}".format(d["port"])
    hdr = {"X-CCT-Token": d["token"]}
    with urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers=hdr), timeout=5) as resp:
        assert json.load(resp)["version"]
    urllib.request.urlopen(urllib.request.Request(base + "/api/quit", data=b"{}", method="POST", headers=hdr), timeout=5)
    for _ in range(50):
        if not inst.exists():
            break
        time.sleep(0.1)
    assert not inst.exists()  # l'interface s'est arrêtée et a fait le ménage


def test_lanceur_signale_un_echec(tmp_path):
    r = subprocess.run([sys.executable, LAUNCHER, "--no-open", "--port", "99999999"], env=_env(tmp_path),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 1
    assert "n'a pas pu démarrer" in r.stderr and "gui.log" in r.stderr
