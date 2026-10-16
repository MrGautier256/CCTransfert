"""Fichier bundle `.CCTransfert` : une archive zip avec un `manifest.json`.

Les fichiers sont stockés **tels quels** : aucune réécriture à l'export, tout se fait
à l'import. Un même bundle peut ainsi aller vers n'importe quelle machine.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from . import FORMAT_VERSION

MANIFEST = "manifest.json"
ALLOWED_TOP = ("session", "file-history", "plans", "tasks", "todos", "memory")
ALLOWED_FILES = ("history.jsonl",)
_CHUNK = 1024 * 1024


class BundleError(Exception):
    pass


def safe_member(name):
    """Le chemin interne est-il sûr (protection zip-slip) ?"""
    if not name or "\x00" in name or "\\" in name:
        return False
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return False
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    return parts[0] in ALLOWED_TOP or name in ALLOWED_FILES


class BundleWriter:
    def __init__(self, out_path):
        self.out_path = out_path
        d = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(d, exist_ok=True)
        fd, self.tmp = tempfile.mkstemp(prefix=".cct-", suffix=".zip", dir=d)
        os.close(fd)
        self.zf = zipfile.ZipFile(self.tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)
        self.files = []
        self._names = set()

    def add_file(self, member, fs_path, kind):
        if not safe_member(member):
            raise BundleError("chemin interne refusé : {}".format(member))
        if member in self._names:
            return
        st = os.stat(fs_path)
        h = hashlib.sha256()
        zi = zipfile.ZipInfo.from_file(fs_path, member, strict_timestamps=False)
        zi.compress_type = zipfile.ZIP_DEFLATED
        with open(fs_path, "rb") as src, self.zf.open(zi, "w", force_zip64=True) as dst:
            while True:
                b = src.read(_CHUNK)
                if not b:
                    break
                h.update(b)
                dst.write(b)
        self._names.add(member)
        self.files.append({"path": member, "kind": kind, "sha256": h.hexdigest(), "size": st.st_size, "mtime": st.st_mtime})

    def add_bytes(self, member, data, kind, mtime=None):
        if not safe_member(member):
            raise BundleError("chemin interne refusé : {}".format(member))
        self.zf.writestr(member, data)
        self._names.add(member)
        self.files.append({"path": member, "kind": kind, "sha256": hashlib.sha256(data).hexdigest(),
                           "size": len(data), "mtime": mtime})

    def close(self, manifest):
        manifest = dict(manifest)
        manifest["format_version"] = FORMAT_VERSION
        manifest["files"] = self.files
        self.zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        self.zf.close()
        os.replace(self.tmp, self.out_path)
        return manifest

    def abort(self):
        try:
            self.zf.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.unlink(self.tmp)
        except OSError:
            pass


class BundleReader:
    """Lecture d'un bundle, avec vérification des chemins et des sha256."""

    def __init__(self, path):
        self.path = path
        self._decrypted = None
        real = self._maybe_decrypt(path)
        try:
            self.zf = zipfile.ZipFile(real, "r")
        except zipfile.BadZipFile:
            raise BundleError("{} n'est pas un bundle CCTransfert valide (zip illisible)".format(path))
        try:
            self.manifest = json.loads(self.zf.read(MANIFEST).decode("utf-8"))
        except KeyError:
            raise BundleError("manifest.json absent : ce n'est pas un bundle CCTransfert")
        fv = self.manifest.get("format_version")
        if not isinstance(fv, int) or fv > FORMAT_VERSION:
            raise BundleError("format de bundle {} non pris en charge (cet outil lit jusqu'à {}). "
                              "Mettez CCTransfert à jour.".format(fv, FORMAT_VERSION))
        self.entries = {}
        for e in self.manifest.get("files", []):
            p = e.get("path")
            if not safe_member(p):
                raise BundleError("chemin dangereux dans le bundle, import refusé : {!r}".format(p))
            self.entries[p] = e
        names = set(self.zf.namelist())
        for n in names:
            if n != MANIFEST and n not in self.entries:
                raise BundleError("fichier inattendu dans le bundle (absent du manifest), import refusé : {!r}".format(n))
        for p in self.entries:
            if p not in names:
                raise BundleError("fichier listé dans le manifest mais absent du zip : {}".format(p))

    def _maybe_decrypt(self, path):
        with open(path, "rb") as f:
            head = f.read(64)
        if head.startswith(b"PK"):
            return path
        tool = None
        if head.startswith(b"age-encryption.org/") or head.startswith(b"-----BEGIN AGE"):
            tool = ["age", "-d", "-o"]
        elif head and (head[0] & 0x80 or head.startswith(b"-----BEGIN PGP")):
            tool = ["gpg", "--quiet", "--decrypt", "--output"]
        if tool is None or not shutil.which(tool[0]):
            raise BundleError("{} n'est ni un zip ni un fichier chiffré lisible (age/gpg)".format(path))
        fd, tmp = tempfile.mkstemp(prefix="cct-dec-", suffix=".zip")
        os.close(fd)
        os.unlink(tmp)
        r = subprocess.run(tool + [tmp, path])
        if r.returncode != 0 or not os.path.exists(tmp):
            raise BundleError("déchiffrement impossible ({})".format(tool[0]))
        self._decrypted = tmp
        return tmp

    def verify(self):
        """Vérifie la taille et le sha256 de chaque fichier. Lève BundleError en cas d'écart."""
        for p, e in self.entries.items():
            h = hashlib.sha256()
            n = 0
            with self.zf.open(p) as f:
                while True:
                    b = f.read(_CHUNK)
                    if not b:
                        break
                    h.update(b)
                    n += len(b)
            if h.hexdigest() != e.get("sha256") or (e.get("size") is not None and n != e["size"]):
                raise BundleError("fichier corrompu dans le bundle (sha256 différent) : {}".format(p))

    def open(self, member):
        return self.zf.open(member)

    def read(self, member):
        return self.zf.read(member)

    def by_kind(self, kind):
        return [e for e in self.entries.values() if e.get("kind") == kind]

    def close(self):
        self.zf.close()
        if self._decrypted:
            try:
                os.unlink(self._decrypted)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def encrypt_file(path):
    """Chiffre `path` avec age (phrase de passe) ou gpg (symétrique). Renvoie le nouveau chemin."""
    if shutil.which("age"):
        out = path + ".age"
        r = subprocess.run(["age", "-p", "-o", out, path])
    elif shutil.which("gpg"):
        out = path + ".gpg"
        r = subprocess.run(["gpg", "--symmetric", "--cipher-algo", "AES256", "--output", out, path])
    else:
        raise BundleError("--encrypt : ni `age` ni `gpg` n'est installé")
    if r.returncode != 0:
        raise BundleError("le chiffrement a échoué")
    os.unlink(path)
    return out
