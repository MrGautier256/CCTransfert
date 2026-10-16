"""Manipulation de chemins selon leur *style* (posix ou windows), indépendamment de l'OS hôte.

Un chemin venant d'une autre machine ne doit jamais passer par `os.path` : sous Linux,
`os.path` ne comprend pas `C:\\Users`. On choisit donc explicitement `posixpath` ou
`ntpath` selon le style du chemin.
"""

import ntpath
import posixpath
import re

POSIX = "posix"
WINDOWS = "windows"

_DRIVE_RE = re.compile(r"^[A-Za-z]:([\\/]|$)")


def style_of(path):
    """Devine le style d'un chemin absolu : `windows` pour `C:\\…`, `C:/…` ou `\\\\serveur\\…`."""
    if _DRIVE_RE.match(path) or path.startswith("\\\\"):
        return WINDOWS
    return POSIX


def mod(style):
    """Module de chemins correspondant au style."""
    return ntpath if style == WINDOWS else posixpath


def sep(style):
    return "\\" if style == WINDOWS else "/"


def join(style, *parts):
    return mod(style).join(*parts)


def is_abs(path, style):
    if style == WINDOWS:
        return bool(_DRIVE_RE.match(path)) or path.startswith("\\\\")
    return path.startswith("/")


def normalize(path, style):
    """Forme canonique d'un chemin absolu : séparateurs natifs, sans séparateur final.

    On n'utilise pas `normpath` sur les chemins posix pour ne pas toucher aux `//`
    éventuels ; on se contente de nettoyer les séparateurs de fin.
    """
    if style == WINDOWS:
        p = path.replace("/", "\\")
        p = ntpath.normpath(p)
        # `C:` seul -> `C:\`
        if re.fullmatch(r"[A-Za-z]:", p):
            p += "\\"
        return p
    p = path
    while len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def strip_sep(path, style):
    """Retire le séparateur final, sauf pour une racine (`/`, `C:\\`)."""
    s = sep(style)
    if style == WINDOWS:
        while path.endswith(("\\", "/")) and not re.fullmatch(r"[A-Za-z]:[\\/]", path):
            path = path[:-1]
        return path
    while len(path) > 1 and path.endswith(s):
        path = path[:-1]
    return path


def same_path(a, b, style):
    """Égalité de chemins : insensible à la casse et aux séparateurs sous Windows."""
    if style == WINDOWS:
        return normalize(a, style).lower() == normalize(b, style).lower()
    return normalize(a, style) == normalize(b, style)


def is_under(path, root, style):
    """`path` est-il `root` ou un descendant de `root` ?"""
    p = normalize(path, style)
    r = normalize(root, style)
    if style == WINDOWS:
        p, r = p.lower(), r.lower()
    if p == r:
        return True
    s = sep(style)
    return p.startswith(r if r.endswith(s) else r + s)


def rel_parts(path, root, style):
    """Segments de `path` relatifs à `root` (suppose `is_under`)."""
    p = normalize(path, style)
    r = normalize(root, style)
    rest = p[len(r):]
    return [x for x in re.split(r"[\\/]", rest) if x]


def segments(path, style):
    """Découpe un chemin absolu en segments. Sous Windows, le premier est le lecteur (`C:`)."""
    p = normalize(path, style)
    return [x for x in re.split(r"[\\/]", p) if x] if style == WINDOWS else [x for x in p.split("/") if x]


def basename(path, style):
    return mod(style).basename(strip_sep(path, style))


def with_drive_case(path, case):
    """Applique la convention de casse de lettre de lecteur (`upper`/`lower`) à un chemin Windows."""
    if case and _DRIVE_RE.match(path):
        d = path[0].upper() if case == "upper" else path[0].lower()
        return d + path[1:]
    return path
