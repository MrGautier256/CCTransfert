"""Import d'un bundle sur la machine cible : placement, conflits, sauvegardes, journal d'annulation."""

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone

from . import paths as P
from .bundle import BundleReader
from .jsonio import AtomicWriter, split_eol, write_json_atomic
from .rewrite import Report, Rewriter, build_rules
from .store import find_session_files, resolve_slug, running_sessions

DEFAULT_CLEANUP_DAYS = 30


class ImportError_(Exception):
    """Erreur d'import présentée telle quelle à l'utilisateur.

    `code` permet à l'interface graphique de proposer la bonne action :
    `conflict_same` (remplacer ?), `conflict_other` (déplacer ou nouvel ID ?), `running`.
    """

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# -- dossier de l'outil (~/.CCTransfert) ----------------------------------------------


def tool_home():
    return os.environ.get("CCTRANSFERT_HOME") or os.path.join(os.path.expanduser("~"), ".CCTransfert")


def mapping_key(source):
    return "{}|{}".format(source.get("hostname", ""), source.get("project_root", ""))


def load_mappings():
    p = os.path.join(tool_home(), "mappings.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_mapping(source, target_root):
    d = load_mappings()
    d[mapping_key(source)] = {"target": target_root, "updated": datetime.now(timezone.utc).isoformat()}
    write_json_atomic(os.path.join(tool_home(), "mappings.json"), d)


def recalled_mapping(source):
    e = load_mappings().get(mapping_key(source))
    return e.get("target") if isinstance(e, dict) else None


# -- options et résultat --------------------------------------------------------------


class ImportOptions:
    def __init__(self, target_root, maps=(), new_id=False, replace=False, dry_run=False,
                 keep_mtime=False, confirm=None, now=None, relocate=False, slug_override=None, forced_sid=None):
        self.target_root = target_root
        self.forced_sid = forced_sid  # avec new_id : réutilise l'ID montré par l'aperçu
        self.slug_override = slug_override  # slug observé par --calibrate
        self.maps = list(maps)
        self.new_id = new_id
        self.replace = replace
        self.dry_run = dry_run
        self.keep_mtime = keep_mtime
        # confirm(question) -> bool ; None = non interactif (refus par défaut)
        self.confirm = confirm
        self.now = now
        self.relocate = relocate


class ImportResult:
    def __init__(self):
        self.old_sid = None
        self.new_sid = None
        self.slug = None
        self.slug_reason = None
        self.target_root = None
        self.transcript = None
        self.actions = []  # (op, chemin logique, détail)
        self.report = Report()
        self.warnings = []
        self.journal_dir = None
        self.git_warnings = []
        self.rules = []


def _ask(opts, question):
    return bool(opts.confirm and opts.confirm(question))


# -- journal / sauvegardes ---------------------------------------------------------------


class Journal:
    """Trace de tout ce qu'un import a écrit, pour `CCTransfert undo`."""

    def __init__(self, session_ids, target_root, bundle):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = os.path.join(tool_home(), "backups")
        name = "{}_{}".format(stamp, session_ids[-1][:8])
        d = os.path.join(base, name)
        i = 1
        while os.path.exists(d):
            i += 1
            d = os.path.join(base, "{}-{}".format(name, i))
        self.dir = d
        self.data = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "bundle": os.path.abspath(bundle) if bundle else None,
            "session_ids": session_ids,
            "target_root": target_root,
            "created": [],  # {"path": physique, "sha256"}
            "backups": [],  # {"path": physique, "backup": nom dans files/}
            "dirs": [],  # dossiers créés
            "history_lines": [],
            "history_file": None,
            "undone": False,
        }
        self._n = 0

    def ensure(self):
        os.makedirs(os.path.join(self.dir, "files"), exist_ok=True)

    def backup(self, fs_path):
        """Déplace un fichier existant dans la sauvegarde avant remplacement ou suppression."""
        self.ensure()
        self._n += 1
        name = "{:05d}_{}".format(self._n, os.path.basename(fs_path))
        dest = os.path.join(self.dir, "files", name)
        shutil.copy2(fs_path, dest)
        self.data["backups"].append({"path": fs_path, "backup": name})
        self.save()

    def created(self, fs_path, sha):
        self.data["created"].append({"path": fs_path, "sha256": sha})

    def mkdirs(self, fs_dir):
        """Crée `fs_dir` (et ses parents) en notant les dossiers réellement créés."""
        missing = []
        d = fs_dir
        while d and not os.path.isdir(d):
            missing.append(d)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        os.makedirs(fs_dir, exist_ok=True)
        for m in reversed(missing):
            self.data["dirs"].append(m)

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        write_json_atomic(os.path.join(self.dir, "journal.json"), self.data)


def _sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _walk(fs_dir):
    for base, _dirs, files in os.walk(fs_dir):
        for n in files:
            yield os.path.join(base, n)


def rollback(journal):
    """Annule un import interrompu (ou terminé) d'après son journal."""
    errors = []
    for e in reversed(journal.data["created"]):
        try:
            os.unlink(e["path"])
        except FileNotFoundError:
            pass
        except OSError as ex:
            errors.append(str(ex))
    for e in reversed(journal.data["backups"]):
        src = os.path.join(journal.dir, "files", e["backup"])
        try:
            os.makedirs(os.path.dirname(e["path"]), exist_ok=True)
            shutil.copy2(src, e["path"])
        except OSError as ex:
            errors.append(str(ex))
    hf = journal.data.get("history_file")
    if hf and journal.data.get("history_lines"):
        _remove_history_lines(hf, journal.data["history_lines"])
    # Dossiers créés par l'import, plus les dossiers vides que Claude Code a pu y ajouter
    # en reprenant la session (ex. `memory/`, `session-env/<id>`).
    for d in sorted(journal.data["dirs"] + journal.data.get("side_dirs", []), key=len, reverse=True):
        _remove_empty_tree(d)
    return errors


def _remove_empty_tree(d):
    """Supprime `d` s'il ne contient (récursivement) que des dossiers vides."""
    if not os.path.isdir(d):
        return
    for base, _dirs, files in os.walk(d):
        if files:
            return
    for base, _dirs, _files in sorted(os.walk(d), key=lambda t: len(t[0]), reverse=True):
        try:
            os.rmdir(base)
        except OSError:
            return


def _remove_history_lines(fs_path, lines):
    try:
        with open(fs_path, "rb") as f:
            content = f.read().split(b"\n")
    except OSError:
        return
    targets = {ln.encode("utf-8") for ln in lines}
    kept = [ln for ln in content if ln not in targets]
    with AtomicWriter(fs_path) as out:
        out.write(b"\n".join(kept))


# -- import --------------------------------------------------------------------------------


class _Sink:
    """Destination d'écriture : fichier atomique ou rien (dry-run), avec sha256."""

    def __init__(self, fs_path, dry):
        self.h = hashlib.sha256()
        self.size = 0
        self.dry = dry
        self._w = None if dry else AtomicWriter(fs_path)
        self.f = None if dry else self._w.__enter__()

    def write(self, b):
        self.h.update(b)
        self.size += len(b)
        if self.f is not None:
            self.f.write(b)

    def close(self, ok=True):
        if self._w is not None:
            self._w.__exit__(None if ok else Exception, None, None)


def _content_kind(name):
    n = name.lower()
    if n.endswith(".jsonl"):
        return "jsonl"
    if n.endswith(".json"):
        return "json"
    if n.endswith((".md", ".txt")):
        return "text"
    return "raw"


def import_bundle(bundle_path, target, opts):
    """Importe le bundle sur la machine `target` (Profile). Renvoie un ImportResult."""
    res = ImportResult()
    with BundleReader(bundle_path) as rd:
        rd.verify()
        return _import(rd, bundle_path, target, opts, res)


def _import(rd, bundle_path, target, opts, res):
    m = rd.manifest
    src = m["source"]
    old = m["session"]["id"]
    res.old_sid = old
    new = (opts.forced_sid or str(uuid.uuid4())) if opts.new_id else old
    res.new_sid = new
    sty = target.style

    root = opts.target_root
    if not P.is_abs(root, sty):
        raise ImportError_("le dossier cible doit être un chemin absolu : {}".format(root))
    root = P.normalize(root, sty)
    slug, reason, root = resolve_slug(target, root)
    if opts.slug_override and opts.slug_override != slug:
        slug, reason = opts.slug_override, "calibration (claude -p)"
    res.slug, res.slug_reason, res.target_root = slug, reason, root
    if len(slug) > 200 and reason == "calcul":
        res.warnings.append("chemin long : slug calculé avec le hash de Claude Code ({}). "
                            "Utilisez --calibrate pour le vérifier.".format(slug[-12:]))

    # Session en cours sur la cible ?
    running = running_sessions(target)
    for sid in {old, new}:
        if sid in running:
            raise ImportError_("la session {} est en cours sur cette machine (PID {}). "
                               "Quittez-la (/exit) avant d'importer.".format(sid[:8], running[sid].get("pid")),
                               code="running")

    cfg = target.config_dir
    pj = target.projects_dir
    transcript = target.join(pj, slug, new + ".jsonl")
    res.transcript = transcript

    # Conflits d'ID.
    to_remove = []  # chemins physiques à sauvegarder puis supprimer
    to_replace_dirs = []  # dossiers dont le contenu sera remplacé
    for other in find_session_files(target, new):
        same = other == slug
        if same:
            if opts.relocate:
                raise ImportError_("la session est déjà dans ce dossier projet.", code="already_here")
            if not (opts.replace or _ask(opts, "La session {} existe déjà dans ce dossier projet (aller-retour ?). "
                                                "La remplacer (l'ancienne version sera sauvegardée) ?".format(new[:8]))):
                raise ImportError_("la session existe déjà ici. Relancez avec --replace pour la remplacer, "
                                   "ou --new-id pour créer une copie.", code="conflict_same")
            res.actions.append(("remplacer", target.join(pj, slug, new + ".jsonl"), "ancienne version sauvegardée"))
        else:
            if not (opts.replace or opts.relocate or _ask(
                    opts, "La session {} existe déjà dans un autre dossier projet ({}).\n"
                          "Deux copies du même ID rendraient `claude --resume` inutilisable.\n"
                          "La déplacer ici (l'ancienne sera sauvegardée puis supprimée) ?".format(new[:8], other))):
                raise ImportError_("la session existe déjà dans {}. Utilisez --new-id pour importer une copie "
                                   "indépendante, ou --replace pour la déplacer ici.".format(other),
                                   code="conflict_other")
            res.actions.append(("déplacer", target.join(pj, other, new + ".jsonl"), "sauvegardée puis supprimée"))
        to_remove.append(target.fs(target.join(pj, other, new + ".jsonl")))
        to_replace_dirs.append(target.fs(target.join(pj, other, new)))
    to_replace_dirs.append(target.fs(target.join(cfg, "file-history", new)))
    to_replace_dirs.append(target.fs(target.join(cfg, "tasks", re.sub(r"[^a-zA-Z0-9_-]", "-", new))))

    # Règles de réécriture.
    for a, b in opts.maps:
        if not P.is_abs(a, src["style"]) or not P.is_abs(b, sty):
            raise ImportError_("--map {}={} : il faut deux chemins absolus (source puis cible)".format(a, b))
    rules = build_rules(src, target, root, slug, opts.maps)
    rw = Rewriter(rules, src["style"], sty, old_sid=old, new_sid=new, report=res.report)
    res.rules = rules

    # Plan d'écriture : (membre, chemin logique cible, traitement, mtime d'origine, type)
    plan = []
    memory_items = []
    history_member = None
    for member, e in sorted(rd.entries.items()):
        kind = e.get("kind")
        parts = member.split("/")
        if member == "history.jsonl":
            history_member = member
            continue
        if parts[0] == "session":
            if member == "session/{}.jsonl".format(old):
                dest = transcript
            elif parts[1] == old and len(parts) > 2:
                rel = [p.replace(old, new) for p in parts[2:]]
                dest = target.join(pj, slug, new, *rel)
            else:
                res.warnings.append("fichier de session inattendu ignoré : {}".format(member))
                continue
        elif parts[0] == "file-history" and len(parts) > 2:
            dest = target.join(cfg, "file-history", new, *parts[2:])
            plan.append((member, dest, "raw", e.get("mtime"), kind))
            continue
        elif parts[0] == "tasks" and len(parts) > 2:
            dest = target.join(cfg, "tasks", re.sub(r"[^a-zA-Z0-9_-]", "-", new), *parts[2:])
        elif parts[0] == "todos" and len(parts) == 2:
            dest = target.join(cfg, "todos", parts[1].replace(old, new))
        elif parts[0] == "plans" and len(parts) == 2:
            dest = target.join(cfg, "plans", parts[1])
        elif parts[0] == "memory":
            memory_items.append((member, target.join(pj, slug, "memory", *parts[1:]), e))
            continue
        else:
            res.warnings.append("fichier ignoré : {}".format(member))
            continue
        plan.append((member, dest, _content_kind(member), e.get("mtime"), kind))

    now = opts.now or time.time()
    journal = None if opts.dry_run else Journal([old, new] if old != new else [new], root, bundle_path)
    if journal is not None:
        senv = target.fs(target.join(cfg, "session-env", new))
        if not os.path.exists(senv):
            journal.data["side_dirs"] = [senv]

    def set_mtime(fs_path, orig, kind):
        if opts.dry_run:
            return
        keep = opts.keep_mtime or kind == "file-history"
        t = orig if (keep and orig) else now
        try:
            os.utime(fs_path, (t, t))
        except OSError:
            pass

    def produce(member, how):
        """Contenu cible d'un membre, sous forme d'itérable d'octets (streaming pour les .jsonl)."""
        if how == "jsonl":
            with rd.open(member) as f:
                for n, raw in enumerate(f, 1):
                    body, eol = split_eol(raw)
                    yield rw.rewrite_line(body, "{}:{}".format(member, n)) + eol
            return
        data = rd.read(member)
        if how == "json":
            yield rw.rewrite_json_bytes(data, member)
        elif how == "text":
            yield rw.rewrite_text_bytes(data, member)
        else:
            yield data

    try:
        # 1. Sauvegarde puis suppression de ce qui est remplacé ou déplacé.
        if not opts.dry_run:
            for p in to_remove:
                if os.path.isfile(p):
                    journal.backup(p)
                    os.unlink(p)
            for d in to_replace_dirs:
                if os.path.isdir(d):
                    for f in sorted(_walk(d)):
                        journal.backup(f)
                        os.unlink(f)
                    subdirs = [base for base, _d, _f in os.walk(d)]
                    for base in sorted(subdirs, key=len, reverse=True):
                        try:
                            os.rmdir(base)
                        except OSError:
                            pass
        else:
            for d in to_replace_dirs:
                if os.path.isdir(d) and any(True for _ in _walk(d)):
                    res.actions.append(("remplacer", d, "contenu sauvegardé puis remplacé"))

        # 2. Écriture des fichiers.
        for member, dest, how, orig_mtime, kind in plan:
            fs_dest = target.fs(dest)
            existed = os.path.isfile(fs_dest)
            if kind == "plan" and existed:
                newdata = b"".join(produce(member, how))
                with open(fs_dest, "rb") as f:
                    if f.read() == newdata:
                        res.actions.append(("identique", dest, "plan déjà présent"))
                        continue
                res.actions.append(("remplacer", dest, "plan (ancienne version sauvegardée)"))
                if not opts.dry_run:
                    journal.backup(fs_dest)
            else:
                res.actions.append(("créer" if not existed else "remplacer", dest, kind))
                if existed and not opts.dry_run:
                    journal.backup(fs_dest)
            if not opts.dry_run:
                journal.mkdirs(os.path.dirname(fs_dest))
            sink = _Sink(fs_dest, opts.dry_run)
            ok = False
            try:
                for chunk in produce(member, how):
                    sink.write(chunk)
                ok = True
            finally:
                sink.close(ok)
            if not opts.dry_run:
                journal.created(fs_dest, sink.h.hexdigest())
                set_mtime(fs_dest, orig_mtime, kind)

        # 3. Mémoire du projet : fusion sans écrasement.
        for member, dest, e in memory_items:
            data = b"".join(produce(member, _content_kind(member)))
            fs_dest = target.fs(dest)
            if os.path.isfile(fs_dest):
                with open(fs_dest, "rb") as f:
                    if f.read() == data:
                        res.actions.append(("identique", dest, "mémoire"))
                        continue
                stem, ext = os.path.splitext(dest)
                dest = "{}.importe-{}{}".format(stem, re.sub(r"[^A-Za-z0-9_-]", "-", src.get("hostname") or "source"), ext)
                fs_dest = target.fs(dest)
                res.warnings.append("mémoire : {} existe déjà avec un autre contenu, version importée écrite "
                                    "à côté ({}). Fusionnez à la main.".format(P.basename(dest, sty), P.basename(dest, sty)))
            res.actions.append(("créer", dest, "mémoire"))
            if not opts.dry_run:
                if os.path.isfile(fs_dest):
                    journal.backup(fs_dest)
                journal.mkdirs(os.path.dirname(fs_dest))
                with AtomicWriter(fs_dest) as f:
                    f.write(data)
                journal.created(fs_dest, hashlib.sha256(data).hexdigest())
                set_mtime(fs_dest, e.get("mtime"), "memory")

        # 4. Historique des prompts (flèche ↑).
        if history_member:
            hist = target.fs(target.join(cfg, "history.jsonl"))
            existing = set()
            try:
                with open(hist, "rb") as f:
                    existing = {ln.rstrip(b"\r\n") for ln in f}
            except OSError:
                pass
            new_lines = []
            for raw in rd.read(history_member).split(b"\n"):
                if not raw.strip():
                    continue
                line = rw.rewrite_line(raw, "history.jsonl")
                if line not in existing:
                    new_lines.append(line)
            res.actions.append(("ajouter", target.join(cfg, "history.jsonl"),
                                "{} entrée(s) d'historique".format(len(new_lines))))
            if new_lines and not opts.dry_run:
                journal.data["history_file"] = hist
                journal.data["history_lines"] = [ln.decode("utf-8") for ln in new_lines]
                journal.save()
                need_nl = False
                if os.path.isfile(hist) and os.path.getsize(hist) > 0:
                    with open(hist, "rb") as f:
                        f.seek(-1, os.SEEK_END)
                        need_nl = f.read(1) != b"\n"
                with open(hist, "ab") as f:
                    f.write((b"\n" if need_nl else b"") + b"\n".join(new_lines) + b"\n")
    except BaseException:
        if journal is not None:
            rollback(journal)
            journal.data["undone"] = True
            journal.data["aborted"] = True
            journal.save()
        raise

    if not opts.dry_run:
        journal.save()
        res.journal_dir = journal.dir

    # Avertissement nettoyage automatique si on garde des dates anciennes.
    if opts.keep_mtime:
        days = target.settings_cleanup_days or DEFAULT_CLEANUP_DAYS
        tm = rd.entries.get("session/{}.jsonl".format(old), {}).get("mtime")
        if tm and now - tm > days * 86400:
            res.warnings.append("--keep-mtime : le transcript date de plus de {} jours ; Claude Code risque de le "
                                "supprimer au prochain démarrage (cleanupPeriodDays).".format(days))

    res.git_warnings = git_check(m, target, root)
    return res


def relocate_session(profile, info, root, maps=(), dry_run=False, keep_mtime=False):
    """Déplace une session vers un autre dossier projet de la même machine (export + import)."""
    import tempfile

    from .exporter import export_session

    if info.running:
        raise ImportError_("la session {} est en cours (PID {}) : quittez-la avant de la déplacer.".format(
            info.short_id, info.running.get("pid")), code="running")
    if info.root and P.same_path(info.root, root, profile.style):
        raise ImportError_("la session est déjà rattachée à {}".format(root), code="already_here")
    tmpdir = tempfile.mkdtemp(prefix="cct-relocate-")
    try:
        tmp = os.path.join(tmpdir, "session.CCTransfert")
        export_session(profile, info, tmp, with_memory=False, with_file_history=True, with_history=False)
        opts = ImportOptions(root, maps=maps, new_id=False, replace=True, dry_run=dry_run,
                             keep_mtime=keep_mtime, relocate=True)
        return import_bundle(tmp, profile, opts)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def git_check(manifest, target, root):
    """Compare branche et HEAD du dossier cible avec ceux du manifest."""
    gi = (manifest.get("session") or {}).get("git")
    if not gi or not target.is_local:
        return []
    from .exporter import git_info

    here = git_info(target.fs(root))
    if here is None:
        return ["le dossier cible n'est pas un dépôt git (source : branche {}, HEAD {}). "
                "Synchronisez le code séparément.".format(gi.get("branch"), (gi.get("head") or "")[:10])]
    out = []
    if gi.get("branch") and here.get("branch") != gi.get("branch"):
        out.append("branche différente : source {}, ici {}".format(gi.get("branch"), here.get("branch")))
    if gi.get("head") and here.get("head") != gi.get("head"):
        out.append("HEAD différent : source {}, ici {}".format(gi["head"][:10], (here.get("head") or "")[:10]))
    if gi.get("dirty"):
        out.append("la copie de travail source avait des modifications non commitées")
    return out


# -- undo --------------------------------------------------------------------------------------


def list_journals():
    base = os.path.join(tool_home(), "backups")
    try:
        names = sorted(os.listdir(base), reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(base, n, "journal.json")
        try:
            with open(p, encoding="utf-8") as f:
                out.append((os.path.join(base, n), json.load(f)))
        except (OSError, ValueError):
            continue
    return out


def last_journal():
    for d, data in list_journals():
        if not data.get("undone"):
            return d, data
    return None, None


def undo_check(data):
    """Fichiers créés par l'import et modifiés depuis (ex. session reprise)."""
    changed = []
    for e in data["created"]:
        p = e["path"]
        if os.path.isfile(p) and _sha256_file(p) != e["sha256"]:
            changed.append(p)
    return changed


def undo(journal_dir, data):
    j = Journal.__new__(Journal)
    j.dir, j.data, j._n = journal_dir, data, 0
    errors = rollback(j)
    data["undone"] = True
    data["undone_at"] = datetime.now(timezone.utc).isoformat()
    j.save()
    return errors
