"""Construit la version autonome de CCTransfert avec PyInstaller.

    python -m pip install pyinstaller
    python packaging/build.py

Windows : dist/CCTransfert-<version>-windows-x64.exe (interface, sans console)
          dist/cctransfert-cli-<version>-windows-x64.exe (ligne de commande)
Linux   : dist/CCTransfert-<version>-linux-<arch> (interface sans argument, CLI avec arguments)

Python n'a pas besoin d'être installé sur la machine qui utilise ces fichiers.
Un exe Windows ne peut être construit que sous Windows (c'est le rôle de la CI GitHub).
"""

import os
import platform
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cctransfert import __version__  # noqa: E402

STATIC = os.path.join(ROOT, "cctransfert", "gui", "static")
ENTRY = os.path.join(ROOT, "packaging", "entry.py")
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build", "pyinstaller")


def build(name, windowed):
    import PyInstaller.__main__

    args = [
        ENTRY, "--name", name, "--onefile", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", os.path.join(WORK, name), "--specpath", WORK,
        "--paths", ROOT,
        "--add-data", "{}{}{}".format(STATIC, os.pathsep, "cctransfert/gui/static"),
        "--hidden-import", "cctransfert.gui.server", "--hidden-import", "cctransfert.gui.shortcuts",
        # Modules inutiles qui alourdiraient l'exécutable.
        "--exclude-module", "tkinter", "--exclude-module", "unittest", "--exclude-module", "pydoc",
        "--windowed" if windowed else "--console",
    ]
    if os.name == "nt":
        args += ["--icon", os.path.join(STATIC, "icon.ico")]
    PyInstaller.__main__.run(args)
    out = os.path.join(DIST, name + (".exe" if os.name == "nt" else ""))
    print("construit :", out, "({:.1f} Mo)".format(os.path.getsize(out) / 1048576))
    return out


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    v = __version__
    if os.name == "nt":
        build("CCTransfert-{}-windows-x64".format(v), windowed=True)
        build("cctransfert-cli-{}-windows-x64".format(v), windowed=False)
    else:
        arch = "aarch64" if platform.machine().lower() in ("aarch64", "arm64") else "x86_64"
        system = "macos" if sys.platform == "darwin" else "linux"
        build("CCTransfert-{}-{}-{}".format(v, system, arch), windowed=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
