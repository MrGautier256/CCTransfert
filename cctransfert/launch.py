"""Point d'entrée de la version autonome (exe Windows, binaire Linux construits avec PyInstaller).

* Sans argument (double-clic) ou avec un seul fichier `.CCTransfert` (glissé sur l'exe) :
  ouvre l'interface graphique.
* Avec des arguments : ligne de commande normale (`CCTransfert list`, `export`…).

L'exe Windows n'a pas de console : la sortie va dans ~/.CCTransfert/gui.log et une erreur
au démarrage est affichée dans une boîte de dialogue.
"""

import os
import shutil
import subprocess
import sys
import traceback

TITLE = "CCTransfert"
BUNDLE_EXT = (".cctransfert", ".cctransfert.age", ".cctransfert.gpg")


def _log_path():
    from .importer import tool_home

    home = tool_home()
    os.makedirs(home, exist_ok=True)
    return os.path.join(home, "gui.log")


def show_error(msg):
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, TITLE, 0x10)  # MB_ICONERROR
        return
    for cmd in (["zenity", "--error", "--no-wrap", "--title=" + TITLE, "--text=" + msg],
                ["kdialog", "--title", TITLE, "--error", msg],
                ["notify-send", "-u", "critical", TITLE, msg]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


def main(argv=None):
    from . import update

    update.cleanup_old_binary()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (len(argv) == 1 and argv[0].lower().endswith(BUNDLE_EXT) and os.path.isfile(argv[0])):
        argv = ["gui"] + argv  # double-clic, ou fichier glissé sur l'exe
    windowed = sys.stdout is None  # exe sans console
    log = None
    if windowed:
        log = open(_log_path(), "a", encoding="utf-8")
        sys.stdout = sys.stderr = log
    from .cli import main as cli_main

    try:
        code = cli_main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception:  # noqa: BLE001 - tout doit finir dans le journal
        traceback.print_exc()
        code = 1
    if code and windowed:
        log.flush()
        reason = ""
        try:
            with open(_log_path(), encoding="utf-8", errors="replace") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            reason = lines[-1] if lines else ""
        except OSError:
            pass
        show_error("CCTransfert n'a pas pu démarrer.\n\n{}\n\nJournal : {}".format(reason, _log_path()))
    return code or 0
