"""Vérifie un exécutable construit par packaging/build.py (utilisé par la CI).

    python packaging/smoke_test.py dist/cctransfert-cli-1.1.0-windows-x64.exe

Contrôle : --version, doctor --json, et démarrage du serveur de l'interface (sans fenêtre)
qui doit servir sa page et ses fichiers intégrés, puis s'arrêter proprement.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request


def main(exe):
    tmp = tempfile.mkdtemp(prefix="cct-smoke-")
    env = dict(os.environ, CCTRANSFERT_HOME=os.path.join(tmp, "home"), CLAUDE_CONFIG_DIR=os.path.join(tmp, "cfg"))
    os.makedirs(env["CLAUDE_CONFIG_DIR"])

    out = subprocess.run([exe, "--version"], capture_output=True, text=True, env=env, timeout=120)
    print("--version :", out.stdout.strip(), out.stderr.strip())
    assert out.returncode == 0 and "CCTransfert" in out.stdout

    out = subprocess.run([exe, "doctor", "--json"], capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    print("doctor : os={} config={}".format(d["os"], d["config_dir"]))

    p = subprocess.Popen([exe, "gui", "--no-open"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    inst = os.path.join(env["CCTRANSFERT_HOME"], "gui-instance.json")
    for _ in range(300):
        if os.path.exists(inst):
            break
        time.sleep(0.1)
    with open(inst, encoding="utf-8") as f:
        i = json.load(f)
    base = "http://127.0.0.1:{}".format(i["port"])
    for path in ("/cctransfert/", "/cctransfert/app.js", "/cctransfert/app.css", "/cctransfert/icon.svg"):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            assert r.status == 200 and r.read(), path
    hdr = {"X-CCT-Token": i["token"]}
    with urllib.request.urlopen(urllib.request.Request(base + "/api/state", headers=hdr), timeout=10) as r:
        print("interface : version", json.load(r)["version"])
    urllib.request.urlopen(urllib.request.Request(base + "/api/quit", data=b"{}", method="POST", headers=hdr), timeout=10)
    p.wait(timeout=30)
    print("serveur arrêté, code", p.returncode)
    assert p.returncode == 0
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
