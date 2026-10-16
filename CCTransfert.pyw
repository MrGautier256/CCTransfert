#!/usr/bin/env python3
"""Lanceur de l'interface graphique CCTransfert, pour Windows et Linux.

Windows : double-cliquez sur ce fichier (aucune fenêtre de console ne s'ouvre).
          Glisser un fichier .CCTransfert sur ce lanceur l'ouvre dans l'onglet Importer.
Linux   : clic droit → « Exécuter comme un programme » (GNOME n'exécute rien par
          double-clic dans un dossier), ou `./CCTransfert.pyw` dans un terminal.
          Pour une icône en un clic : `CCTransfert gui --install-shortcut`.

Le lanceur démarre l'interface en arrière-plan puis se termine ; l'interface s'arrête
toute seule quand sa fenêtre est fermée. Journal : ~/.CCTransfert/gui.log
"""

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
TITLE = "CCTransfert"


def log_path():
    home = os.environ.get("CCTRANSFERT_HOME") or os.path.join(os.path.expanduser("~"), ".CCTransfert")
    os.makedirs(home, exist_ok=True)
    return os.path.join(home, "gui.log")


def show_error(msg):
    """Message d'erreur visible même sans terminal."""
    if os.environ.get("CCT_NO_DIALOG"):
        print(msg, file=sys.stderr)
        return
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
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(TITLE, msg)
    except Exception:  # noqa: BLE001 - dernier recours : la sortie d'erreur
        print(msg, file=sys.stderr)


def main():
    if sys.version_info < (3, 9):
        show_error("CCTransfert a besoin de Python 3.9 ou plus récent (version actuelle : {}).".format(
            sys.version.split()[0]))
        return 1
    if not os.path.isfile(os.path.join(ROOT, "cctransfert", "gui", "server.py")):
        show_error("Ce lanceur doit rester à la racine du dossier CCTransfert.")
        return 1

    exe = sys.executable
    if os.name == "nt":
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(pyw):
            exe = pyw  # pas de console pour le processus de l'interface
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONUTF8", "1")
    cmd = [exe, "-m", "cctransfert", "gui"] + sys.argv[1:]

    log = log_path()
    with open(log, "a", encoding="utf-8") as lf:
        lf.write("\n=== {} : {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(cmd)))
        lf.flush()
        kw = {"stdin": subprocess.DEVNULL, "stdout": lf, "stderr": lf, "env": env, "cwd": os.path.expanduser("~")}
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
        else:
            kw["start_new_session"] = True  # survit à la fermeture du terminal ouvert par le gestionnaire de fichiers
        try:
            child = subprocess.Popen(cmd, **kw)
        except OSError as e:
            show_error("Impossible de lancer CCTransfert : {}".format(e))
            return 1

    # Si l'interface meurt tout de suite, on le dit au lieu de ne rien afficher.
    for _ in range(30):
        code = child.poll()
        if code is not None:
            if code == 0:
                return 0  # ex. une instance était déjà ouverte : une fenêtre y a été ajoutée
            reason = ""
            try:
                with open(log, encoding="utf-8", errors="replace") as f:
                    lines = [ln.strip() for ln in f if ln.strip()]
                reason = lines[-1] if lines else ""  # « Erreur : … » ou « TypeErreur: … » final
            except OSError:
                pass
            show_error("CCTransfert n'a pas pu démarrer.\n\n{}\n\nJournal complet : {}".format(reason, log))
            return 1
        time.sleep(0.1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
