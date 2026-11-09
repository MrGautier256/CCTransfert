"""Mises à jour : vérifie la dernière release GitHub et installe la nouvelle version.

* Vérification : un appel anonyme à l'API publique des releases GitHub (seul en-tête
  envoyé en plus du minimum : `User-Agent: CCTransfert/<version>`). En automatique, au
  plus une fois par jour, désactivable.
* Installation selon la façon dont CCTransfert est installé :
  - exe Windows / binaire Linux (version autonome) : téléchargement, vérification sha256,
    remplacement de l'exécutable, redémarrage ;
  - pipx / uv : téléchargement de la wheel vérifiée, puis `pipx install --force` / `uv tool install --force` ;
  - copie git ou autre : on indique la commande à lancer (pas d'action automatique).
* Tout téléchargement est refusé si son sha256 ne correspond pas au fichier
  `SHA256SUMS.txt` publié avec la release.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from . import __version__
from .jsonio import write_json_atomic
from .profile import no_window

REPO = os.environ.get("CCTRANSFERT_UPDATE_REPO", "MrGautier256/CCTransfert")
API = os.environ.get("CCTRANSFERT_UPDATE_API", "https://api.github.com").rstrip("/")
RELEASES_URL = "https://github.com/{}/releases".format(REPO)
CHECK_INTERVAL = 24 * 3600
SUMS_NAME = "SHA256SUMS.txt"
AUTO_KINDS = ("exe", "binary", "pipx", "uv")


class UpdateError(Exception):
    pass


def _tool_home():
    from .importer import tool_home

    return tool_home()


def _state_path():
    return os.path.join(_tool_home(), "update.json")


def load_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**changes):
    d = load_state()
    d.update(changes)
    write_json_atomic(_state_path(), d)
    return d


def parse_version(v):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest, current=__version__):
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


def _request(url, timeout, accept="application/vnd.github+json"):
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "CCTransfert/" + __version__})


def latest_release(timeout=8):
    """Dernière release publiée (hors brouillons et pré-versions)."""
    url = "{}/repos/{}/releases/latest".format(API, REPO)
    try:
        with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("aucune release publique trouvée pour {} (dépôt privé ?)".format(REPO))
        raise UpdateError("GitHub a répondu {} {}".format(e.code, e.reason))
    except (OSError, ValueError) as e:
        raise UpdateError("vérification impossible : {}".format(getattr(e, "reason", e)))
    tag = data.get("tag_name") or ""
    return {
        "version": tag.lstrip("v"),
        "tag": tag,
        "url": data.get("html_url") or RELEASES_URL,
        "notes": data.get("body") or "",
        "published": data.get("published_at"),
        "assets": [{"name": a.get("name"), "url": a.get("browser_download_url"), "size": a.get("size")}
                   for a in data.get("assets") or []],
    }


# -- type d'installation ------------------------------------------------------------------------


def install_kind():
    """exe | binary | pipx | uv | git | pip"""
    if getattr(sys, "frozen", False):
        return "exe" if os.name == "nt" else "binary"
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(repo, ".git")):
        return "git"  # copie du dépôt (y compris une installation « editable » qui pointe dessus)
    prefix = sys.prefix.replace("\\", "/").lower()
    if "/pipx/" in prefix:
        return "pipx"
    if "/uv/tools/" in prefix:
        return "uv"
    return "pip"


KIND_LABELS = {
    "exe": "exécutable Windows", "binary": "binaire Linux", "pipx": "installation pipx",
    "uv": "installation uv", "git": "copie du dépôt git", "pip": "installation pip",
}


def manual_instructions(kind, release=None):
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if kind == "git":
        return 'git -C "{}" pull'.format(repo_dir)
    wheel = _pick_asset(release, "pip") if release else None
    if wheel:
        return "{} -m pip install --upgrade {}".format(os.path.basename(sys.executable) or "python", wheel["url"])
    return "Téléchargez la nouvelle version : {}".format(RELEASES_URL)


def _pick_asset(release, kind):
    assets = (release or {}).get("assets") or []
    if kind == "exe":
        want_cli = "cli" in os.path.basename(sys.executable).lower()
        for a in assets:
            n = a["name"].lower()
            if n.endswith(".exe") and "windows" in n and (("cli" in n) == want_cli):
                return a
    elif kind == "binary":
        arch = "aarch64" if platform.machine().lower() in ("aarch64", "arm64") else "x86_64"
        for a in assets:
            if a["name"].lower().endswith("linux-" + arch):
                return a
    else:
        for a in assets:
            if a["name"].endswith(".whl"):
                return a
    return None


# -- vérification ---------------------------------------------------------------------------------


def check(force=False):
    """Résultat de la vérification (avec cache d'un jour pour les vérifications automatiques)."""
    st = load_state()
    kind = install_kind()
    res = {"current": __version__, "kind": kind, "kind_label": KIND_LABELS.get(kind, kind),
           "auto_check": st.get("auto_check", True), "can_auto": kind in AUTO_KINDS,
           "available": False, "latest": None, "error": None, "releases_url": RELEASES_URL}
    if not force and not res["auto_check"]:
        return res
    latest = st.get("latest")
    fresh = latest and time.time() - (st.get("checked_at") or 0) < CHECK_INTERVAL
    if force or not fresh:
        try:
            latest = latest_release()
            save_state(checked_at=time.time(), latest=latest)
        except UpdateError as e:
            res["error"] = str(e)
            return res
    res["latest"] = latest
    if latest and is_newer(latest.get("version")):
        res["available"] = True
        res["skipped"] = st.get("skipped") == latest.get("version")
        if res["can_auto"] and not _pick_asset(latest, kind):
            res["can_auto"] = False  # la release ne contient pas le fichier pour cette installation
        if not res["can_auto"]:
            res["instructions"] = manual_instructions(kind, latest)
    return res


# -- installation ---------------------------------------------------------------------------------


def _download(url, dest, progress=None):
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(_request(url, 60, accept="application/octet-stream"), timeout=60) as r, \
            open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            f.write(b)
            h.update(b)
            done += len(b)
            if progress:
                progress(done, total)
    return h.hexdigest()


def _expected_sums(release):
    a = next((x for x in release.get("assets") or [] if x["name"] == SUMS_NAME), None)
    if not a:
        raise UpdateError("la release ne publie pas {} : mise à jour automatique refusée".format(SUMS_NAME))
    with urllib.request.urlopen(_request(a["url"], 30, accept="application/octet-stream"), timeout=30) as r:
        text = r.read().decode("utf-8", "replace")
    sums = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line.strip())
        if m:
            sums[m.group(2).strip()] = m.group(1).lower()
    return sums


def download_verified(release, kind, progress=None):
    """Télécharge le fichier adapté à l'installation et vérifie son sha256. Renvoie son chemin."""
    asset = _pick_asset(release, kind)
    if not asset:
        raise UpdateError("aucun fichier de la release ne correspond à cette installation ({})".format(kind))
    sums = _expected_sums(release)
    if asset["name"] not in sums:
        raise UpdateError("{} absent de {}".format(asset["name"], SUMS_NAME))
    d = tempfile.mkdtemp(prefix="cct-update-")
    path = os.path.join(d, asset["name"])
    digest = _download(asset["url"], path, progress)
    if digest != sums[asset["name"]]:
        shutil.rmtree(d, ignore_errors=True)
        raise UpdateError("empreinte sha256 incorrecte pour {} : fichier rejeté".format(asset["name"]))
    return path


def restart_command():
    """Commande qui relance l'interface après la mise à jour."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "gui"]
    exe = shutil.which("CCTransfert") or shutil.which("cctransfert")
    return [exe, "gui"] if exe else [sys.executable, "-m", "cctransfert", "gui"]


def apply(release=None, progress=None):
    """Installe la dernière version. Renvoie {"version", "restart"} ou lève UpdateError."""
    kind = install_kind()
    if kind not in AUTO_KINDS:
        raise UpdateError("mise à jour automatique impossible pour une {} : {}".format(
            KIND_LABELS.get(kind, kind), manual_instructions(kind, release)))
    release = release or latest_release()
    if not is_newer(release.get("version")):
        raise UpdateError("vous avez déjà la dernière version ({})".format(__version__))
    path = download_verified(release, kind, progress)
    try:
        if kind in ("exe", "binary"):
            exe = os.path.abspath(sys.executable)
            new = exe + ".new"
            shutil.copyfile(path, new)
            if os.name == "nt":
                old = exe + ".old"
                if os.path.exists(old):
                    os.remove(old)
                os.replace(exe, old)  # un exe en cours d'exécution peut être renommé, pas écrasé
                os.replace(new, exe)
            else:
                os.chmod(new, 0o755)
                os.replace(new, exe)
        else:
            tool = shutil.which("pipx" if kind == "pipx" else "uv")
            if not tool:
                raise UpdateError("`{}` introuvable : {}".format(kind, manual_instructions("pip", release)))
            cmd = [tool, "install", "--force", path] if kind == "pipx" else [tool, "tool", "install", "--force", path]
            r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, **no_window())
            if r.returncode != 0:
                raise UpdateError("échec de « {} » : {}".format(" ".join(cmd[:3]), (r.stderr or r.stdout).strip()[-400:]))
    finally:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    save_state(skipped=None)
    return {"version": release["version"], "restart": restart_command()}


def spawn_restart(cmd):
    """Relance l'interface ; le nouveau processus attend que celui-ci soit terminé."""
    env = dict(os.environ, CCT_WAIT_PID=str(os.getpid()))
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": env}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # détaché, sans console
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)


def wait_for_previous():
    """Au démarrage après une mise à jour : attendre la fin de l'ancien processus."""
    pid = os.environ.pop("CCT_WAIT_PID", None)
    if not pid or not pid.isdigit():
        return
    from .store import _pid_alive

    for _ in range(100):
        if not _pid_alive(int(pid)):
            return
        time.sleep(0.2)


def cleanup_old_binary():
    """Windows : supprime l'ancien exe renommé lors de la dernière mise à jour."""
    if getattr(sys, "frozen", False):
        old = os.path.abspath(sys.executable) + ".old"
        try:
            if os.path.exists(old):
                os.remove(old)
        except OSError:
            pass
