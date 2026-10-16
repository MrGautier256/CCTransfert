"""Lecture du dossier de config de Claude Code : sessions, métadonnées, sessions en cours."""

import json
import os
import re
import sys
from datetime import datetime, timezone

from . import paths as P
from .slug import compute_slug, drive_case_of_slug, is_session_id, slug_matches

HEAD_BYTES = 64 * 1024
# Sessions créées par `claude -p` ou le SDK : Claude Code les exclut de son picker.
SDK_ENTRYPOINTS = frozenset({"sdk-cli", "sdk-ts", "sdk-py"})
TAIL_BYTES = 256 * 1024


# -- utilitaires -----------------------------------------------------------------


def parse_ts(s):
    """Horodatage ISO de Claude Code (`2026-10-08T12:27:13.867Z`) -> datetime UTC, ou None."""
    if not isinstance(s, str) or not s:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$", s)
    if not m:
        return None
    frac = (m.group(3) or "0")[:6].ljust(6, "0")
    try:
        dt = datetime.strptime("{}T{}".format(m.group(1), m.group(2)), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    dt = dt.replace(microsecond=int(frac), tzinfo=timezone.utc)
    tz = m.group(4)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        from datetime import timedelta

        dt = dt - sign * timedelta(hours=hh, minutes=mm)
    return dt


def _parse_lines(blob):
    out = []
    for raw in blob.split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            o = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


def read_head_tail(fs_path, head=HEAD_BYTES, tail=TAIL_BYTES):
    """Lignes JSON complètes du début et de la fin d'un fichier (comme le picker de Claude Code)."""
    size = os.path.getsize(fs_path)
    with open(fs_path, "rb") as f:
        if size <= head + tail:
            lines = _parse_lines(f.read())
            return lines, lines
        h = f.read(head)
        h = h[: h.rfind(b"\n") + 1] if b"\n" in h else b""
        f.seek(size - tail)
        t = f.read()
        t = t[t.find(b"\n") + 1:] if b"\n" in t else b""
        return _parse_lines(h), _parse_lines(t)


_SKIP_PROMPT_PREFIXES = ("<command-", "<local-command", "<system-reminder", "<bash-", "<user-memory", "Caveat:")


def prompt_text(obj):
    """Texte d'un vrai prompt utilisateur (ignore méta, sous-agents, résultats d'outils)."""
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return None
    if "toolUseResult" in obj:
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    c = msg.get("content")
    texts = []
    if isinstance(c, str):
        texts = [c]
    elif isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                texts.append(b["text"])
            elif isinstance(b, dict) and b.get("type") == "tool_result":
                return None
    for t in texts:
        t2 = t.strip()
        if t2 and not t2.startswith(_SKIP_PROMPT_PREFIXES):
            return " ".join(t2.split())
    return None


def short(s, n):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


# -- sessions en cours -------------------------------------------------------------


def _pid_alive(pid, proc_start=None):
    """Le processus `pid` existe-t-il (et est-ce bien le même, via son heure de démarrage) ?"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes

            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
                return bool(ok) and code.value == 259  # STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            return False
    stat = "/proc/{}/stat".format(pid)
    if os.path.exists("/proc/self/stat"):
        try:
            with open(stat, encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError:
            return False
        if proc_start is not None:
            fields = raw[raw.rfind(")") + 2:].split()
            # champ 22 de /proc/<pid>/stat = heure de démarrage ; Claude Code la note dans procStart
            return len(fields) > 19 and fields[19] == str(proc_start)
        return True
    try:  # macOS et autres : signal 0 (ne tue rien sous Unix)
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def running_sessions(profile):
    """{sessionId: infos} des sessions en cours d'exécution sur cette machine."""
    out = {}
    d = profile.fs(profile.join(profile.config_dir, "sessions"))
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        sid = info.get("sessionId")
        if not sid:
            continue
        ps = info.get("procStart")
        if _pid_alive(info.get("pid"), ps if ps not in (None, "") else None):
            out[sid] = info
    return out


# -- sessions ----------------------------------------------------------------------


class SessionInfo:
    def __init__(self, sid, slug, path, fs_path):
        self.sid = sid
        self.slug = slug
        self.path = path  # chemin logique du .jsonl
        self.fs_path = fs_path
        self.size = 0
        self.mtime = 0.0
        self.root = None  # dossier projet (cwd de départ ou relocatedCwd)
        self.title = None
        self.title_source = None
        self.first_prompt = None
        self.last_prompt = None
        self.created = None
        self.last = None
        self.version = None
        self.git_branch = None
        self.entrypoint = None
        self.running = None

    @property
    def short_id(self):
        return self.sid[:8]

    @property
    def display_title(self):
        return self.title or self.first_prompt or self.last_prompt or "(sans titre)"

    @property
    def last_activity(self):
        return self.last or datetime.fromtimestamp(self.mtime, timezone.utc)


def _session_dir_title(profile, slug, sid):
    p = profile.fs(profile.join(profile.projects_dir, slug, sid, "custom-title.json"))
    try:
        with open(p, encoding="utf-8") as f:
            t = json.load(f).get("customTitle")
        return t if isinstance(t, str) and t.strip() else None
    except (OSError, ValueError, AttributeError):
        return None


def summarize(profile, slug, sid):
    """Résumé d'une session à partir du début et de la fin du transcript (rapide)."""
    path = profile.join(profile.projects_dir, slug, sid + ".jsonl")
    fs_path = profile.fs(path)
    info = SessionInfo(sid, slug, path, fs_path)
    st = os.stat(fs_path)
    info.size, info.mtime = st.st_size, st.st_mtime
    head, tail = read_head_tail(fs_path)
    for o in head:
        if info.root is None and isinstance(o.get("cwd"), str):
            info.root = o["cwd"]
        if info.created is None and o.get("timestamp"):
            info.created = parse_ts(o["timestamp"])
        if info.first_prompt is None:
            info.first_prompt = prompt_text(o)
        if info.version is None and o.get("version"):
            info.version = o["version"]
        if info.entrypoint is None and isinstance(o.get("entrypoint"), str):
            info.entrypoint = o["entrypoint"]
    custom = ai = None
    for o in tail:
        t = o.get("type")
        if t == "custom-title" and o.get("customTitle"):
            custom = o["customTitle"]
        elif t == "ai-title" and o.get("aiTitle"):
            ai = o["aiTitle"]
        elif t == "last-prompt" and o.get("lastPrompt"):
            info.last_prompt = " ".join(str(o["lastPrompt"]).split())
        elif t == "relocated" and o.get("relocatedCwd"):
            info.root = o["relocatedCwd"]
        if o.get("timestamp"):
            ts = parse_ts(o["timestamp"])
            if ts and (info.last is None or ts > info.last):
                info.last = ts
        if o.get("gitBranch"):
            info.git_branch = o["gitBranch"]
        if o.get("version"):
            info.version = o["version"]
    custom = _session_dir_title(profile, slug, sid) or custom
    if custom:
        info.title, info.title_source = custom, "rename"
    elif ai:
        info.title, info.title_source = ai, "ai"
    return info


def iter_project_dirs(profile):
    try:
        names = sorted(os.listdir(profile.fs(profile.projects_dir)))
    except OSError:
        return []
    return [n for n in names if os.path.isdir(profile.fs(profile.join(profile.projects_dir, n)))]


def session_ids_in(profile, slug):
    d = profile.fs(profile.join(profile.projects_dir, slug))
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(n[:-6] for n in names if n.endswith(".jsonl") and is_session_id(n[:-6]))


def list_sessions(profile, slugs=None):
    """Toutes les sessions (ou celles des dossiers `slugs`), de la plus récente à la plus ancienne."""
    running = running_sessions(profile)
    out = []
    for slug in (slugs if slugs is not None else iter_project_dirs(profile)):
        for sid in session_ids_in(profile, slug):
            try:
                info = summarize(profile, slug, sid)
            except OSError:
                continue
            info.running = running.get(sid)
            out.append(info)
    out.sort(key=lambda i: i.last_activity, reverse=True)
    return out


def find_session_files(profile, sid):
    """Slugs de tous les dossiers projet contenant `<sid>.jsonl`."""
    return [s for s in iter_project_dirs(profile)
            if os.path.isfile(profile.fs(profile.join(profile.projects_dir, s, sid + ".jsonl")))]


def head_cwd(profile, slug, sid):
    """cwd de départ d'un transcript (ou relocatedCwd s'il y en a un en fin de fichier)."""
    fs_path = profile.fs(profile.join(profile.projects_dir, slug, sid + ".jsonl"))
    try:
        head, tail = read_head_tail(fs_path, head=32 * 1024, tail=16 * 1024)
    except OSError:
        return None
    for o in reversed(tail):
        if o.get("type") == "relocated" and o.get("relocatedCwd"):
            return o["relocatedCwd"]
    for o in head:
        if isinstance(o.get("cwd"), str):
            return o["cwd"]
    return None


# -- slug sur une machine ------------------------------------------------------------


def drive_case_convention(profile):
    """Convention de casse de la lettre de lecteur observée dans les slugs existants."""
    counts = {"upper": 0, "lower": 0}
    for n in iter_project_dirs(profile):
        c = drive_case_of_slug(n)
        if c:
            counts[c] += 1
    if counts["upper"] == counts["lower"] == 0:
        return None
    return "upper" if counts["upper"] >= counts["lower"] else "lower"


def dirs_for_root(profile, root, max_files=5):
    """Dossiers projet existants dont des transcripts ont `root` pour cwd."""
    ci = profile.style == P.WINDOWS
    found = []
    for n in iter_project_dirs(profile):
        for sid in session_ids_in(profile, n)[:max_files]:
            cwd = head_cwd(profile, n, sid)
            if cwd and (P.same_path(cwd, root, profile.style) if ci else cwd == root):
                found.append(n)
                break
    return found


def resolve_slug(profile, root):
    """Slug à utiliser sur `profile` pour le dossier projet `root` (déjà absolu et résolu).

    Ordre : CLAUDE_CODE_PROJECT_DIR_NAME, dossier existant dont un transcript a ce cwd,
    calcul par la règle (avec la convention de lettre de lecteur de la machine).
    Renvoie (slug, raison, root éventuellement ajusté pour la casse du lecteur).
    """
    if profile.project_dir_name:
        return profile.project_dir_name, "CLAUDE_CODE_PROJECT_DIR_NAME", root
    ci = profile.style == P.WINDOWS
    existing = dirs_for_root(profile, root)
    if existing:
        computed = compute_slug(root)
        best = next((d for d in existing if (d.lower() == computed.lower() if ci else d == computed)), None)
        best = best or next((d for d in existing if slug_matches(d, root, ci)), None) or existing[0]
        return best, "dossier existant (cwd identique)", root
    if profile.style == P.WINDOWS:
        case = drive_case_convention(profile)
        if case:
            root = P.with_drive_case(root, case)
        return compute_slug(root), "calcul" + (" (lecteur : {})".format(case) if case else ""), root
    return compute_slug(root), "calcul", root


def slugs_for_root(profile, root):
    """Tous les dossiers projet correspondant à `root` (pour `list --here`)."""
    ci = profile.style == P.WINDOWS
    if profile.project_dir_name:
        return [profile.project_dir_name]
    out = []
    exact = compute_slug(root)
    for n in iter_project_dirs(profile):
        if (n.lower() == exact.lower()) if ci else n == exact:
            out.append(n)
        elif slug_matches(n, root, ci):
            # Slug long : même préfixe de 200 caractères, on vérifie le cwd comme Claude Code.
            if any((P.same_path(c, root, profile.style) if ci else c == root)
                   for c in (head_cwd(profile, n, s) for s in session_ids_in(profile, n)[:5]) if c):
                out.append(n)
    if not out:
        out = dirs_for_root(profile, root)
    return out
