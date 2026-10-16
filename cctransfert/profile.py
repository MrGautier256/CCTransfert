"""Profil d'une machine : OS, style de chemins, home, dossier de config, dossier temp.

Toute la logique (slug, réécriture, placement) reçoit un `Profile` au lieu de lire
l'environnement directement. On peut ainsi simuler une machine Windows depuis Linux
dans les tests : les chemins *logiques* (ceux écrits dans les transcripts) sont au
style de la machine simulée, et `fs()` les traduit en chemins *physiques* sur le
disque de test.
"""

import getpass
import os
import platform
import re
import shutil
import socket
import subprocess
import sys

from . import paths as P


class Profile:
    def __init__(
        self,
        os_name,
        home,
        config_dir,
        temp_root,
        hostname="",
        user="",
        wsl=False,
        claude_version=None,
        project_dir_name=None,
        cwd=None,
        fs_map=None,
        settings_cleanup_days=None,
    ):
        self.os = os_name  # linux | windows | darwin
        self.style = P.WINDOWS if os_name == "windows" else P.POSIX
        self.home = home
        self.config_dir = config_dir
        self.temp_root = temp_root  # ex. /tmp/claude-1000 ou C:\Users\x\AppData\Local\Temp\claude-0
        self.hostname = hostname
        self.user = user
        self.wsl = wsl
        self.claude_version = claude_version
        # Valeur effective de CLAUDE_CODE_PROJECT_DIR_NAME (déjà validée), ou None.
        self.project_dir_name = project_dir_name
        self.cwd = cwd
        # Traduction chemin logique -> chemin physique. None = identité (machine locale).
        self._fs_map = fs_map
        self.settings_cleanup_days = settings_cleanup_days

    # -- chemins -----------------------------------------------------------------

    @property
    def is_local(self):
        return self._fs_map is None

    def fs(self, path):
        """Chemin physique (sur le disque) d'un chemin logique de cette machine."""
        return path if self._fs_map is None else self._fs_map(path)

    def join(self, *parts):
        return P.join(self.style, *parts)

    @property
    def projects_dir(self):
        return self.join(self.config_dir, "projects")

    def realpath(self, path):
        """realpath si la machine est la machine locale, sinon simple normalisation."""
        if self.is_local:
            return os.path.realpath(path)
        return P.normalize(path, self.style)

    def to_dict(self):
        return {
            "os": self.os,
            "style": self.style,
            "hostname": self.hostname,
            "user": self.user,
            "home": self.home,
            "config_dir": self.config_dir,
            "temp_root": self.temp_root,
            "wsl": self.wsl,
            "claude_version": self.claude_version,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            os_name=d.get("os") or ("windows" if d.get("style") == P.WINDOWS else "linux"),
            home=d["home"],
            config_dir=d["config_dir"],
            temp_root=d.get("temp_root") or "",
            hostname=d.get("hostname", ""),
            user=d.get("user", ""),
            wsl=bool(d.get("wsl")),
            claude_version=d.get("claude_version"),
        )


# -- détection de la machine locale ------------------------------------------------


def host_os():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def detect_wsl():
    """Vrai si on tourne dans WSL (Linux sous Windows)."""
    if host_os() != "linux":
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    for f in ("/proc/sys/kernel/osrelease", "/proc/version"):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                if "microsoft" in fh.read().lower():
                    return True
        except OSError:
            pass
    return False


def node_tmpdir():
    """Équivalent de `os.tmpdir()` de Node.js (utilisé par Claude Code)."""
    if host_os() == "windows":
        p = os.environ.get("TEMP") or os.environ.get("TMP") or (
            os.path.join(os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows", "temp")
        )
        if len(p) > 1 and p.endswith("\\") and not p.endswith(":\\"):
            p = p[:-1]
        return p
    p = os.environ.get("TMPDIR") or os.environ.get("TMP") or os.environ.get("TEMP") or "/tmp"
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def claude_temp_root():
    """Racine temp de Claude Code : `$CLAUDE_CODE_TMPDIR|os.tmpdir()` + `claude-<uid>`.

    Sous Windows `process.getuid` n'existe pas : le code donne `claude-0`. Si seul un
    dossier `claude` existe (convention d'une autre version), on le préfère.
    """
    base = os.environ.get("CLAUDE_CODE_TMPDIR") or node_tmpdir()
    uid = os.getuid() if hasattr(os, "getuid") else 0
    preferred = os.path.join(base, "claude-{}".format(uid))
    if not os.path.isdir(preferred):
        alt = os.path.join(base, "claude")
        if os.path.isdir(alt):
            return alt
    return preferred


def local_config_dir():
    """Dossier de config de Claude Code : `$CLAUDE_CONFIG_DIR` ou `~/.claude` (en NFC)."""
    import unicodedata

    d = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return unicodedata.normalize("NFC", d)


def local_project_dir_name():
    """`CLAUDE_CODE_PROJECT_DIR_NAME`, pris en compte seulement avec `CLAUDE_CONFIG_DIR`."""
    from .slug import valid_project_dir_name

    if not os.environ.get("CLAUDE_CONFIG_DIR"):
        return None
    v = (os.environ.get("CLAUDE_CODE_PROJECT_DIR_NAME") or "").strip()
    return v if valid_project_dir_name(v) else None


def claude_version(timeout=20):
    """Version de Claude Code (`claude --version`), ou None."""
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\d+\.\d+\.\d+", out or "")
    return m.group(0) if m else (out or "").strip() or None


def read_cleanup_days(config_dir):
    """`cleanupPeriodDays` des settings utilisateur, ou None (défaut de Claude Code : 30)."""
    try:
        import json

        with open(os.path.join(config_dir, "settings.json"), encoding="utf-8") as f:
            v = json.load(f).get("cleanupPeriodDays")
        return int(v) if v is not None else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def local_profile(with_claude_version=False):
    """Profil de la machine sur laquelle on tourne."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - getuser peut lever des erreurs variées
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    config = local_config_dir()
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = None
    return Profile(
        os_name=host_os(),
        home=os.path.expanduser("~"),
        config_dir=config,
        temp_root=claude_temp_root(),
        hostname=socket.gethostname() or platform.node(),
        user=user,
        wsl=detect_wsl(),
        claude_version=claude_version() if with_claude_version else None,
        project_dir_name=local_project_dir_name(),
        cwd=cwd,
        settings_cleanup_days=read_cleanup_days(config),
    )
