"""Export d'une session vers un bundle `.CCTransfert`."""

import json
import os
import re
import subprocess
from datetime import datetime, timezone

from . import __version__
from . import paths as P
from .bundle import BundleWriter
from .profile import no_window
from .store import parse_ts, prompt_text


def full_scan(fs_path):
    """Parcours complet d'un transcript : compteurs, dates, titres, plans référencés."""
    r = {
        "messages": 0, "user_prompts": 0, "created": None, "last": None, "git_branch": None,
        "version": None, "root": None, "relocated": None, "custom_title": None, "ai_title": None,
        "first_prompt": None, "entrypoint": None, "plan_slugs": set(), "plan_paths": set(), "bad_lines": 0,
    }
    with open(fs_path, "rb") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                o = json.loads(raw.decode("utf-8"))
            except ValueError:
                r["bad_lines"] += 1
                continue
            if not isinstance(o, dict):
                continue
            t = o.get("type")
            if t in ("user", "assistant") and not o.get("isSidechain"):
                r["messages"] += 1
                if t == "user" and prompt_text(o):
                    r["user_prompts"] += 1
                    if r["first_prompt"] is None:
                        r["first_prompt"] = prompt_text(o)
            if r["entrypoint"] is None and isinstance(o.get("entrypoint"), str):
                r["entrypoint"] = o["entrypoint"]
            if r["root"] is None and isinstance(o.get("cwd"), str):
                r["root"] = o["cwd"]
            ts = parse_ts(o.get("timestamp"))
            if ts:
                if r["created"] is None or ts < r["created"]:
                    r["created"] = ts
                if r["last"] is None or ts > r["last"]:
                    r["last"] = ts
            if o.get("gitBranch"):
                r["git_branch"] = o["gitBranch"]
            if o.get("version"):
                r["version"] = o["version"]
            if isinstance(o.get("slug"), str) and o["slug"]:
                r["plan_slugs"].add(o["slug"])
            if t == "custom-title" and o.get("customTitle"):
                r["custom_title"] = o["customTitle"]
            elif t == "ai-title" and o.get("aiTitle"):
                r["ai_title"] = o["aiTitle"]
            elif t == "relocated" and o.get("relocatedCwd"):
                r["relocated"] = o["relocatedCwd"]
            att = o.get("attachment")
            if isinstance(att, dict) and isinstance(att.get("planFilePath"), str):
                r["plan_paths"].add(att["planFilePath"])
    return r


def git_info(root):
    """HEAD, branche et remote du dépôt git du projet (si accessible localement)."""
    if not root or not os.path.isdir(root):
        return None

    def g(*args):
        try:
            p = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True,
                               timeout=10, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                               **no_window())
        except (OSError, subprocess.SubprocessError):
            return None
        return p.stdout.strip() if p.returncode == 0 else None

    head = g("rev-parse", "HEAD")
    if not head:
        return None
    return {
        "head": head,
        "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
        "remote": g("remote", "get-url", "origin"),
        "dirty": bool(g("status", "--porcelain")),
    }


def _walk_files(fs_dir):
    for base, dirs, files in os.walk(fs_dir):
        dirs.sort()
        for n in sorted(files):
            full = os.path.join(base, n)
            rel = os.path.relpath(full, fs_dir).replace(os.sep, "/")
            yield rel, full


def collect(profile, info, scan, with_memory=False, with_file_history=True):
    """Liste (chemin_dans_bundle, type, chemin_physique) des fichiers de la session."""
    sid, slug = info.sid, info.slug
    cfg = profile.config_dir
    out = [("session/{}.jsonl".format(sid), "transcript", info.fs_path)]

    sdir = profile.fs(profile.join(profile.projects_dir, slug, sid))
    if os.path.isdir(sdir):
        for rel, full in _walk_files(sdir):
            kind = "subagent" if rel.startswith("subagents/") and rel.endswith(".jsonl") else "session-dir"
            out.append(("session/{}/{}".format(sid, rel), kind, full))

    if with_file_history:
        fh = profile.fs(profile.join(cfg, "file-history", sid))
        if os.path.isdir(fh):
            for rel, full in _walk_files(fh):
                out.append(("file-history/{}/{}".format(sid, rel), "file-history", full))

    # Plans : <cfg>/plans/<slug-plan>.md et <slug-plan>-agent-*.md, plus les planFilePath
    # pointant dans <cfg>/plans.
    plans_dir = profile.fs(profile.join(cfg, "plans"))
    if os.path.isdir(plans_dir):
        names = set()
        for n in os.listdir(plans_dir):
            for ps in scan["plan_slugs"]:
                if n == ps + ".md" or (n.startswith(ps + "-agent-") and n.endswith(".md")):
                    names.add(n)
        for pp in scan["plan_paths"]:
            if P.is_under(pp, profile.join(cfg, "plans"), profile.style):
                names.add(P.basename(pp, profile.style))
        for n in sorted(names):
            full = os.path.join(plans_dir, n)
            if os.path.isfile(full):
                out.append(("plans/{}".format(n), "plan", full))

    # Listes de tâches : <cfg>/tasks/<id assaini>/
    task_dir = profile.fs(profile.join(cfg, "tasks", re.sub(r"[^a-zA-Z0-9_-]", "-", sid)))
    if os.path.isdir(task_dir):
        for rel, full in _walk_files(task_dir):
            out.append(("tasks/{}/{}".format(sid, rel), "task", full))

    # Anciennes versions : <cfg>/todos/<sid>-*.json
    todos = profile.fs(profile.join(cfg, "todos"))
    if os.path.isdir(todos):
        for n in sorted(os.listdir(todos)):
            if n.startswith(sid) and n.endswith(".json"):
                out.append(("todos/{}".format(n), "todo", os.path.join(todos, n)))

    if with_memory:
        mem = profile.fs(profile.join(profile.projects_dir, slug, "memory"))
        if os.path.isdir(mem):
            for rel, full in _walk_files(mem):
                out.append(("memory/{}".format(rel), "memory", full))
    return out


def history_lines(profile, sid):
    """Lignes brutes de `history.jsonl` appartenant à la session."""
    p = profile.fs(profile.join(profile.config_dir, "history.jsonl"))
    out = []
    try:
        with open(p, "rb") as f:
            for raw in f:
                if sid.encode() not in raw:
                    continue
                try:
                    o = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(o, dict) and o.get("sessionId") == sid:
                    out.append(raw if raw.endswith(b"\n") else raw + b"\n")
    except OSError:
        pass
    return out


def _slugify_name(s, n):
    s = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", s or "")
    s = re.sub(r"\s+", "-", s.strip())
    s = s.strip(".-")
    return s[:n].rstrip(".-") or "session"


def default_name(info, root, style):
    project = P.basename(root, style) if root else "projet"
    date = info.last_activity.astimezone().strftime("%Y-%m-%d")
    return "{}_{}_{}_{}.CCTransfert".format(
        _slugify_name(project, 40), _slugify_name(info.display_title, 40), date, info.short_id)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if dt else None


def build_manifest(profile, info, scan, options):
    root = scan["relocated"] or scan["root"] or info.root
    # Titre : /rename (y compris custom-title.json) > titre généré > aucun.
    custom = (info.title if info.title_source == "rename" else None) or scan["custom_title"]
    if custom:
        title, title_source = custom, "rename"
    elif scan["ai_title"]:
        title, title_source = scan["ai_title"], "ai"
    else:
        title, title_source = None, None
    gi = git_info(profile.fs(root)) if root and profile.is_local else None
    return {
        "tool": {"name": "CCTransfert", "version": __version__},
        "created_at": _iso(datetime.now(timezone.utc)),
        "source": dict(profile.to_dict(), project_root=root, slug=info.slug),
        "session": {
            "id": info.sid,
            "title": title,
            "title_source": title_source,
            "first_prompt": scan["first_prompt"],
            "created": _iso(scan["created"]),
            "last_activity": _iso(scan["last"]),
            "messages": scan["messages"],
            "user_prompts": scan["user_prompts"],
            "git_branch": scan["git_branch"],
            "git": gi,
            "claude_version": scan["version"],
            "entrypoint": scan["entrypoint"],
            "plan_slugs": sorted(scan["plan_slugs"]),
        },
        "options": options,
    }


def export_session(profile, info, out_path, with_memory=False, with_file_history=True, with_history=False):
    """Écrit le bundle et renvoie le manifest."""
    scan = full_scan(info.fs_path)
    files = collect(profile, info, scan, with_memory=with_memory, with_file_history=with_file_history)
    options = {"with_memory": with_memory, "file_history": with_file_history, "with_history": with_history}
    manifest = build_manifest(profile, info, scan, options)
    w = BundleWriter(out_path)
    try:
        for member, kind, full in files:
            w.add_file(member, full, kind)
        if with_history:
            lines = history_lines(profile, info.sid)
            if lines:
                w.add_bytes("history.jsonl", b"".join(lines), "history")
        return w.close(manifest)
    except BaseException:
        w.abort()
        raise
