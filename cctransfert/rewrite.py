"""Réécriture des chemins (et éventuellement de l'id de session) dans les données d'une session.

Principes (voir README, section « Réécriture des chemins ») :

* On travaille sur le JSON parsé : valeurs ET clés de dictionnaire.
* Une table de règles `préfixe source -> préfixe cible`, appliquée du préfixe le plus
  long au plus court. Une règle « neutre » (source == cible) reste dans la table : elle
  empêche une règle plus courte de s'appliquer à ce préfixe.
* Une source Windows est reconnue sous toutes ses formes : `C:\\a`, `C:/a`, `c:\\a`,
  `C:\\\\a` (échappée) et `/c/a` (Git Bash), sans tenir compte de la casse.
* Frontière obligatoire autour du préfixe : `/MonProjet` ne matche pas `/MonProjet2`.
* Le reste du chemin voit ses séparateurs convertis quand le style change. C'est exact
  pour une chaîne qui EST un chemin, et approximatif (jusqu'au prochain espace ou
  guillemet) dans le texte libre.
* Jamais modifiés : blocs `thinking`/`redacted_thinking`, `signature`, données base64,
  identifiants (`uuid`, `parentUuid`…), et les lignes de diagnostic `api-request*`.
* Une ligne identique ressort à l'octet près ; une ligne illisible est recopiée telle
  quelle avec un avertissement.
"""

import re
from collections import Counter, OrderedDict

from . import paths as P
from .jsonio import RawNumber, dumps_js, loads_raw, split_eol

# Clés dont la valeur est un chemin : conversion exacte des séparateurs.
PATH_KEYS = frozenset(
    {
        "cwd", "file_path", "filePath", "path", "paths", "notebook_path", "notebookPath",
        "realParentDir", "trackingPath", "persistedOutputPath", "outputFile", "planFilePath",
        "skillDir", "filename", "relocatedCwd", "workingDirectory", "root", "out_dir",
        "project", "originCwd", "worktreePath", "directory", "dir", "projectPath",
        "transcript_path", "agent_transcript_path", "additionalWorkingDirectories",
    }
)

# Clés dont la valeur ne doit jamais être touchée (identifiants, hachages, signatures).
PROTECTED_KEYS = frozenset(
    {
        "uuid", "parentUuid", "leafUuid", "logicalParentUuid", "messageId", "snapshotMessageId",
        "promptId", "requestId", "id", "tool_use_id", "toolUseID", "toolUseId",
        "sourceToolAssistantUUID", "sourceToolUseID", "agentId", "hash", "shapeHash",
        "signature", "backupFileName", "source_uuid", "delivery_id", "deliveryId",
        "commandUuid", "interruptedMessageId", "supersedesUuids", "retractedMessageUuids",
        "refusedUserMessageUuid", "summarizedTurnPromptUuids", "base64",
    }
)

# Types de blocs signés : on ne touche à rien à l'intérieur.
PROTECTED_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})

# Lignes de diagnostic adressées par hash (docs/FORMAT.md §6) : recopiées telles quelles,
# seul `sessionId` change avec --new-id.
OPAQUE_LINE_TYPES = frozenset({"api-request-blob", "api-request", "api-request-shape"})

# Fin d'un chemin dans le texte libre.
_FREE_STOP = set(" \t\r\n\v\f\"'`<>|()[]{},;*?:&=!")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/=_-]+")

# Frontières autour d'un préfixe.
_BEFORE = r"(?:(?<=file://)|(?<=file:///)|(?<![\w.\-~/\\$%]))"
_AFTER = r"(?![\w\-~$%@+])(?!\.[\w\-])"
_WIN_SEP = r"(?:\\\\|[\\/])"  # `\\` (échappé), `\` ou `/`

# Rapport des chemins non mappés : seulement les racines où vivent des données utilisateur
# (pas les routes d'API `/api/v2/…` ni les imports `@/core/…`).
_USER_ROOTS = {"home", "Users", "tmp", "mnt", "media", "srv", "root", "data", "workspace", "workspaces",
               "Volumes", "opt"}
_UNMAPPED_POSIX = re.compile(r"(?<![\w.\-~/\\:@$})*])/[\w.\-]+(?:/[\w.\-@+]+)+")
_UNMAPPED_WIN = re.compile(r"(?<![\w])[A-Za-z]:[\\/]+[^\s\"'<>|:*?]+")


class Rule:
    """Une correspondance `src -> dst`. `src` est au style source, `dst` au style cible."""

    def __init__(self, name, src, dst, src_style, dst_style):
        self.name = name
        self.src = P.strip_sep(src, src_style)
        self.dst = P.strip_sep(dst, dst_style)
        self.src_style = src_style
        self.dst_style = dst_style

    @property
    def noop(self):
        if self.src_style != self.dst_style:
            return False
        return self.src == self.dst

    def __repr__(self):
        return "Rule({}: {} -> {})".format(self.name, self.src, self.dst)


def _posix_pattern(prefix):
    segs = [s for s in prefix.split("/") if s]
    return "/" + "/".join(re.escape(s) for s in segs)


def _win_patterns(prefix):
    """Motifs regex d'un préfixe Windows : forme lecteur (séparateurs mélangés) et Git Bash."""
    norm = P.normalize(prefix, P.WINDOWS)
    if norm.startswith("\\\\"):  # chemin UNC : \\serveur\partage\…
        segs = [s for s in norm.split("\\") if s]
        pat = _WIN_SEP + _WIN_SEP + _WIN_SEP.join(re.escape(s) for s in segs)
        return ["(?i:" + pat + ")"]
    drive, segs = norm[0], [s for s in norm[2:].split("\\") if s]
    pat_drive = re.escape(drive) + ":" + "".join(_WIN_SEP + re.escape(s) for s in segs)
    pat_git = "/" + re.escape(drive) + "".join("/" + re.escape(s) for s in segs)
    return ["(?i:" + pat_drive + ")", "(?i:" + pat_git + ")"]


def _form_of(matched):
    """Forme d'un chemin Windows trouvé : git (`/c/…`), esc (`C:\\\\…`), back (`C:\\…`), fwd (`C:/…`)."""
    if matched.startswith("/"):
        return "git"
    if "\\\\" in matched:
        return "esc"
    if "\\" in matched:
        return "back"
    if "/" in matched:
        return "fwd"
    return "back"


def _render_win(native, form):
    """Écrit un chemin Windows natif (`C:\\…`) sous la forme demandée."""
    if form == "fwd":
        return native.replace("\\", "/")
    if form == "esc":
        return native.replace("\\", "\\\\")
    if form == "git" and re.match(r"^[A-Za-z]:", native):
        rest = native[2:].replace("\\", "/")
        return "/" + native[0].lower() + (rest if rest.startswith("/") else "/" + rest)
    if form == "git":
        return native.replace("\\", "/")
    return native


def _longest_segment(prefix):
    segs = [s for s in re.split(r"[\\/]", prefix) if s and not s.endswith(":")]
    return max(segs, key=len) if segs else prefix


class Report:
    """Statistiques de réécriture, cumulées sur tous les fichiers traités."""

    def __init__(self, max_examples=3):
        self.counts = Counter()
        self.examples = OrderedDict()
        self.unmapped = Counter()
        self.warnings = []
        self.lines_total = 0
        self.lines_changed = 0
        self.lines_bad = 0
        self.max_examples = max_examples

    def example(self, rule, before, after):
        lst = self.examples.setdefault(rule, [])
        if len(lst) < self.max_examples and (before, after) not in lst:
            lst.append((before, after))

    def warn(self, msg):
        if len(self.warnings) < 200:
            self.warnings.append(msg)


class Rewriter:
    """Applique une table de règles aux transcripts, fichiers JSON et textes d'une session."""

    def __init__(self, rules, src_style, dst_style, old_sid=None, new_sid=None, report=None):
        self.src_style = src_style
        self.dst_style = dst_style
        self.style_change = src_style != dst_style
        self.old_sid = old_sid if (old_sid and new_sid and old_sid != new_sid) else None
        self.new_sid = new_sid if self.old_sid else None
        self.report = report or Report()
        # Du plus long au plus court : à une position donnée, l'alternative la plus longue gagne.
        self.rules = sorted(rules, key=lambda r: len(r.src), reverse=True)
        self.active = any(not r.noop for r in self.rules) or self.old_sid is not None or self.style_change

        alts = []
        self._group_rule = {}
        for i, r in enumerate(self.rules):
            pats = _win_patterns(r.src) if src_style == P.WINDOWS else [_posix_pattern(r.src)]
            for j, p in enumerate(pats):
                g = "r{}_{}".format(i, j)
                self._group_rule[g] = r
                alts.append("(?P<{}>{})".format(g, p))
        self._re = re.compile(_BEFORE + "(?:" + "|".join(alts) + ")" + _AFTER) if alts else None

        # Filtre rapide par ligne : on ne parcourt le JSON que si une « aiguille » apparaît.
        self._ci = src_style == P.WINDOWS
        needles = {(_longest_segment(r.src).lower() if self._ci else _longest_segment(r.src)) for r in self.rules if not r.noop}
        if self.style_change:
            needles |= {(_longest_segment(r.src).lower() if self._ci else _longest_segment(r.src)) for r in self.rules}
        self._needles = [n for n in needles if n]
        self._unmapped_re = _UNMAPPED_WIN if src_style == P.WINDOWS else _UNMAPPED_POSIX
        # Segments connus contenant des espaces (ex. « Documents de travail ») : dans le texte
        # libre, on continue le chemin à travers eux au lieu de s'arrêter au premier espace.
        spaced = set()
        for r in self.rules:
            for seg in re.split(r"[\\/]", r.src):
                if any(c.isspace() for c in seg):
                    spaced.add(seg.lower() if self._ci else seg)
        self._spaced = sorted(spaced, key=len, reverse=True)

    # -- chaînes -------------------------------------------------------------------

    def _target_prefix(self, rule, matched, exact):
        """Préfixe cible, dans la forme adaptée à la forme trouvée et au style cible."""
        if self.dst_style == P.WINDOWS:
            if self.src_style == P.WINDOWS:
                return _render_win(rule.dst, _form_of(matched))
            # posix -> windows : forme native dans un chemin structuré, `C:/…` dans le texte
            # libre (plus sûr : pas d'antislash qui créerait des séquences d'échappement).
            return rule.dst if exact else rule.dst.replace("\\", "/")
        return rule.dst

    def _convert_rest(self, rest, matched, exact):
        """Convertit les séparateurs du reste du chemin si le style change."""
        if not self.style_change or not rest:
            return rest
        if self.src_style == P.WINDOWS:  # windows -> posix
            form = _form_of(matched)
            if form == "esc":
                return rest.replace("\\\\", "/")
            if form == "back":
                return rest.replace("\\", "/")
            # forme `C:/…` ou Git Bash : `/` est déjà le séparateur ; un antislash qui suit
            # n'en est pas un (ex. `\n` littéral), on n'y touche pas.
            return rest
        # posix -> windows : seulement pour un chemin structuré (texte libre en `C:/…`).
        return rest.replace("/", "\\") if exact else rest

    def rewrite_string(self, s, path_hint=False):
        """Réécrit une chaîne. Renvoie l'objet `s` lui-même s'il n'y a rien à changer."""
        if not s or self._re is None:
            return self._replace_sid(s)
        if len(s) > 2000 and " " not in s and "\n" not in s and _BASE64_RE.fullmatch(s):
            return s  # donnée base64 (image…)
        m = self._re.search(s)
        if m is None:
            if len(s) < 5000:
                self._scan_unmapped(s)
            return self._replace_sid(s)

        out = []
        pos = 0
        changed = False
        while m is not None:
            g = m.lastgroup
            rule = self._group_rule[g]
            start, end = m.start(), m.end()
            matched = m.group(0)
            # Chaîne qui EST un chemin : conversion exacte de tout le reste.
            exact = start == 0 and "\n" not in s and (path_hint or not any(c.isspace() for c in s[end:]))
            rest_end = len(s) if exact else self._free_rest_end(s, end)
            rest = s[end:rest_end]
            if len(s) < 5000:
                self._scan_unmapped(s[pos:start])
            if rule.noop:
                # Règle neutre : on recopie, mais elle a « protégé » ce préfixe.
                out.append(s[pos:rest_end])
            else:
                new_prefix = self._target_prefix(rule, matched, exact)
                new_rest = self._convert_rest(rest, matched, exact)
                cut = start
                if s.endswith(("file://", "file:///"), 0, start):
                    # URL file: `file:///home/…` <-> `file:///C:/…`
                    if self.dst_style == P.WINDOWS and not new_prefix.startswith("/"):
                        new_prefix = "/" + new_prefix.replace("\\", "/")
                        new_rest = new_rest.replace("\\", "/")
                    elif self.dst_style == P.POSIX and s.endswith("file:///", 0, start):
                        cut = start - 1
                piece = new_prefix + new_rest
                old_piece = s[cut:rest_end]
                out.append(s[pos:cut])
                out.append(piece)
                if piece != old_piece:
                    changed = True
                    self.report.counts[rule.name] += 1
                    self.report.example(rule.name, _clip(old_piece), _clip(piece))
            pos = rest_end
            m = self._re.search(s, pos)
        out.append(s[pos:])
        if len(s) < 5000:
            self._scan_unmapped(s[pos:])
        result = "".join(out) if changed else s
        return self._replace_sid(result)

    def _free_rest_end(self, s, end):
        """Fin du chemin commencé en `end` dans du texte libre (avant espace, guillemet…)."""
        i, n = end, len(s)
        while i < n:
            c = s[i]
            if c not in _FREE_STOP:
                i += 1
                continue
            if c == " " and self._spaced:
                seg_start = max(s.rfind("/", end, i), s.rfind("\\", end, i)) + 1
                seg_start = max(seg_start, end)
                hay = s[seg_start:seg_start + len(self._spaced[0]) + 1]
                if self._ci:
                    hay = hay.lower()
                hit = next((g for g in self._spaced if hay.startswith(g)
                            and (len(hay) == len(g) or hay[len(g)] in "\\/" or hay[len(g)] in _FREE_STOP)), None)
                if hit and seg_start + len(hit) > i:
                    i = seg_start + len(hit)
                    continue
            break
        return i

    def _replace_sid(self, s):
        if self.old_sid and s and self.old_sid in s:
            self.report.counts["session-id"] += s.count(self.old_sid)
            return s.replace(self.old_sid, self.new_sid)
        return s

    def _scan_unmapped(self, text):
        if not text or ("/" not in text and "\\" not in text):
            return
        for m in self._unmapped_re.finditer(text):
            tok = m.group(0)
            segs = [x for x in re.split(r"[\\/]+", tok) if x]
            if not segs:
                continue
            if self.src_style == P.POSIX and segs[0] not in _USER_ROOTS:
                continue
            if self.src_style == P.WINDOWS and len(segs) > 1 and segs[1].lower() in ("windows", "program files", "program files (x86)", "programdata"):
                continue
            sepc = "\\" if self.src_style == P.WINDOWS else "/"
            head = sepc.join(segs[:4])
            key = head if self.src_style == P.WINDOWS else "/" + head
            self.report.unmapped[key] += 1

    def convert_relative(self, s):
        """Chemin relatif (clés de file-history) : adapte les séparateurs au style cible."""
        if not self.style_change or not isinstance(s, str) or not s or P.is_abs(s, self.src_style):
            return s
        if self.src_style == P.WINDOWS and "\\" in s:
            self.report.counts["relatif"] += 1
            return s.replace("\\", "/")
        if self.src_style == P.POSIX and "/" in s and not s.startswith("~"):
            self.report.counts["relatif"] += 1
            return s.replace("/", "\\")
        return s

    # -- JSON ---------------------------------------------------------------------

    def rewrite_value(self, v, key=None, parent_key=None):
        """Réécrit récursivement une valeur JSON (renvoie le même objet si inchangée)."""
        if isinstance(v, str):
            return self.rewrite_string(v, path_hint=key in PATH_KEYS)
        if isinstance(v, list):
            out = None
            for i, x in enumerate(v):
                nx = self.rewrite_value(x, key, parent_key)
                if nx is not x:
                    if out is None:
                        out = list(v)
                    out[i] = nx
            return v if out is None else out
        if isinstance(v, dict):
            t = v.get("type")
            if not isinstance(t, str):  # ex. schéma JSON : "type": {...} ou ["string", "null"]
                t = None
            if t in PROTECTED_BLOCK_TYPES:
                return v
            is_b64 = t == "base64"
            changed = False
            items = []
            for k, x in v.items():
                if k in PROTECTED_KEYS or (is_b64 and k == "data"):
                    nx = x
                elif k == "trackingPath" and isinstance(x, str) and not P.is_abs(x, self.src_style):
                    nx = self.convert_relative(x)
                else:
                    nx = self.rewrite_value(x, k, key)
                if isinstance(k, str):
                    if key == "trackedFileBackups" and not P.is_abs(k, self.src_style):
                        nk = self.convert_relative(k)
                    else:
                        nk = self.rewrite_string(k, path_hint=True)
                else:
                    nk = k
                if nk is not k or nx is not x:
                    changed = True
                items.append((nk, nx))
            if not changed:
                return v
            new = {}
            for nk, nx in items:
                if nk in new:
                    self.report.warn("clé en double après réécriture : {}".format(_clip(nk)))
                new[nk] = nx
            return new
        return v

    def _needle_hit(self, text):
        if self.old_sid and self.old_sid in text:
            return True
        hay = text.lower() if self._ci else text
        return any(n in hay for n in self._needles)

    def rewrite_line(self, body, where=""):
        """Réécrit le contenu d'une ligne JSONL (sans fin de ligne). Renvoie des octets."""
        self.report.lines_total += 1
        if not body.strip():
            return body
        try:
            text = body.decode("utf-8")
            obj = loads_raw(text)
        except ValueError:  # UnicodeDecodeError hérite de ValueError
            self.report.lines_bad += 1
            self.report.warn("ligne illisible recopiée telle quelle : {}".format(where))
            return body
        if not self.active or not self._needle_hit(text):
            return body
        if isinstance(obj, dict) and isinstance(obj.get("type"), str) and obj["type"] in OPAQUE_LINE_TYPES:
            if self.old_sid and obj.get("sessionId") == self.old_sid:
                obj["sessionId"] = self.new_sid
                self.report.counts["session-id"] += 1
                self.report.lines_changed += 1
                return dumps_js(obj).encode("utf-8")
            return body
        try:
            new = self.rewrite_value(obj)
            out = None if new is obj else dumps_js(new).encode("utf-8")
        except Exception as e:  # noqa: BLE001 - ne jamais casser une ligne : on la recopie
            self.report.warn("réécriture impossible, ligne recopiée telle quelle ({}) : {}".format(type(e).__name__, where))
            return body
        if out is None:
            return body
        self.report.lines_changed += 1
        return out

    def rewrite_jsonl_stream(self, src, write, label=""):
        """Lit un flux binaire JSONL ligne à ligne et écrit le résultat via `write(bytes)`."""
        for n, raw in enumerate(src, 1):
            body, eol = split_eol(raw)
            write(self.rewrite_line(body, "{}:{}".format(label, n)) + eol)

    def rewrite_json_bytes(self, data, label=""):
        """Fichier `.json` : réécrit si parsable, sinon recopie."""
        if not self.active:
            return data
        try:
            obj = loads_raw(data.decode("utf-8"))
        except ValueError:
            return data
        new = self.rewrite_value(obj)
        if new is obj:
            return data
        trailing = b"\n" if data.endswith(b"\n") else b""
        return dumps_js(new).encode("utf-8") + trailing

    def rewrite_text_bytes(self, data, label=""):
        """Fichier texte (`.md`, `.txt`) : réécriture « texte libre »."""
        if not self.active:
            return data
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        new = self.rewrite_string(text)
        return data if new is text else new.encode("utf-8")


def _clip(s, n=150):
    s = s.replace("\n", "⏎")
    return s if len(s) <= n else s[: n - 1] + "…"


def build_rules(source, target_profile, target_root, target_slug, user_maps=()):
    """Table de règles pour un import.

    `source` : dict `manifest["source"]` (project_root, slug, config_dir, temp_root, home, style).
    `user_maps` : liste de couples (ancien, nouveau) saisis avec --map.
    """
    ss, ds = source["style"], target_profile.style
    sj = lambda *p: P.join(ss, *p)  # noqa: E731
    dj = target_profile.join
    rules = [
        Rule("projet", source["project_root"], target_root, ss, ds),
        Rule("config-projet", sj(source["config_dir"], "projects", source["slug"]),
             dj(target_profile.config_dir, "projects", target_slug), ss, ds),
        Rule("config", source["config_dir"], target_profile.config_dir, ss, ds),
        Rule("home", source["home"], target_profile.home, ss, ds),
    ]
    if source.get("temp_root") and target_profile.temp_root:
        rules.append(Rule("temp-projet", sj(source["temp_root"], source["slug"]),
                          dj(target_profile.temp_root, target_slug), ss, ds))
        rules.append(Rule("temp", source["temp_root"], target_profile.temp_root, ss, ds))
    for old, new in user_maps:
        rules.append(Rule("map", old, new, ss, ds))
    # En cas de doublon de préfixe source, la dernière règle (--map) l'emporte.
    by_src = OrderedDict()
    for r in rules:
        k = r.src.lower() if ss == P.WINDOWS else r.src
        by_src[k] = r
    return list(by_src.values())


__all__ = ["Rule", "Rewriter", "Report", "build_rules", "RawNumber"]
