"""Icônes « CCTransfert » : bureau et menu (menu Démarrer sous Windows, menu des applications sous Linux).

Chaque raccourci s'active ou se retire séparément. Avec la version autonome (exe Windows,
binaire Linux), l'exécutable est d'abord copié dans un dossier stable pour que les
raccourcis restent valides même si le fichier téléchargé est supprimé.
"""

import os
import re
import shutil
import subprocess
import sys

from ..profile import no_window

LINK_NAME = "CCTransfert"


def is_frozen():
    """Vrai pour la version autonome (exe Windows / binaire Linux construits avec PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def _static():
    from .server import STATIC

    return STATIC


def _pkg_parent():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# -- emplacement d'installation de la version autonome ---------------------------------------


def install_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "Programs", "CCTransfert")
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data, "cctransfert")


def ensure_installed():
    """Version autonome : copie l'exécutable dans le dossier d'installation et renvoie son chemin.
    (Ailleurs : renvoie None, les raccourcis lancent Python.)"""
    if not is_frozen():
        return None
    exe = os.path.abspath(sys.executable)
    # Nom stable (sans numéro de version) : les mises à jour remplacent le fichier sur place.
    if os.name == "nt":
        name = "cctransfert-cli.exe" if "cli" in os.path.basename(exe).lower() else "CCTransfert.exe"
    else:
        name = "CCTransfert"
    dest = os.path.join(install_dir(), name)
    if os.path.normcase(exe) == os.path.normcase(dest):
        return exe
    os.makedirs(install_dir(), exist_ok=True)
    tmp = dest + ".copie"
    shutil.copyfile(exe, tmp)
    if os.name != "nt":
        os.chmod(tmp, 0o755)
    os.replace(tmp, dest)
    # L'exe en ligne de commande, s'il est à côté, suit le même chemin.
    if os.name == "nt" and name == "CCTransfert.exe":
        for other in os.listdir(os.path.dirname(exe)):
            if other.lower().startswith("cctransfert-cli") and other.lower().endswith(".exe"):
                try:
                    shutil.copyfile(os.path.join(os.path.dirname(exe), other),
                                    os.path.join(install_dir(), "cctransfert-cli.exe"))
                except OSError:
                    pass
                break
    return dest


# -- Windows ------------------------------------------------------------------------------------


def _win_folder(csidl):
    import ctypes

    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) != 0:
        raise OSError("dossier Windows introuvable ({})".format(csidl))
    return buf.value


def _win_paths():
    return {
        "desktop": os.path.join(_win_folder(0x0010), LINK_NAME + ".lnk"),  # CSIDL_DESKTOPDIRECTORY (OneDrive compris)
        "menu": os.path.join(_win_folder(0x0002), LINK_NAME + ".lnk"),  # CSIDL_PROGRAMS : menu Démarrer
    }


def _powershell(script):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, **no_window())
    if r.returncode != 0:
        raise OSError("PowerShell : " + (r.stderr or r.stdout).strip())
    return r.stdout.strip()


def _win_create(lnk):
    installed = ensure_installed()
    if installed:
        target, args, wd, icon = installed, "", os.path.dirname(installed), installed
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        target = pyw if os.path.isfile(pyw) else sys.executable  # pythonw : pas de console
        args, wd, icon = "-m cctransfert gui", _pkg_parent(), os.path.join(_static(), "icon.ico")
    q = lambda v: v.replace("'", "''")  # noqa: E731 - échappement PowerShell
    _powershell(
        "$s=(New-Object -COM WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{t}';$s.Arguments='{a}';"
        "$s.WorkingDirectory='{wd}';$s.IconLocation='{ico},0';$s.Description='Transférer une session Claude Code';"
        "$s.Save()".format(lnk=q(lnk), t=q(target), a=q(args), wd=q(wd), ico=q(icon)))


# -- Linux --------------------------------------------------------------------------------------


def _xdg_desktop_dir():
    home = os.path.expanduser("~")
    try:
        with open(os.path.join(home, ".config", "user-dirs.dirs"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r'\s*XDG_DESKTOP_DIR="(.*)"', line)
                if m:
                    return m.group(1).replace("$HOME", home)
    except OSError:
        pass
    for n in ("Desktop", "Bureau"):
        if os.path.isdir(os.path.join(home, n)):
            return os.path.join(home, n)
    return None


def _linux_paths():
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    desk = _xdg_desktop_dir()
    return {
        "menu": os.path.join(data, "applications", "cctransfert.desktop"),
        "desktop": os.path.join(desk, "cctransfert.desktop") if desk else None,
        "svg": os.path.join(data, "icons", "hicolor", "scalable", "apps", "cctransfert.svg"),
        "png": os.path.join(data, "icons", "hicolor", "256x256", "apps", "cctransfert.png"),
        "apps_dir": os.path.join(data, "applications"),
    }


def _wm_class():
    """Identifiant que Chrome donnera à la fenêtre, pour que le dock affiche l'icône CCTransfert."""
    from .server import APP_SLUG, _app_browsers

    exes = _app_browsers()
    name = os.path.basename(exes[0]) if exes else ""
    prefix = ("chrome" if name.startswith("google-chrome") else "chromium" if name.startswith("chromium")
              else "brave" if name.startswith("brave") else "msedge" if "edge" in name else None)
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and prefix:
        return "{}-{}-Default".format(prefix, APP_SLUG)
    return APP_SLUG


def _linux_entry(icon):
    installed = ensure_installed()
    if installed:
        exec_line = '"{}" gui %f'.format(installed)
    else:
        exec_line = 'env PYTHONPATH="{}" "{}" -m cctransfert gui %f'.format(_pkg_parent(), sys.executable)
    return ("[Desktop Entry]\nType=Application\nName=CCTransfert\n"
            "GenericName=Transfert de sessions Claude Code\n"
            "Comment=Transférer une session Claude Code vers une autre machine\n"
            "Exec={}\nIcon={}\nTerminal=false\nCategories=Development;\nStartupNotify=true\n"
            "StartupWMClass={}\nKeywords=claude;session;transfert;export;import;\n").format(exec_line, icon, _wm_class())


def _linux_create(path, trusted):
    p = _linux_paths()
    static = _static()
    for key, src in (("svg", "icon.svg"), ("png", "icon-256.png")):
        os.makedirs(os.path.dirname(p[key]), exist_ok=True)
        shutil.copyfile(os.path.join(static, src), p[key])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_linux_entry(p["svg"]))
    os.chmod(path, 0o755)
    if trusted and shutil.which("gio"):
        # Le bureau GNOME (DING) ne lance une icône que si elle est marquée « de confiance ».
        subprocess.run(["gio", "set", path, "metadata::trusted", "true"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", p["apps_dir"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# -- interface commune --------------------------------------------------------------------------


def _paths():
    if os.name == "nt":
        return _win_paths()
    if sys.platform == "darwin":
        return {}
    return _linux_paths()


def status():
    """État des raccourcis : {supported, desktop, menu, menu_label, install_dir, frozen}."""
    if sys.platform == "darwin":
        return {"supported": False, "desktop": False, "menu": False}
    p = _paths()
    return {
        "supported": True,
        "desktop": bool(p.get("desktop") and os.path.isfile(p["desktop"])),
        "menu": os.path.isfile(p["menu"]),
        "desktop_available": bool(p.get("desktop")),
        "menu_label": "menu Démarrer" if os.name == "nt" else "menu des applications",
        "frozen": is_frozen(),
        "install_dir": install_dir() if is_frozen() else None,
    }


def set_shortcuts(desktop=None, menu=None):
    """Active (True) ou retire (False) chaque raccourci ; None = inchangé. Renvoie (créés, supprimés)."""
    if sys.platform == "darwin":
        raise OSError("raccourcis non pris en charge sous macOS : lancez `CCTransfert gui`")
    p = _paths()
    created, removed = [], []
    for key, want in (("menu", menu), ("desktop", desktop)):
        path = p.get(key)
        if want is None or not path:
            continue
        if want:
            if os.name == "nt":
                _win_create(path)
            else:
                _linux_create(path, trusted=(key == "desktop"))
            created.append(path)
        elif os.path.isfile(path):
            os.unlink(path)
            removed.append(path)
    if os.name != "nt" and not os.path.isfile(p["menu"]) and not (p.get("desktop") and os.path.isfile(p["desktop"])):
        for key in ("svg", "png"):  # plus aucun raccourci : on retire aussi les icônes copiées
            if os.path.isfile(p[key]):
                os.unlink(p[key])
    return created, removed


def install_shortcut(on_desktop=True):
    """Menu + (par défaut) bureau. Renvoie la liste des fichiers créés."""
    return set_shortcuts(desktop=bool(on_desktop), menu=True)[0]


def remove_shortcut():
    """Retire tous les raccourcis. Renvoie la liste des fichiers supprimés."""
    return set_shortcuts(desktop=False, menu=False)[1]
