"""Nom du dossier projet (« slug ») dans `<config>/projects/`.

Algorithme relevé dans le code de Claude Code 2.1.301 et vérifié empiriquement
(voir docs/FORMAT.md §2) :

    slug = chemin.replace(/[^a-zA-Z0-9]/g, "-")        // par unité UTF-16
    si len(slug) > 200 : slug[:200] + "-" + base36(abs(hashCode_java(chemin)))
"""

import re

MAX_SLUG = 200
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PROJECT_DIR_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RESERVED_WIN_RE = re.compile(r"^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])$", re.IGNORECASE)


def _utf16_units(s):
    """Unités de code UTF-16 de `s`, comme `String.charCodeAt` en JavaScript."""
    b = s.encode("utf-16-le", "surrogatepass")
    return [b[i] | (b[i + 1] << 8) for i in range(0, len(b), 2)]


def java_hash(s):
    """`String.hashCode` façon Java / JS `(h << 5) - h + c | 0`, entier signé 32 bits."""
    h = 0
    for u in _utf16_units(s):
        h = ((h << 5) - h + u) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h


def _base36(n):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


def sanitize(path):
    """Remplace chaque unité UTF-16 non `[A-Za-z0-9]` par `-` (un emoji donne donc `--`)."""
    return "".join(
        chr(u) if (48 <= u <= 57 or 65 <= u <= 90 or 97 <= u <= 122) else "-"
        for u in _utf16_units(path)
    )


def compute_slug(path):
    """Slug calculé par la règle de Claude Code, pour un chemin absolu déjà résolu (realpath)."""
    s = sanitize(path)
    if len(s) <= MAX_SLUG:
        return s
    return "{}-{}".format(s[:MAX_SLUG], _base36(abs(java_hash(path))))


def is_session_id(name):
    return bool(_UUID_RE.match(name))


def valid_project_dir_name(name):
    """Validation de `CLAUDE_CODE_PROJECT_DIR_NAME`, identique à celle de Claude Code."""
    return bool(name) and bool(_PROJECT_DIR_NAME_RE.match(name)) and not _RESERVED_WIN_RE.match(name)


def drive_case_of_slug(slug):
    """`upper`/`lower` si le slug commence par une lettre de lecteur (`C--…`), sinon None."""
    m = re.match(r"^([A-Za-z])--", slug)
    if not m:
        return None
    return "upper" if m.group(1).isupper() else "lower"


def slug_matches(dir_name, path, case_insensitive=False):
    """Le dossier `dir_name` peut-il être le dossier projet de `path` ?

    Pour un slug long, Claude Code accepte tout dossier commençant par les 200 premiers
    caractères suivis de `-` (le hash a changé d'implémentation au fil des versions).
    """
    expected = compute_slug(path)
    a, b = (dir_name.lower(), expected.lower()) if case_insensitive else (dir_name, expected)
    if a == b:
        return True
    if len(sanitize(path)) > MAX_SLUG:
        prefix = sanitize(path)[:MAX_SLUG] + "-"
        if case_insensitive:
            prefix = prefix.lower()
        return a.startswith(prefix)
    return False
