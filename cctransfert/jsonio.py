"""Lecture et écriture JSON fidèles au format de Claude Code.

Objectif : une ligne qu'on ne modifie pas ressort **à l'octet près** ; une ligne
modifiée ressort comme l'aurait écrite `JSON.stringify` (compact, UTF-8 brut,
mêmes échappements), avec les nombres recopiés tels qu'ils étaient dans le fichier.
"""

import json
import os
import re
import tempfile


class RawNumber:
    """Nombre JSON conservé sous sa forme textuelle d'origine (`1.0`, `1e21`, gros entiers…)."""

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __eq__(self, other):
        return isinstance(other, RawNumber) and other.text == self.text

    def __hash__(self):
        return hash(self.text)

    def __repr__(self):
        return "RawNumber({!r})".format(self.text)


def loads_raw(text):
    """`json.loads` qui garde les nombres sous forme de `RawNumber`."""
    return json.loads(text, parse_int=RawNumber, parse_float=RawNumber, parse_constant=RawNumber)


# Échappements de JSON.stringify : guillemet, antislash, contrôles < 0x20, et les
# substituts UTF-16 isolés (ES2019 « well-formed JSON.stringify »).
_ESC_RE = re.compile("[\x00-\x1f\"\\\\\ud800-\udfff]")
_ESC_MAP = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _esc(m):
    c = m.group(0)
    return _ESC_MAP.get(c) or "\\u{:04x}".format(ord(c))


def dump_str(s):
    return '"' + _ESC_RE.sub(_esc, s) + '"'


def dumps_js(obj):
    """Sérialise comme `JSON.stringify(obj)` (sans indentation)."""
    parts = []
    _dump(obj, parts.append)
    return "".join(parts)


def _dump(o, w):
    if isinstance(o, str):
        w(dump_str(o))
    elif isinstance(o, dict):
        w("{")
        first = True
        for k, v in o.items():
            if not first:
                w(",")
            first = False
            w(dump_str(k))
            w(":")
            _dump(v, w)
        w("}")
    elif isinstance(o, list):
        w("[")
        for i, v in enumerate(o):
            if i:
                w(",")
            _dump(v, w)
        w("]")
    elif isinstance(o, RawNumber):
        w(o.text)
    elif o is True:
        w("true")
    elif o is False:
        w("false")
    elif o is None:
        w("null")
    elif isinstance(o, int):
        w(str(o))
    elif isinstance(o, float):
        w(json.dumps(o))
    else:
        raise TypeError("type non sérialisable : {}".format(type(o).__name__))


def split_eol(raw):
    """Sépare une ligne binaire de sa fin de ligne (`\\n` ou `\\r\\n`)."""
    if raw.endswith(b"\r\n"):
        return raw[:-2], b"\r\n"
    if raw.endswith(b"\n"):
        return raw[:-1], b"\n"
    return raw, b""


class AtomicWriter:
    """Écrit dans un fichier temporaire du même dossier, puis le renomme à la fermeture.

    En cas d'exception, le fichier temporaire est supprimé et la cible reste intacte.
    """

    def __init__(self, path, mode="wb"):
        self.path = path
        self.mode = mode
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, self.tmp = tempfile.mkstemp(prefix=".cct-", suffix=".tmp", dir=d)
        if "b" in mode:
            self.f = os.fdopen(fd, mode)
        else:
            self.f = os.fdopen(fd, mode, encoding="utf-8", newline="\n")

    def __enter__(self):
        return self.f

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.f.flush()
            try:
                os.fsync(self.f.fileno())
            except OSError:
                pass
            self.f.close()
            os.replace(self.tmp, self.path)
        else:
            self.f.close()
            try:
                os.unlink(self.tmp)
            except OSError:
                pass
        return False


def write_json_atomic(path, obj, indent=2):
    with AtomicWriter(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.write("\n")
