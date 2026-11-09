"""Serveur de l'interface graphique : API JSON + fichiers statiques, sur 127.0.0.1 uniquement.

Sécurité :
* écoute seulement sur 127.0.0.1 ;
* chaque appel à l'API exige le jeton aléatoire généré au lancement (en-tête
  `X-CCT-Token`), transmis à la page dans l'URL (fragment `#t=…`, jamais envoyé au
  serveur ni enregistré dans un historique de requêtes) ;
* l'en-tête `Host` doit être 127.0.0.1/localhost (protection contre le DNS rebinding) ;
* aucune ressource externe : la page fonctionne hors ligne et ne fait fuiter aucune
  donnée de session.

Le serveur s'arrête tout seul quand la page est fermée (battement de cœur).
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
from urllib.parse import parse_qs, quote, unquote, urlparse

from .. import __version__
from .. import doctor as doctor_mod
from .. import paths as P
from ..bundle import BundleError, BundleReader
from ..exporter import collect, default_name, export_session, full_scan, git_info, history_lines
from ..importer import (
    ImportError_, ImportOptions, duplicate_session, import_bundle, last_journal, list_journals,
    recalled_mapping, relocate_session, remember_mapping, tool_home, undo, undo_check,
)
from .. import update as up
from ..jsonio import write_json_atomic
from ..profile import local_profile
from ..slug import is_session_id
from ..store import (
    SDK_ENTRYPOINTS, find_session_files, list_sessions, running_sessions, summarize,
)

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".ico": "image/x-icon"}
PING_TIMEOUT = 150  # s sans battement de cœur avant arrêt (Chrome ralentit les onglets cachés à 1/min)
FIRST_PING_TIMEOUT = 120  # s pour que la fenêtre ouverte au lancement se connecte
BYE_GRACE = 5  # s après fermeture de la page (laisse le temps à un rechargement)


class ApiError(Exception):
    def __init__(self, message, status=400, code=None, extra=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra or {}


# -- sérialisation ------------------------------------------------------------------------


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def session_dict(s):
    return {
        "id": s.sid, "short": s.short_id, "title": s.display_title, "has_title": bool(s.title),
        "title_source": s.title_source, "first_prompt": s.first_prompt, "last": _iso(s.last_activity),
        "created": _iso(s.created), "size": s.size, "root": s.root, "slug": s.slug,
        "project": P.basename(s.root, P.style_of(s.root)) if s.root else None,
        "running": bool(s.running), "pid": (s.running or {}).get("pid"),
        "sdk": s.entrypoint in SDK_ENTRYPOINTS, "entrypoint": s.entrypoint, "branch": s.git_branch,
    }


def result_dict(res, entrypoint=None):
    rep = res.report
    style = P.style_of(res.target_root or "/")
    cd = 'cd "{}"; '.format(res.target_root) if style == P.WINDOWS else 'cd "{}" && '.format(res.target_root)
    sdk = entrypoint in SDK_ENTRYPOINTS
    return {
        "old_id": res.old_sid, "new_id": res.new_sid, "slug": res.slug, "slug_reason": res.slug_reason,
        "target_root": res.target_root, "transcript": res.transcript, "journal": res.journal_dir,
        "actions": [{"op": op, "path": p, "detail": d} for op, p, d in res.actions],
        "rules": [{"name": r.name, "src": r.src, "dst": r.dst} for r in sorted(res.rules, key=lambda r: -len(r.src)) if not r.noop],
        "report": {
            "lines_total": rep.lines_total, "lines_changed": rep.lines_changed, "lines_bad": rep.lines_bad,
            "counts": rep.counts.most_common(),
            "examples": [{"rule": k, "pairs": v} for k, v in rep.examples.items()],
            "unmapped": rep.unmapped.most_common(10), "warnings": rep.warnings[:50],
        },
        "warnings": res.warnings, "git_warnings": res.git_warnings, "sdk": sdk,
        "commands": {
            "resume": cd + ("claude --resume {}".format(res.new_sid) if sdk else "claude -r"),
            "by_id": "claude --resume {}".format(res.new_sid),
            "fallback": 'claude --resume "{}"'.format(res.transcript),
        },
    }


def bundle_dict(path, rd, verified, profile, known_roots):
    m = rd.manifest
    kinds = {}
    for e in m.get("files", []):
        k = kinds.setdefault(e.get("kind"), {"count": 0, "size": 0})
        k["count"] += 1
        k["size"] += e.get("size") or 0
    src = m.get("source") or {}
    remembered = recalled_mapping(src)
    suggestions = []
    if remembered:
        suggestions.append({"path": remembered, "label": "mémorisé pour ce projet"})
    base = P.basename(src.get("project_root") or "", src.get("style") or P.POSIX)
    for r in sorted(known_roots):
        if base and P.basename(r, profile.style) == base and r != remembered:
            suggestions.append({"path": r, "label": "projet du même nom sur cette machine"})
    if src.get("project_root") and src.get("hostname") == profile.hostname and src.get("style") == profile.style:
        if not any(s["path"] == src["project_root"] for s in suggestions):
            suggestions.append({"path": src["project_root"], "label": "dossier d'origine (même machine)"})
    return {
        "path": path, "name": os.path.basename(path), "size": os.path.getsize(path), "verified": verified,
        "manifest": {k: m.get(k) for k in ("format_version", "tool", "created_at", "source", "session", "options")},
        "kinds": kinds, "suggestions": suggestions,
    }


def _downloads_dir():
    home = os.path.expanduser("~")
    try:
        with open(os.path.join(home, ".config", "user-dirs.dirs"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r'\s*XDG_DOWNLOAD_DIR="(.*)"', line)
                if m:
                    d = m.group(1).replace("$HOME", home)
                    if os.path.isdir(d):
                        return d
    except OSError:
        pass
    for n in ("Downloads", "Téléchargements"):
        d = os.path.join(home, n)
        if os.path.isdir(d):
            return d
    return home


def open_in_file_manager(path):
    """Ouvre le dossier (ou montre le fichier) dans le gestionnaire de fichiers."""
    if sys.platform.startswith("win"):
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
        else:
            os.startfile(path)  # noqa: S606 - action explicite de l'utilisateur
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
    else:
        target = path if os.path.isdir(path) else os.path.dirname(path)
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# -- application --------------------------------------------------------------------------


class App:
    def __init__(self, token, preload=None):
        self.token = token
        self.preload = preload
        self.write_lock = threading.Lock()
        self.last_ping = None
        self.bye_at = None
        self.pages = {}
        self.quit = False
        self.started = time.time()
        self.uploads = os.path.join(tool_home(), "gui-uploads", secrets.token_hex(4))
        self.prefs_path = os.path.join(tool_home(), "gui.json")
        self._profile = None

    @property
    def profile(self):
        if self._profile is None:
            self._profile = local_profile(with_claude_version=True)
        return self._profile

    def prefs(self):
        try:
            with open(self.prefs_path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_pref(self, key, value):
        d = self.prefs()
        d[key] = value
        write_json_atomic(self.prefs_path, d)

    def find(self, sid):
        prof = self.profile
        slugs = find_session_files(prof, sid)
        if not slugs:
            raise ApiError("session introuvable : {}".format(sid), 404)
        info = summarize(prof, slugs[0], sid)
        info.running = running_sessions(prof).get(sid)
        return info

    # -- API ----------------------------------------------------------------

    def api_state(self, q, body):
        p = self.profile
        return {"version": __version__, "os": p.os, "wsl": p.wsl, "hostname": p.hostname, "user": p.user,
                "home": p.home, "config_dir": p.config_dir, "claude_version": p.claude_version,
                "style": p.style, "sep": "\\" if p.style == P.WINDOWS else "/",
                "export_dir": self.prefs().get("export_dir") or _downloads_dir(),
                "cwd": os.path.realpath(p.cwd) if p.cwd else None,
                "windows": len(self.pages),
                "preload": self.preload}

    def api_sessions(self, q, body):
        return {"sessions": [session_dict(s) for s in list_sessions(self.profile)]}

    def api_session(self, q, body):
        info = self.find(q.get("id", ""))
        scan = full_scan(info.fs_path)
        files = collect(self.profile, info, scan, with_memory=True, with_file_history=True)
        kinds = {}
        for _m, kind, full in files:
            k = kinds.setdefault(kind, {"count": 0, "size": 0})
            k["count"] += 1
            try:
                k["size"] += os.path.getsize(full)
            except OSError:
                pass
        d = session_dict(info)
        d.update({"messages": scan["messages"], "user_prompts": scan["user_prompts"],
                  "created": _iso(scan["created"]) or d["created"], "bad_lines": scan["bad_lines"],
                  "kinds": kinds, "history": len(history_lines(self.profile, info.sid)),
                  "git": git_info(info.root) if info.root else None})
        return d

    def api_export(self, q, body):
        info = self.find(body.get("id", ""))
        if info.running and not body.get("force_running"):
            raise ApiError("la session est en cours", 409, "running", {"pid": info.running.get("pid")})
        out_dir = os.path.abspath(os.path.expanduser(body.get("out_dir") or _downloads_dir()))
        os.makedirs(out_dir, exist_ok=True)
        name = default_name(info, info.root, self.profile.style)
        out = os.path.join(out_dir, name)
        n = 2
        while os.path.exists(out):
            stem = name[: -len(".CCTransfert")]
            out = os.path.join(out_dir, "{} ({}).CCTransfert".format(stem, n))
            n += 1
        with self.write_lock:
            m = export_session(self.profile, info, out, with_memory=bool(body.get("with_memory")),
                               with_file_history=body.get("file_history", True) is not False,
                               with_history=bool(body.get("with_history")))
        self.save_pref("export_dir", out_dir)
        return {"path": out, "name": os.path.basename(out), "size": os.path.getsize(out),
                "files": len(m.get("files", []))}

    def _open_bundle(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise ApiError("fichier introuvable : {}".format(path), 404)
        with BundleReader(path) as rd:
            try:
                rd.verify()
                verified = True
            except BundleError as e:
                raise ApiError(str(e), 422, "corrupt")
            roots = {s.root for s in list_sessions(self.profile) if s.root}
            return bundle_dict(path, rd, verified, self.profile, roots)

    def api_bundle(self, q, body):
        return self._open_bundle(body.get("path", ""))

    def upload(self, handler):
        name = os.path.basename(unquote(handler.headers.get("X-Filename") or "") or "bundle.CCTransfert")
        name = re.sub(r"[^\w.\- ()]+", "_", name) or "bundle.CCTransfert"
        length = int(handler.headers.get("Content-Length") or 0)
        os.makedirs(self.uploads, exist_ok=True)
        dest = os.path.join(self.uploads, name)
        left = length
        with open(dest, "wb") as f:
            while left > 0:
                chunk = handler.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            raise ApiError("envoi interrompu")
        return self._open_bundle(dest)

    def _import_opts(self, body, dry):
        target = (body.get("target") or "").strip()
        if not target:
            raise ApiError("indiquez le dossier du projet sur cette machine")
        target = os.path.abspath(os.path.expanduser(target))
        if os.path.isdir(target):
            target = os.path.realpath(target)
        elif os.path.exists(target):
            raise ApiError("{} existe mais n'est pas un dossier".format(target))
        elif not dry:
            os.makedirs(target)
            target = os.path.realpath(target)
        maps = []
        for pair in body.get("maps") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0].strip() and pair[1].strip():
                maps.append((pair[0].strip(), pair[1].strip()))
        forced = body.get("new_sid")
        if not (isinstance(forced, str) and is_session_id(forced)):
            forced = None
        return ImportOptions(target, maps=maps, new_id=bool(body.get("new_id")), replace=bool(body.get("replace")),
                             dry_run=dry, keep_mtime=bool(body.get("keep_mtime")), forced_sid=forced,
                             title=body.get("title"))

    def _do_import(self, body, dry):
        path = body.get("path", "")
        opts = self._import_opts(body, dry)
        with self.write_lock:
            res = import_bundle(path, self.profile, opts)
            with BundleReader(path) as rd:
                m = rd.manifest
        if not dry:
            remember_mapping(m["source"], res.target_root)
        d = result_dict(res, (m.get("session") or {}).get("entrypoint"))
        d["dry_run"] = dry
        d["target_exists"] = os.path.isdir(opts.target_root)
        return d

    def api_preview(self, q, body):
        return self._do_import(body, True)

    def api_import(self, q, body):
        return self._do_import(body, False)

    def api_relocate(self, q, body):
        info = self.find(body.get("id", ""))
        target = os.path.abspath(os.path.expanduser((body.get("target") or "").strip()))
        dry = bool(body.get("dry_run"))
        if not dry:
            os.makedirs(target, exist_ok=True)
        if os.path.isdir(target):
            target = os.path.realpath(target)
        with self.write_lock:
            res = relocate_session(self.profile, info, target, dry_run=dry)
        d = result_dict(res, info.entrypoint)
        d["dry_run"] = dry
        return d

    def api_duplicate(self, q, body):
        """Copie d'une session sur cette machine (nouvel ID), même dossier ou autre, nouvel intitulé."""
        info = self.find(body.get("id", ""))
        dry = bool(body.get("dry_run"))
        target = (body.get("target") or "").strip()
        if target:
            target = os.path.abspath(os.path.expanduser(target))
            if os.path.exists(target) and not os.path.isdir(target):
                raise ApiError("{} existe mais n'est pas un dossier".format(target))
            if not dry:
                os.makedirs(target, exist_ok=True)
            if os.path.isdir(target):
                target = os.path.realpath(target)
        with self.write_lock:
            res = duplicate_session(self.profile, info, target or None, title=body.get("title"), dry_run=dry)
        d = result_dict(res, info.entrypoint)
        d["dry_run"] = dry
        return d

    # -- mises à jour et raccourcis ------------------------------------------------------

    def api_update(self, q, body):
        return up.check(force=q.get("force") == "1")

    def api_update_skip(self, q, body):
        up.save_state(skipped=body.get("version"))
        return {"ok": True}

    def api_update_settings(self, q, body):
        up.save_state(auto_check=bool(body.get("auto_check")))
        return {"auto_check": bool(body.get("auto_check"))}

    def api_update_apply(self, q, body):
        with self.write_lock:
            res = up.apply()

        def restart():  # on répond d'abord à la page, puis on passe la main à la nouvelle version
            time.sleep(0.8)
            up.spawn_restart(res["restart"])
            self.quit = True

        threading.Thread(target=restart, daemon=True).start()
        return {"version": res["version"], "restarting": True}

    def api_shortcuts(self, q, body):
        from . import shortcuts

        return shortcuts.status()

    def api_shortcuts_set(self, q, body):
        from . import shortcuts

        created, removed = shortcuts.set_shortcuts(desktop=body.get("desktop"), menu=body.get("menu"))
        st = shortcuts.status()
        st.update(created=created, removed=removed)
        return st

    def api_journals(self, q, body):
        out = []
        for d, data in list_journals():
            out.append({"id": os.path.basename(d), "created_at": data.get("created_at"),
                        "session_ids": data.get("session_ids", []), "target_root": data.get("target_root"),
                        "undone": bool(data.get("undone")), "aborted": bool(data.get("aborted")),
                        "undone_at": data.get("undone_at"), "created": len(data.get("created", [])),
                        "backups": len(data.get("backups", [])), "history": len(data.get("history_lines", []))})
        return {"journals": out}

    def api_undo(self, q, body):
        d, data = last_journal()
        if not d:
            raise ApiError("aucun import à annuler", 404)
        running = running_sessions(self.profile)
        busy = [s for s in data.get("session_ids", []) if s in running]
        if busy:
            raise ApiError("la session {} est en cours : quittez-la (/exit) avant d'annuler.".format(busy[0][:8]), 409, "running")
        changed = undo_check(data)
        if changed and not body.get("force"):
            raise ApiError("{} fichier(s) ont été modifiés depuis l'import (session reprise ?) : "
                           "ces modifications seront perdues.".format(len(changed)), 409, "modified", {"files": changed})
        with self.write_lock:
            errors = undo(d, data)
        return {"errors": errors, "id": os.path.basename(d)}

    def api_doctor(self, q, body):
        return doctor_mod.collect(self.profile)

    def api_fs(self, q, body):
        """Navigateur de dossiers (le navigateur web ne donne pas accès aux chemins locaux)."""
        want_files = q.get("files") == "1"
        path = q.get("path") or ""
        path = os.path.abspath(os.path.expanduser(path)) if path else os.path.expanduser("~")
        while not os.path.isdir(path):
            parent = os.path.dirname(path)
            if parent == path:
                path = os.path.expanduser("~")
                break
            path = parent
        entries = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        is_dir = e.is_dir()
                    except OSError:
                        continue
                    if is_dir:
                        entries.append({"name": e.name, "path": e.path, "dir": True})
                    elif want_files and e.name.endswith((".CCTransfert", ".CCTransfert.age", ".CCTransfert.gpg")):
                        st = e.stat()
                        entries.append({"name": e.name, "path": e.path, "dir": False, "size": st.st_size,
                                        "mtime": st.st_mtime})
        except PermissionError:
            raise ApiError("accès refusé : {}".format(path), 403)
        entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        home = os.path.expanduser("~")
        shortcuts = [{"label": "Dossier personnel", "path": home}]
        for label, sub in (("Bureau", "Desktop"), ("Bureau", "Bureau"), ("Documents", "Documents"),
                           ("Projets", "Projets"), ("Projets", "Projects")):
            p = os.path.join(home, sub)
            if os.path.isdir(p) and not any(s["path"] == p for s in shortcuts):
                shortcuts.append({"label": label, "path": p})
        shortcuts.append({"label": "Téléchargements", "path": _downloads_dir()})
        if sys.platform.startswith("win"):
            import string

            for letter in string.ascii_uppercase:
                d = letter + ":\\"
                if os.path.exists(d):
                    shortcuts.append({"label": d, "path": d})
        else:
            shortcuts.append({"label": "Racine /", "path": "/"})
        parent = os.path.dirname(path)
        return {"path": path, "parent": parent if parent != path else None, "entries": entries,
                "shortcuts": shortcuts, "sep": os.sep}

    def api_stat(self, q, body):
        p = (q.get("path") or "").strip()
        if not p:
            return {"exists": False, "is_dir": False, "abs": ""}
        a = os.path.abspath(os.path.expanduser(p))
        return {"exists": os.path.exists(a), "is_dir": os.path.isdir(a), "abs": a}

    def api_open(self, q, body):
        p = body.get("path") or ""
        if not os.path.exists(p):
            raise ApiError("introuvable : {}".format(p), 404)
        open_in_file_manager(p)
        return {"ok": True}

    # -- cycle de vie : une entrée par fenêtre ouverte, arrêt quand la dernière se ferme ----

    def api_ping(self, q, body):
        now = time.time()
        self.last_ping = now
        self.pages[str(body.get("page") or q.get("page") or "?")] = now
        return {"ok": True}

    def api_bye(self, q, body):
        self.pages.pop(str(q.get("page") or body.get("page") or "?"), None)
        self.bye_at = time.time()
        return {"ok": True}

    def api_quit(self, q, body):
        self.quit = True
        return {"ok": True}

    def should_stop(self, waited_for_window):
        now = time.time()
        if self.quit:
            return True
        for k, t in list(self.pages.items()):
            if now - t > PING_TIMEOUT:  # fenêtre fermée sans prévenir (plantage, mise en veille…)
                self.pages.pop(k, None)
        if self.pages:
            return False
        if self.last_ping is None:
            return waited_for_window and now - self.started > FIRST_PING_TIMEOUT
        # Plus aucune fenêtre : on laisse quelques secondes à un rechargement de page.
        last = max(self.bye_at or 0, self.last_ping)
        return now - last >= BYE_GRACE

    def cleanup(self):
        shutil.rmtree(self.uploads, ignore_errors=True)


ROUTES = {
    ("GET", "/api/state"): "api_state", ("GET", "/api/sessions"): "api_sessions",
    ("GET", "/api/session"): "api_session", ("POST", "/api/export"): "api_export",
    ("POST", "/api/bundle"): "api_bundle", ("POST", "/api/preview"): "api_preview",
    ("POST", "/api/import"): "api_import", ("POST", "/api/relocate"): "api_relocate",
    ("POST", "/api/duplicate"): "api_duplicate",
    ("GET", "/api/update"): "api_update", ("POST", "/api/update/skip"): "api_update_skip",
    ("POST", "/api/update/settings"): "api_update_settings", ("POST", "/api/update/apply"): "api_update_apply",
    ("GET", "/api/shortcuts"): "api_shortcuts", ("POST", "/api/shortcuts"): "api_shortcuts_set",
    ("GET", "/api/journals"): "api_journals", ("POST", "/api/undo"): "api_undo",
    ("GET", "/api/doctor"): "api_doctor", ("GET", "/api/fs"): "api_fs", ("GET", "/api/stat"): "api_stat",
    ("POST", "/api/open"): "api_open", ("POST", "/api/ping"): "api_ping", ("POST", "/api/bye"): "api_bye",
    ("POST", "/api/quit"): "api_quit",
}


def make_handler(app, port_ref):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CCTransfert/" + __version__

        def log_message(self, fmt, *args):  # pas de journal des requêtes dans le terminal
            pass

        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            return host in ("127.0.0.1:{}".format(port_ref[0]), "localhost:{}".format(port_ref[0]))

        def _send(self, status, data, ctype="application/json; charset=utf-8"):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                 "connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path):
            if path.startswith(APP_PATH):
                path = "/" + path[len(APP_PATH):]
            elif path == APP_PATH.rstrip("/"):
                path = "/"
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            if "/" in name or name.startswith("."):
                return self._send(404, {"error": "introuvable"})
            full = os.path.join(STATIC, name)
            if not os.path.isfile(full):
                return self._send(404, {"error": "introuvable"})
            with open(full, "rb") as f:
                data = f.read()
            self._send(200, data, CONTENT_TYPES.get(os.path.splitext(name)[1], "application/octet-stream"))

        def _dispatch(self, method):
            if not self._host_ok():
                return self._send(403, {"error": "hôte refusé"})
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not u.path.startswith("/api/"):
                if method != "GET":
                    return self._send(405, {"error": "méthode refusée"})
                return self._static(u.path)
            token = self.headers.get("X-CCT-Token") or q.get("t")
            if not token or not secrets.compare_digest(token, app.token):
                return self._send(403, {"error": "jeton invalide"})
            try:
                if (method, u.path) == ("POST", "/api/upload"):
                    return self._send(200, app.upload(self))
                name = ROUTES.get((method, u.path))
                if not name:
                    return self._send(404, {"error": "route inconnue"})
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n:
                        raw = self.rfile.read(n)
                        try:
                            body = json.loads(raw.decode("utf-8"))
                        except ValueError:
                            body = {}
                self._send(200, getattr(app, name)(q, body))
            except ApiError as e:
                self._send(e.status, dict({"error": str(e), "code": e.code}, **e.extra))
            except ImportError_ as e:
                self._send(409 if e.code else 400, {"error": str(e), "code": e.code})
            except BundleError as e:
                self._send(422, {"error": str(e), "code": "bundle"})
            except up.UpdateError as e:
                self._send(502, {"error": str(e), "code": "update"})
            except OSError as e:
                self._send(500, {"error": str(e), "code": "os"})
            except Exception as e:  # noqa: BLE001 - on renvoie l'erreur à la page plutôt que de couper la connexion
                import traceback

                traceback.print_exc()
                self._send(500, {"error": "{}: {}".format(type(e).__name__, e), "code": "internal"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


# -- lancement -----------------------------------------------------------------------------


def _app_browsers():
    """Navigateurs capables d'ouvrir une fenêtre « application » (sans barre d'adresse)."""
    out = []
    if sys.platform.startswith("win"):
        envs = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]
        rels = ["Microsoft\\Edge\\Application\\msedge.exe", "Google\\Chrome\\Application\\chrome.exe",
                "BraveSoftware\\Brave-Browser\\Application\\brave.exe"]
        for rel in rels:
            for base in envs:
                if base and os.path.isfile(os.path.join(base, rel)):
                    out.append(os.path.join(base, rel))
    elif sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Chromium", "Brave Browser"):
            p = "/Applications/{0}.app/Contents/MacOS/{0}".format(app)
            if os.path.isfile(p):
                out.append(p)
    else:
        for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                  "microsoft-edge-stable", "brave-browser"):
            p = shutil.which(n)
            if p:
                out.append(p)
    return out


APP_PATH = "/cctransfert/"  # chemin fixe : donne à la fenêtre un identifiant stable (icône du dock)
APP_SLUG = "127.0.0.1__cctransfert"  # nom de fenêtre que Chrome dérive de http://127.0.0.1:<port>/cctransfert/


def browser_profile_dir():
    """Profil de navigateur dédié : fenêtre indépendante, sans les extensions de l'utilisateur."""
    return os.path.join(tool_home(), "browser-profile")


def open_window(url, app_mode=True):
    if app_mode:
        for exe in _app_browsers():
            args = [exe, "--app=" + url, "--user-data-dir=" + browser_profile_dir(), "--no-first-run",
                    "--no-default-browser-check", "--window-size=1280,860"]
            if sys.platform.startswith("linux"):
                args.append("--class=CCTransfert")
            kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
            if not sys.platform.startswith("win"):
                kw["start_new_session"] = True
            try:
                subprocess.Popen(args, **kw)
                return exe
            except OSError:
                continue
    webbrowser.open(url)
    return "navigateur par défaut"


def app_url(port, token, preload=None):
    url = "http://127.0.0.1:{}{}#t={}".format(port, APP_PATH, token)
    if preload:
        url += "&open=" + quote(preload, safe="")
    return url


def make_server(port=0, preload=None):
    """Crée le serveur (sans le démarrer). Renvoie (httpd, app, url)."""
    token = secrets.token_urlsafe(24)
    app = App(token, preload=preload)
    port_ref = [0]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    httpd.daemon_threads = True
    port_ref[0] = httpd.server_address[1]
    return httpd, app, app_url(port_ref[0], token)


# -- instance unique ----------------------------------------------------------------------


def _instance_file():
    return os.path.join(tool_home(), "gui-instance.json")


def existing_instance():
    """Instance déjà lancée et qui répond, ou None. (Fichier en 0600 : il contient le jeton.)"""
    from ..store import _pid_alive

    try:
        with open(_instance_file(), encoding="utf-8") as f:
            d = json.load(f)
        if not _pid_alive(int(d["pid"])):
            return None
        req = urllib.request.Request("http://127.0.0.1:{}/api/state".format(int(d["port"])),
                                     headers={"X-CCT-Token": d["token"]})
        with urllib.request.urlopen(req, timeout=3) as r:
            return d if r.status == 200 else None
    except Exception:  # noqa: BLE001 - fichier absent, périmé ou serveur muet
        return None


def serve(port=0, open_browser=True, app_mode=True, preload=None, quiet=False):
    """Lance l'interface. Bloque jusqu'à la fermeture de la dernière fenêtre (ou Ctrl+C)."""
    up.wait_for_previous()  # relance après une mise à jour : laisser l'ancienne version se fermer
    if open_browser:
        inst = existing_instance()
        if inst:
            open_window(app_url(inst["port"], inst["token"], preload), app_mode)
            if not quiet:
                print("CCTransfert est déjà ouvert : nouvelle fenêtre sur l'instance existante.", flush=True)
            return 0
    httpd, app, url = make_server(port, preload)
    try:
        write_json_atomic(_instance_file(), {"pid": os.getpid(), "port": httpd.server_address[1], "token": app.token})
    except OSError:
        pass

    def watchdog():
        while not app.should_stop(open_browser):
            time.sleep(1)
        httpd.shutdown()

    threading.Thread(target=watchdog, daemon=True).start()
    if threading.current_thread() is threading.main_thread():
        import signal

        def _term(*_a):  # arrêt propre (nettoyage des fichiers envoyés) sur SIGTERM
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _term)
    if not quiet:
        print("Interface CCTransfert : {}".format(url), flush=True)
        print("(elle s'arrête quand la fenêtre est fermée ; Ctrl+C pour quitter)", flush=True)
    if open_browser:
        how = open_window(app_url(httpd.server_address[1], app.token, preload), app_mode)
        if not quiet:
            print("Ouverture avec : {}".format(how), flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.cleanup()
        try:
            with open(_instance_file(), encoding="utf-8") as f:
                mine = json.load(f).get("pid") == os.getpid()
            if mine:  # fichier refermé avant suppression : Windows refuse d'effacer un fichier ouvert
                os.unlink(_instance_file())
        except (OSError, ValueError):
            pass
    return 0
