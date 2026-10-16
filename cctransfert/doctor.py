"""`CCTransfert doctor` : diagnostic de la machine, notamment pour valider le format sous Windows."""

import glob
import os
import platform
import shutil
import sys

from . import __version__
from . import paths as P
from .profile import claude_version, node_tmpdir
from .slug import compute_slug, drive_case_of_slug, slug_matches
from .store import head_cwd, iter_project_dirs, running_sessions, session_ids_in


def collect(profile):
    """Rassemble les informations de diagnostic dans un dict (sérialisable en JSON)."""
    d = {"outil": __version__, "python": sys.version.split()[0], "python_exe": sys.executable}
    d["os"] = profile.os
    d["plateforme"] = platform.platform()
    d["wsl"] = profile.wsl
    d["hote"] = profile.hostname
    d["utilisateur"] = profile.user
    d["home"] = profile.home
    d["env"] = {k: os.environ.get(k) for k in (
        "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_PROJECT_DIR_NAME", "CLAUDE_CODE_TMPDIR", "TMPDIR", "TEMP", "TMP")
        if os.environ.get(k)}
    d["config_dir"] = profile.config_dir
    d["config_existe"] = os.path.isdir(profile.config_dir)

    exe = shutil.which("claude")
    d["claude_exe"] = exe
    d["claude_version"] = claude_version() if exe else None
    hints = []
    if profile.wsl and exe and exe.startswith("/mnt/"):
        hints.append("Le `claude` trouvé est celui de Windows ({}). Lancez CCTransfert dans le même "
                     "environnement que Claude Code : côté Windows pour ce binaire.".format(exe))
    if profile.wsl and not d["config_existe"]:
        win = glob.glob("/mnt/c/Users/*/.claude")
        if win:
            hints.append("Pas de config Claude Code dans WSL, mais il y en a côté Windows ({}). "
                         "Utilisez CCTransfert sous Windows.".format(", ".join(win)))
    if profile.os == "windows" and shutil.which("wsl"):
        hints.append("WSL est installé : si vous utilisez Claude Code DANS WSL, lancez CCTransfert dans WSL.")
    if not exe:
        hints.append("`claude` est introuvable dans le PATH.")

    # Dossier temp de Claude Code.
    base = os.environ.get("CLAUDE_CODE_TMPDIR") or node_tmpdir()
    d["temp_base"] = base
    d["temp_root"] = profile.temp_root
    try:
        d["temp_dossiers_claude"] = sorted(n for n in os.listdir(base) if n.startswith("claude"))
    except OSError:
        d["temp_dossiers_claude"] = []

    # Dossier courant.
    cwd = profile.cwd
    real = os.path.realpath(cwd) if cwd else None
    d["cwd"] = cwd
    d["cwd_realpath"] = real
    d["slug_calcule"] = compute_slug(real) if real else None
    projects = iter_project_dirs(profile)
    d["slug_existe"] = bool(real) and any(slug_matches(n, real, profile.style == P.WINDOWS) for n in projects)

    # Vérification de l'algorithme sur les dossiers existants.
    ok, ko, nb_sessions = 0, [], 0
    case = {"upper": 0, "lower": 0}
    for n in projects:
        ids = session_ids_in(profile, n)
        nb_sessions += len(ids)
        c = drive_case_of_slug(n)
        if c:
            case[c] += 1
        if profile.project_dir_name and n == profile.project_dir_name:
            continue
        cwd0 = next((x for x in (head_cwd(profile, n, s) for s in ids[:3]) if x), None)
        if not cwd0:
            continue
        if slug_matches(n, cwd0, case_insensitive=False):
            ok += 1
        else:
            ko.append({"dossier": n, "cwd": cwd0, "calcule": compute_slug(cwd0)})
    d["projets"] = len(projects)
    d["sessions"] = nb_sessions
    d["slugs_conformes"] = ok
    d["slugs_differents"] = ko
    d["lettre_lecteur"] = case
    d["cleanup_period_days"] = profile.settings_cleanup_days
    d["sessions_en_cours"] = len(running_sessions(profile))
    d["conseils"] = hints
    return d


def render(d):
    L = []
    L.append("CCTransfert {} — Python {} ({})".format(d["outil"], d["python"], d["python_exe"]))
    L.append("Système        : {} ({}){}".format(d["os"], d["plateforme"], " — WSL" if d["wsl"] else ""))
    L.append("Hôte / user    : {} / {}".format(d["hote"], d["utilisateur"]))
    L.append("Home           : {}".format(d["home"]))
    if d["env"]:
        L.append("Variables      : " + ", ".join("{}={}".format(k, v) for k, v in d["env"].items()))
    L.append("Config Claude  : {}{}".format(d["config_dir"], "" if d["config_existe"] else "  (INTROUVABLE)"))
    L.append("Claude Code    : {} ({})".format(d["claude_version"] or "?", d["claude_exe"] or "absent du PATH"))
    L.append("Temp de Claude : {}".format(d["temp_root"]))
    L.append("  dossiers « claude* » dans {} : {}".format(d["temp_base"], ", ".join(d["temp_dossiers_claude"]) or "aucun"))
    L.append("Dossier courant: {}".format(d["cwd_realpath"]))
    L.append("  slug calculé : {}  ({})".format(d["slug_calcule"], "dossier existant" if d["slug_existe"] else "pas encore de dossier"))
    L.append("Projets        : {} dossiers, {} sessions, {} en cours".format(d["projets"], d["sessions"], d["sessions_en_cours"]))
    L.append("Slugs vérifiés : {} conformes au calcul, {} différents".format(d["slugs_conformes"], len(d["slugs_differents"])))
    for x in d["slugs_differents"][:10]:
        L.append("  ✗ {}\n    cwd    : {}\n    calcul : {}".format(x["dossier"], x["cwd"], x["calcule"]))
    c = d["lettre_lecteur"]
    if c["upper"] or c["lower"]:
        L.append("Lettre lecteur : {} en majuscule (C--…), {} en minuscule (c--…)".format(c["upper"], c["lower"]))
    L.append("cleanupPeriodDays : {}".format(d["cleanup_period_days"] if d["cleanup_period_days"] is not None else "défaut (30)"))
    for h in d["conseils"]:
        L.append("⚠ " + h)
    return "\n".join(L)
