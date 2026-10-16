"""Interface en ligne de commande de CCTransfert."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import __version__
from . import doctor as doctor_mod
from . import paths as P
from . import ui
from .bundle import BundleError, BundleReader, encrypt_file
from .exporter import default_name, export_session
from .importer import (
    ImportError_, ImportOptions, import_bundle, last_journal, recalled_mapping, remember_mapping,
    undo, undo_check,
)
from .profile import local_profile
from .slug import MAX_SLUG, compute_slug
from .store import (
    SDK_ENTRYPOINTS, find_session_files, iter_project_dirs, list_sessions, parse_ts, resolve_slug,
    running_sessions, slugs_for_root,
)

KIND_LABELS = {
    "transcript": "transcript", "subagent": "sous-agents", "session-dir": "fichiers de session",
    "file-history": "file-history (rewind)", "plan": "plans", "task": "tâches", "todo": "todos",
    "memory": "mémoire du projet", "history": "historique des prompts",
}

# Variables d'environnement d'une session Claude Code parente, à retirer pour lancer `claude -p`.
_CHILD_ENV_DROP = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
                   "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID",
                   "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_SESSION_ATTENDED", "AI_AGENT")


class CliError(Exception):
    pass


# -- sélection des sessions -------------------------------------------------------------


def scope_sessions(profile, args):
    if getattr(args, "all", False):
        return list_sessions(profile), "toutes les sessions"
    d = getattr(args, "dir", None) or os.getcwd()
    real = os.path.realpath(os.path.expanduser(d))
    return list_sessions(profile, slugs_for_root(profile, real)), "sessions de {}".format(real)


def _tilde(path):
    home = os.path.expanduser("~")
    if path and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path or "?"


def print_sessions(sessions):
    rows = []
    for i, s in enumerate(sessions, 1):
        mark = ("● " if s.running else "") + ("[-p] " if s.entrypoint in SDK_ENTRYPOINTS else "")
        rows.append([str(i), s.short_id, ui.fmt_dt(s.last_activity), ui.human_size(s.size),
                     mark + s.display_title, _tilde(s.root)])
    print(ui.table(["N°", "ID", "Dernière activité", "Taille", "Titre", "Dossier"], rows,
                   flex_col=[5, 4], left_clip=[5]))
    if any(s.running for s in sessions):
        print("● = session en cours d'exécution")
    if any(s.entrypoint in SDK_ENTRYPOINTS for s in sessions):
        print("[-p] = session créée par `claude -p` ou le SDK : Claude Code ne l'affiche pas dans son picker")


def find_by_selector(profile, sel, scope):
    """Retrouve une session par N° (dans `scope`), id (ou début d'id) ou nom."""
    if sel.isdigit() and len(sel) <= 4 and 1 <= int(sel) <= len(scope):
        return scope[int(sel) - 1]
    everything = None
    if re.fullmatch(r"[0-9a-fA-F-]{4,36}", sel):
        everything = list_sessions(profile)
        m = [s for s in everything if s.sid.startswith(sel.lower())]
        if len(m) == 1:
            return m[0]
        if len(m) > 1:
            raise CliError("l'id « {} » est ambigu ({} sessions). Donnez plus de caractères.".format(sel, len(m)))
    low = sel.lower()
    if everything is None:
        everything = list_sessions(profile)
    for pool in (scope, everything):
        exact = [s for s in pool if (s.title or "").lower() == low]
        if len(exact) == 1:
            return exact[0]
        sub = [s for s in pool if low in s.display_title.lower()]
        if len(sub) == 1:
            return sub[0]
        if len(sub) > 1:
            print_sessions(sub)
            raise CliError("plusieurs sessions correspondent à « {} ». Précisez (id ou N°).".format(sel))
    raise CliError("aucune session ne correspond à « {} ».".format(sel))


def pick_session(profile, sessions, label, verb="exporter"):
    """Picker interactif : liste numérotée et saisie du numéro."""
    showing_all = label == "toutes les sessions"
    while True:
        print("\n{} :".format(label[0].upper() + label[1:]))
        if sessions:
            print_sessions(sessions)
        else:
            print("  (aucune)")
        choices = "numéro" + ("" if showing_all else ", t = toutes les sessions") + ", q = quitter"
        r = ui.ask("\nSession à {} ({}) : ".format(verb, choices))
        if r is None:
            return None
        r = r.strip().lower()
        if r in ("", "q"):
            return None
        if r == "t" and not showing_all:
            sessions, label, showing_all = list_sessions(profile), "toutes les sessions", True
            continue
        if r.isdigit() and 1 <= int(r) <= len(sessions):
            return sessions[int(r) - 1]
        print("Choix invalide.")


def select_session(profile, args, verb="exporter"):
    scope, label = scope_sessions(profile, args)
    if args.session:
        return find_by_selector(profile, args.session, scope)
    if getattr(args, "latest", False):
        if not scope:
            raise CliError("aucune session dans ce périmètre ({}).".format(label))
        return scope[0]
    if not ui.interactive():
        raise CliError("précisez la session (ID, nom, N° ou --latest) : pas de terminal interactif.")
    return pick_session(profile, scope, label, verb)


# -- commandes ------------------------------------------------------------------------------


def cmd_list(args):
    profile = local_profile()
    sessions, label = scope_sessions(profile, args)
    if not sessions:
        print("Aucune session ({}).".format(label))
        if not args.all:
            print("Astuce : `CCTransfert list --all` pour toutes les sessions de la machine.")
        return 0
    print("{} ({}) :\n".format(label[0].upper() + label[1:], len(sessions)))
    print_sessions(sessions)
    return 0


def bundle_summary(m, verified=None):
    s, src = m["session"], m["source"]
    L = []
    L.append("Session      : {}  {}".format(s["id"], "« {} »".format(s["title"]) if s.get("title") else ""))
    if s.get("first_prompt"):
        L.append("1er prompt   : {}".format(ui.clip(s["first_prompt"], 100)))
    L.append("Activité     : créée {}, dernière {}  ({} messages)".format(
        ui.fmt_dt(parse_ts(s.get("created"))), ui.fmt_dt(parse_ts(s.get("last_activity"))), s.get("messages", "?")))
    if s.get("entrypoint") in SDK_ENTRYPOINTS:
        L.append("               session créée par `claude -p`/SDK : absente du picker, reprise par ID")
    L.append("Source       : {}@{} ({}{}), Claude Code {}".format(
        src.get("user"), src.get("hostname"), src.get("os"), ", WSL" if src.get("wsl") else "",
        src.get("claude_version") or s.get("claude_version") or "?"))
    L.append("Projet source: {}".format(src.get("project_root")))
    g = s.get("git") or {}
    if g or (s.get("git_branch") and s.get("git_branch") != "HEAD"):
        L.append("Git          : branche {}{}{}".format(
            g.get("branch") or s.get("git_branch") or "?", ", HEAD " + g["head"][:10] if g.get("head") else "",
            " (modifications non commitées)" if g.get("dirty") else ""))
        if g.get("remote"):
            L.append("               remote {}".format(g["remote"]))
    kinds = {}
    for e in m.get("files", []):
        k = kinds.setdefault(e.get("kind"), [0, 0])
        k[0] += 1
        k[1] += e.get("size") or 0
    parts = ["{} {} ({})".format(n, KIND_LABELS.get(k, k), ui.human_size(sz)) for k, (n, sz) in kinds.items()]
    L.append("Contenu      : " + ", ".join(parts))
    if verified is not None:
        L.append("Intégrité    : " + ("sha256 vérifiés ✓" if verified else "NON vérifiée"))
    return "\n".join(L)


SECRETS_WARNING = ("Attention : le transcript contient tout ce qui est passé dans la session (sorties de "
                   "commandes, fichiers lus…), y compris d'éventuels secrets en clair. Transférez-le par "
                   "un canal de confiance (ou utilisez --encrypt).")


def cmd_export(args):
    profile = local_profile(with_claude_version=True)
    info = select_session(profile, args)
    if info is None:
        print("Export annulé.")
        return 1
    if info.running:
        ui.warn("la session {} est en cours (PID {}). Faites /exit dedans avant d'exporter, sinon "
                "la fin de la conversation manquera.".format(info.short_id, info.running.get("pid")))
        if not args.yes and not ui.ask_yes("Exporter quand même ?"):
            return 1
    name = default_name(info, info.root, profile.style)
    out = args.output
    if out:
        out = os.path.expanduser(out)
        if os.path.isdir(out) or out.endswith(("/", os.sep)):
            out = os.path.join(out, name)
    else:
        out = os.path.join(os.getcwd(), name)
    out = os.path.abspath(out)
    if os.path.exists(out) and not args.yes and not ui.ask_yes("{} existe déjà. L'écraser ?".format(out)):
        return 1
    print("Export de « {} » ({})…".format(ui.clip(info.display_title, 60), info.short_id))
    m = export_session(profile, info, out, with_memory=args.with_memory,
                       with_file_history=not args.no_file_history, with_history=args.with_history)
    if args.encrypt:
        out = encrypt_file(out)
    print("\nBundle créé : {} ({})\n".format(out, ui.human_size(os.path.getsize(out))))
    print(bundle_summary(m))
    print()
    ui.warn(SECRETS_WARNING)
    print("\nSur l'autre machine :\n  CCTransfert import \"{}\" --to <dossier du projet>".format(os.path.basename(out)))
    return 0


def cmd_inspect(args):
    with BundleReader(args.bundle) as rd:
        ok = True
        try:
            rd.verify()
        except BundleError as e:
            ok = False
            ui.err(str(e))
        m = rd.manifest
        print(bundle_summary(m, verified=ok))
        src = m["source"]
        print("\nMachine source :")
        for k in ("os", "hostname", "user", "home", "config_dir", "temp_root", "style", "slug"):
            print("  {:<11}: {}".format(k, src.get(k)))
        print("\nOptions d'export : " + ", ".join("{}={}".format(k, v) for k, v in (m.get("options") or {}).items()))
        print("Créé le {} par CCTransfert {} (format {})".format(
            m.get("created_at"), (m.get("tool") or {}).get("version"), m.get("format_version")))
        mapped = recalled_mapping(src)
        if mapped:
            print("Dossier cible mémorisé ici : {}".format(mapped))
        if args.files:
            print("\nFichiers :")
            for e in m.get("files", []):
                print("  {:>10}  {:<14} {}".format(ui.human_size(e.get("size") or 0), e.get("kind"), e["path"]))
        return 0 if ok else 1


def _parse_maps(items):
    out = []
    for it in items or []:
        if "=" not in it:
            raise CliError("--map attend ANCIEN=NOUVEAU : {}".format(it))
        # Coupe au premier `=` suivi d'un chemin absolu (posix `/` ou Windows `X:`).
        m = re.match(r"^(.+?)=(/.*|[A-Za-z]:.*|\\\\.*)$", it)
        a, b = (m.group(1), m.group(2)) if m else it.split("=", 1)
        out.append((a, b))
    return out


def _choose_target(m, args):
    if args.to:
        return args.to
    remembered = recalled_mapping(m["source"])
    cwd = os.getcwd()
    if args.yes or not ui.interactive():
        choice = remembered or cwd
        print("Dossier cible : {} ({})".format(choice, "mémorisé" if remembered else "dossier courant"))
        return choice
    opts = []
    if remembered:
        opts.append(("a", remembered, "dossier mémorisé pour ce projet"))
    opts.append(("b", cwd, "dossier courant"))
    opts.append(("c", None, "saisir un chemin"))
    print("\nOù importer la session ?")
    for k, p, lbl in opts:
        print("  {}) {}{}".format(k, lbl, " : " + p if p else ""))
    default = opts[0][0]
    while True:
        r = ui.ask("Choix [{}] : ".format(default))
        if r is None:
            raise CliError("import annulé")
        r = (r.strip().lower() or default)
        for k, p, _l in opts:
            if r == k:
                if p:
                    return p
                path = ui.ask("Chemin du dossier projet : ")
                if path and path.strip():
                    return path.strip().strip('"')
        print("Choix invalide.")


def _clean_env():
    env = dict(os.environ)
    for k in _CHILD_ENV_DROP:
        env.pop(k, None)
    return env


def calibrate(profile, root):
    """Lance `claude -p "ok"` dans `root`, relève le slug créé, puis supprime la session de test."""
    exe = shutil.which("claude")
    if not exe:
        raise CliError("--calibrate : `claude` introuvable dans le PATH")
    existing_slugs = set(iter_project_dirs(profile))
    print("Calibration : lancement de `claude -p ok` dans {} (une petite requête API)…".format(root))
    sid = None
    for extra in (["--model", "haiku"], []):
        try:
            r = subprocess.run([exe, "-p", "ok", "--output-format", "json"] + extra, cwd=root,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300,
                               env=_clean_env(), encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as e:
            raise CliError("calibration impossible : {}".format(e))
        try:
            sid = json.loads(r.stdout).get("session_id")
        except ValueError:
            sid = None
        if sid:
            break
    if not sid:
        raise CliError("calibration : pas d'id de session dans la réponse de claude")
    found = find_session_files(profile, sid)
    if not found:
        raise CliError("calibration : transcript de test introuvable")
    slug = found[0]
    # Nettoyage de la session de test.
    for s in found:
        try:
            os.unlink(profile.join(profile.projects_dir, s, sid + ".jsonl"))
        except OSError:
            pass
    shutil.rmtree(profile.join(profile.config_dir, "session-env", sid), ignore_errors=True)
    if slug not in existing_slugs:
        for sub in ("memory", ""):
            try:
                os.rmdir(profile.join(profile.projects_dir, slug, sub) if sub else profile.join(profile.projects_dir, slug))
            except OSError:
                pass
    print("Slug observé : {}".format(slug))
    return slug


def print_import_report(res, dry):
    rep = res.report
    print("\nDossier cible  : {}".format(res.target_root))
    print("Dossier Claude : …/projects/{}  ({})".format(res.slug, res.slug_reason))
    if res.new_sid != res.old_sid:
        print("Nouvel ID      : {} → {}".format(res.old_sid, res.new_sid))
    else:
        print("Session        : {}".format(res.new_sid))
    if dry:
        print("\nCe qui serait écrit :")
        for op, path, detail in res.actions:
            print("  {:<10} {}  ({})".format(op, path, detail))
    else:
        labels = {"créer": "créé(s)", "remplacer": "remplacé(s)", "déplacer": "déplacé(s)",
                  "identique": "déjà identique(s)", "ajouter": "ajout(s) à l'historique"}
        counts = {}
        for op, _p, _d in res.actions:
            counts[op] = counts.get(op, 0) + 1
        print("Fichiers       : " + ", ".join("{} {}".format(n, labels.get(op, op)) for op, n in counts.items()))
    print("\nRéécriture : {} lignes, {} modifiées, {} illisibles (recopiées)".format(
        rep.lines_total, rep.lines_changed, rep.lines_bad))
    active = [r for r in res.rules if not r.noop]
    if active:
        print("Correspondances :")
        for r in sorted(active, key=lambda r: len(r.src), reverse=True):
            print("  [{}] {}  →  {}".format(r.name, r.src, r.dst))
    if rep.counts:
        print("Remplacements : " + ", ".join("{} {}".format(k, v) for k, v in rep.counts.most_common()))
    if rep.examples:
        print("Exemples :")
        for rule, exs in rep.examples.items():
            for b, a in exs[:2]:
                print("  [{}]\n    avant : {}\n    après : {}".format(rule, b, a))
    if rep.unmapped:
        print("Chemins absolus restés tels quels (pour info, top 10) :")
        for p, n in rep.unmapped.most_common(10):
            print("  {:>5} × {}".format(n, p))
    for w in rep.warnings[:10]:
        ui.warn(w)
    if len(rep.warnings) > 10:
        ui.warn("… et {} autres avertissements".format(len(rep.warnings) - 10))
    for w in res.warnings:
        ui.warn(w)


def print_final(res, style, entrypoint=None):
    root = res.target_root
    print("\n✓ Session importée.\n")
    print("Pour reprendre :")
    cd = 'cd "{}"; '.format(root) if style == P.WINDOWS else 'cd "{}" && '.format(root)
    if entrypoint in SDK_ENTRYPOINTS:
        print("  {}claude --resume {}".format(cd, res.new_sid))
        print("  (session créée par `claude -p`/SDK : Claude Code ne la montre pas dans le picker `claude -r`)")
    else:
        print("  {}claude -r".format(cd))
        print("  (ou depuis ce dossier : claude --resume {})".format(res.new_sid))
    print("  En dernier recours : claude --resume \"{}\"".format(res.transcript))
    print("\nRappels :")
    print("  • Le code n'est pas transféré : synchronisez-le à part (git).")
    for w in res.git_warnings:
        print("    ⚠ " + w)
    print("  • Au premier lancement, Claude Code demandera de faire confiance au dossier.")
    print("  • Les serveurs MCP, settings et identifiants ne sont pas transférés.")
    if res.journal_dir:
        print("  • Annulation possible : CCTransfert undo")


def _prepare_root(path, args, style):
    root = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(root):
        if os.path.exists(root):
            raise CliError("{} existe mais n'est pas un dossier".format(root))
        if args.dry_run:
            print("(le dossier {} n'existe pas : il serait créé)".format(root))
            return root
        if args.yes or ui.ask_yes("Le dossier {} n'existe pas. Le créer ?".format(root), default=True):
            os.makedirs(root)
        else:
            raise CliError("import annulé")
    return os.path.realpath(root)


def cmd_import(args):
    profile = local_profile()
    with BundleReader(args.bundle) as rd:
        m = rd.manifest
    print(bundle_summary(m))
    root = _prepare_root(_choose_target(m, args), args, profile.style)
    slug_override = None
    if args.calibrate:
        if not os.path.isdir(root):
            raise CliError("--calibrate : le dossier cible doit exister")
        slug_override = calibrate(profile, root)
    else:
        slug, _why, _r = resolve_slug(profile, root)
        if len(compute_slug(root)) > MAX_SLUG and slug == compute_slug(root):
            ui.warn("chemin long : slug calculé avec le hash de Claude Code. --calibrate permet de le vérifier.")
    confirm = None if (args.yes or not ui.interactive()) else ui.ask_yes
    opts = ImportOptions(root, maps=_parse_maps(args.map), new_id=args.new_id, replace=args.replace,
                         dry_run=args.dry_run, keep_mtime=args.keep_mtime, confirm=confirm,
                         slug_override=slug_override)
    res = import_bundle(args.bundle, profile, opts)
    print_import_report(res, args.dry_run)
    if args.dry_run:
        print("\n(dry-run : rien n'a été écrit)")
        return 0
    remember_mapping(m["source"], res.target_root)
    print_final(res, profile.style, (m.get("session") or {}).get("entrypoint"))
    return 0


def cmd_undo(args):
    d, data = last_journal()
    if not d:
        print("Aucun import à annuler.")
        return 0
    print("Dernier import : {}".format(data.get("created_at")))
    print("  session(s)   : {}".format(", ".join(data.get("session_ids", []))))
    print("  dossier      : {}".format(data.get("target_root")))
    print("  {} fichier(s) créé(s) à supprimer, {} sauvegarde(s) à restaurer{}".format(
        len(data.get("created", [])), len(data.get("backups", [])),
        ", {} ligne(s) d'historique".format(len(data["history_lines"])) if data.get("history_lines") else ""))
    running = running_sessions(local_profile())
    busy = [s for s in data.get("session_ids", []) if s in running]
    if busy:
        raise CliError("la session {} est en cours : quittez-la (/exit) avant d'annuler.".format(busy[0][:8]))
    changed = undo_check(data)
    if changed:
        ui.warn("{} fichier(s) ont été modifiés depuis l'import (session reprise ?) : ces "
                "modifications seront perdues.".format(len(changed)))
        for p in changed[:5]:
            print("    " + p)
    if not args.yes and not ui.ask_yes("Annuler cet import ?"):
        return 1
    errors = undo(d, data)
    for e in errors:
        ui.warn(e)
    print("✓ Import annulé." if not errors else "Annulation terminée avec des erreurs.")
    return 0 if not errors else 1


def cmd_relocate(args):
    profile = local_profile()
    scope = list_sessions(profile)
    info = find_by_selector(profile, args.session, scope)
    if info.running:
        raise CliError("la session {} est en cours (PID {}) : quittez-la avant de la déplacer.".format(
            info.short_id, info.running.get("pid")))
    root = _prepare_root(args.to, args, profile.style)
    if info.root and P.same_path(info.root, root, profile.style):
        raise CliError("la session est déjà rattachée à {}".format(root))
    print("Déplacement de « {} » ({})\n  de {}\n  vers {}".format(
        ui.clip(info.display_title, 60), info.short_id, info.root, root))
    tmpdir = tempfile.mkdtemp(prefix="cct-relocate-")
    try:
        tmp = os.path.join(tmpdir, "session.CCTransfert")
        export_session(profile, info, tmp, with_memory=False, with_file_history=True, with_history=False)
        opts = ImportOptions(root, maps=_parse_maps(args.map), new_id=False, replace=True,
                             dry_run=args.dry_run, keep_mtime=args.keep_mtime, relocate=True)
        res = import_bundle(tmp, profile, opts)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print_import_report(res, args.dry_run)
    if args.dry_run:
        print("\n(dry-run : rien n'a été écrit)")
        return 0
    print_final(res, profile.style, info.entrypoint)
    return 0


def cmd_doctor(args):
    profile = local_profile()
    d = doctor_mod.collect(profile)
    if args.json:
        # ASCII pur : la sortie reste lisible même redirigée dans un fichier sous Windows (cp1252).
        print(json.dumps(d, ensure_ascii=True, indent=2))
    else:
        print(doctor_mod.render(d))
    return 0


# -- analyse des arguments ------------------------------------------------------------------------

# Traduction des messages d'argparse (qui passe tous ses textes par `argparse._`).
_ARGPARSE_FR = {
    "usage: ": "usage : ",
    "positional arguments": "commandes et arguments",
    "optional arguments": "options",
    "options": "options",
    "show this help message and exit": "afficher cette aide et quitter",
    "show program's version number and exit": "afficher la version et quitter",
    "%(prog)s: error: %(message)s\n": "%(prog)s : erreur : %(message)s\n",
    "the following arguments are required: %s": "arguments obligatoires manquants : %s",
    "unrecognized arguments: %s": "arguments non reconnus : %s",
    "invalid choice: %(value)r (choose from %(choices)s)": "choix invalide : %(value)r (possibles : %(choices)s)",
    "expected one argument": "une valeur est attendue",
    "argument %(argument_name)s: %(message)s": "argument %(argument_name)s : %(message)s",
    "not allowed with argument %s": "incompatible avec l'argument %s",
}
argparse._ = lambda s: _ARGPARSE_FR.get(s, s)


def build_parser():
    p = argparse.ArgumentParser(
        prog="CCTransfert",
        description="Transférer une session Claude Code d'une machine (ou d'un dossier) à une autre.",
    )
    p.add_argument("--version", action="version", version="CCTransfert " + __version__)
    p.add_argument("--config-dir", help="dossier de config de Claude Code (défaut : $CLAUDE_CONFIG_DIR ou ~/.claude)")
    sub = p.add_subparsers(dest="cmd", metavar="COMMANDE")

    s = sub.add_parser("doctor", help="diagnostic de la machine (OS, WSL, config, slug du dossier courant…)")
    s.add_argument("--json", action="store_true", help="sortie JSON")
    s.set_defaults(func=cmd_doctor)

    def scope_opts(sp):
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--here", action="store_true", help="sessions du dossier courant (défaut)")
        g.add_argument("--dir", metavar="PATH", help="sessions du dossier PATH")
        g.add_argument("--all", action="store_true", help="toutes les sessions de la machine")

    s = sub.add_parser("list", help="lister les sessions")
    scope_opts(s)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("export", help="exporter une session dans un fichier .CCTransfert")
    s.add_argument("session", nargs="?", help="ID (ou début d'ID), nom, ou N° affiché par `list`")
    scope_opts(s)
    s.add_argument("--latest", action="store_true", help="la session la plus récente du périmètre")
    s.add_argument("-o", "--output", metavar="FICHIER|DOSSIER", help="fichier ou dossier de sortie")
    s.add_argument("--with-memory", action="store_true", help="inclure la mémoire du projet (auto memory)")
    s.add_argument("--with-history", action="store_true", help="inclure l'historique des prompts (flèche ↑)")
    s.add_argument("--no-file-history", action="store_true", help="ne pas inclure les sauvegardes du rewind")
    s.add_argument("--encrypt", action="store_true", help="chiffrer le bundle avec age ou gpg (phrase de passe)")
    s.add_argument("-y", "--yes", action="store_true", help="répondre oui aux questions")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("import", help="importer un bundle sur cette machine")
    s.add_argument("bundle", help="fichier .CCTransfert")
    s.add_argument("--to", metavar="DIR", help="dossier du projet sur cette machine")
    s.add_argument("--map", action="append", metavar="ANCIEN=NOUVEAU", help="correspondance de chemin supplémentaire (répétable)")
    s.add_argument("--new-id", action="store_true", help="donner un nouvel ID à la session (copie indépendante)")
    s.add_argument("--replace", action="store_true", help="remplacer/déplacer une session existante de même ID (avec sauvegarde)")
    s.add_argument("--dry-run", action="store_true", help="tout afficher sans rien écrire")
    s.add_argument("--keep-mtime", action="store_true", help="garder les dates d'origine des fichiers")
    s.add_argument("--calibrate", action="store_true", help="vérifier le slug en lançant `claude -p ok` dans le dossier cible")
    s.add_argument("-y", "--yes", action="store_true", help="ne poser aucune question (un remplacement exige --replace)")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("inspect", help="afficher le contenu d'un bundle")
    s.add_argument("bundle")
    s.add_argument("--files", action="store_true", help="lister tous les fichiers")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("undo", help="annuler le dernier import")
    s.add_argument("-y", "--yes", action="store_true", help="ne pas demander de confirmation")
    s.set_defaults(func=cmd_undo)

    s = sub.add_parser("relocate", help="déplacer une session vers un autre dossier de cette machine")
    s.add_argument("session", help="ID (ou début d'ID), nom, ou N° de `list --all`")
    s.add_argument("--to", required=True, metavar="DIR", help="nouveau dossier du projet")
    s.add_argument("--map", action="append", metavar="ANCIEN=NOUVEAU")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--keep-mtime", action="store_true")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(func=cmd_relocate)
    return p


def _setup_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    _setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    if args.config_dir:
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.abspath(os.path.expanduser(args.config_dir))
    try:
        return args.func(args) or 0
    except (BundleError, ImportError_, CliError) as e:
        ui.err(str(e))
        return 1
    except OSError as e:
        ui.err(str(e))
        return 1
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 130
