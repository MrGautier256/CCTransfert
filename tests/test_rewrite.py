import json

from cctransfert import paths as P
from cctransfert.jsonio import dumps_js
from cctransfert.rewrite import Rewriter, Rule

L2W = [
    Rule("projet", "/home/a/Projets/MonProjet", "C:\\Users\\b\\Documents de travail\\Projets\\MonProjet", P.POSIX, P.WINDOWS),
    Rule("config", "/home/a/.claude", "C:\\Users\\b\\.claude", P.POSIX, P.WINDOWS),
    Rule("home", "/home/a", "C:\\Users\\b", P.POSIX, P.WINDOWS),
]
W2L = [
    Rule("projet", "C:\\Users\\b\\Documents de travail\\Projets\\MonProjet", "/home/a/Projets/MonProjet", P.WINDOWS, P.POSIX),
    Rule("home", "C:\\Users\\b", "/home/a", P.WINDOWS, P.POSIX),
]
L2L = [
    Rule("projet", "/home/a/Projets/MonProjet", "/home/c/Travail/Projets/App/MonProjet", P.POSIX, P.POSIX),
    Rule("home", "/home/a", "/home/c", P.POSIX, P.POSIX),
]


def rw(rules, s_style, d_style, **kw):
    return Rewriter(rules, s_style, d_style, **kw)


def test_frontiere_monprojet_monprojet2():
    r = rw(L2L, P.POSIX, P.POSIX)
    assert r.rewrite_string("/home/a/Projets/MonProjet2/x", True) == "/home/c/Projets/MonProjet2/x"  # règle home seulement
    assert r.rewrite_string("/home/a/Projets/MonProjet/x", True) == "/home/c/Travail/Projets/App/MonProjet/x"
    assert r.rewrite_string("/home/a/Projets/MonProjet", True) == "/home/c/Travail/Projets/App/MonProjet"
    assert r.rewrite_string("/home/a/Projets/MonProjet.bak", True) == "/home/c/Projets/MonProjet.bak"
    assert r.rewrite_string("fin de phrase /home/a/Projets/MonProjet.") == "fin de phrase /home/c/Travail/Projets/App/MonProjet."
    assert r.rewrite_string("/opt/home/a/x") == "/opt/home/a/x"  # pas de match au milieu d'un chemin
    assert r.rewrite_string("/home/ab/x") == "/home/ab/x"


def test_texte_libre_plusieurs_chemins():
    r = rw(L2L, P.POSIX, P.POSIX)
    s = 'cd /home/a/Projets/MonProjet && cat "/home/a/.bashrc" src/x.py:12 /home/a/Projets/MonProjet/src/x.py:42'
    assert r.rewrite_string(s) == ('cd /home/c/Travail/Projets/App/MonProjet && cat "/home/c/.bashrc" src/x.py:12 '
                                   '/home/c/Travail/Projets/App/MonProjet/src/x.py:42')


def test_linux_vers_windows():
    r = rw(L2W, P.POSIX, P.WINDOWS)
    # chemin structuré -> forme native
    assert r.rewrite_string("/home/a/Projets/MonProjet/src/a.ts", True) == "C:\\Users\\b\\Documents de travail\\Projets\\MonProjet\\src\\a.ts"
    assert r.rewrite_string("/home/a/.claude/projects/x", True) == "C:\\Users\\b\\.claude\\projects\\x"
    # texte libre -> C:/… (pas d'antislash qui casserait une commande ou une chaîne)
    assert r.rewrite_string("ls /home/a/Projets/MonProjet/src -la") == "ls C:/Users/b/Documents de travail/Projets/MonProjet/src -la"


def test_windows_vers_linux_toutes_les_variantes():
    r = rw(W2L, P.WINDOWS, P.POSIX)
    for s in ["C:\\Users\\b\\Documents de travail\\Projets\\MonProjet\\src\\a.ts",
              "C:/Users/b/Documents de travail/Projets/MonProjet/src/a.ts",
              "c:\\users\\B\\documents de travail\\projets\\monprojet\\src\\a.ts",
              "/c/Users/b/Documents de travail/Projets/MonProjet/src/a.ts"]:
        assert r.rewrite_string(s, True) == "/home/a/Projets/MonProjet/src/a.ts", s
    # forme échappée (JSON dans une sortie de commande)
    assert r.rewrite_string('{"p":"C:\\\\Users\\\\b\\\\x\\\\y.json"}') == '{"p":"/home/a/x/y.json"}'
    # Git Bash dans une commande
    assert r.rewrite_string("cd /c/Users/b/Documents de travail/Projets/MonProjet/src && ls") == "cd /home/a/Projets/MonProjet/src && ls"


def test_texte_libre_segments_avec_espaces_connus():
    r = rw(W2L, P.WINDOWS, P.POSIX)
    s = "voir C:\\Users\\b\\Documents de travail\\Autre\\x.md et C:\\Users\\b\\Documents de travail fin"
    assert r.rewrite_string(s) == "voir /home/a/Documents de travail/Autre/x.md et /home/a/Documents de travail fin"
    # segment inconnu avec espace : conversion jusqu'à l'espace seulement (limite documentée)
    assert r.rewrite_string("voir C:\\Users\\b\\Mes Docs\\x et") == "voir /home/a/Mes Docs\\x et"


def test_windows_vers_windows_garde_la_forme():
    rules = [Rule("home", "C:\\Users\\a", "D:\\Users\\b", P.WINDOWS, P.WINDOWS)]
    r = rw(rules, P.WINDOWS, P.WINDOWS)
    assert r.rewrite_string("C:\\Users\\a\\x", True) == "D:\\Users\\b\\x"
    assert r.rewrite_string("C:/Users/a/x", True) == "D:/Users/b/x"
    assert r.rewrite_string("/c/Users/a/x") == "/d/Users/b/x"
    assert r.rewrite_string("C:\\\\Users\\\\a\\\\x") == "D:\\\\Users\\\\b\\\\x"


def test_regle_neutre_protege_le_prefixe():
    rules = [Rule("projet", "/home/a/P", "/home/a/P", P.POSIX, P.POSIX), Rule("home", "/home/a", "/home/z", P.POSIX, P.POSIX)]
    r = rw(rules, P.POSIX, P.POSIX)
    assert r.rewrite_string("/home/a/P/x") == "/home/a/P/x"
    assert r.rewrite_string("/home/a/Q/x") == "/home/z/Q/x"


def test_cles_de_dictionnaire_et_cles_relatives():
    r = rw(L2W, P.POSIX, P.WINDOWS)
    v = {"trackedFileBackups": {"src/a.ts": {"realParentDir": "/home/a/Projets/MonProjet/src"},
                                "/home/a/.claude/x.md": {"backupFileName": "abc@v1"}}}
    out = r.rewrite_value(v)
    keys = list(out["trackedFileBackups"].keys())
    assert keys == ["src\\a.ts", "C:\\Users\\b\\.claude\\x.md"]
    assert out["trackedFileBackups"]["src\\a.ts"]["realParentDir"] == "C:\\Users\\b\\Documents de travail\\Projets\\MonProjet\\src"
    assert out["trackedFileBackups"]["C:\\Users\\b\\.claude\\x.md"]["backupFileName"] == "abc@v1"


def test_thinking_signature_base64_et_ids_intacts():
    r = rw(L2L, P.POSIX, P.POSIX)
    th = {"type": "thinking", "thinking": "lire /home/a/Projets/MonProjet/a", "signature": "sig/home/a=="}
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA/home/a/xBBB"}}
    line = dumps_js({"type": "assistant", "uuid": "/home/a/u", "message": {"content": [th, img, {"type": "text", "text": "/home/a/x"}]}})
    out = r.rewrite_line(line.encode()).decode()
    assert dumps_js(th) in out  # octet par octet
    assert dumps_js(img) in out
    assert '"uuid":"/home/a/u"' in out
    assert '"text":"/home/c/x"' in out


def test_ligne_corrompue_et_lignes_opaques():
    r = rw(L2L, P.POSIX, P.POSIX)
    bad = b'\x00\x00{"type":"last-prompt","lastPrompt":"/home/a/x"}'
    assert r.rewrite_line(bad) == bad
    assert r.report.lines_bad == 1
    blob = dumps_js({"type": "api-request-blob", "hash": "h", "sessionId": "s", "message": {"t": "/home/a/x"}}).encode()
    assert r.rewrite_line(blob) == blob


def test_ligne_inchangee_identique_a_l_octet():
    r = rw(L2L, P.POSIX, P.POSIX)
    raw = b'{"type":"x","n":1.0,"t":"rien \\u00e9 ici"}'  # échappement non standard : conservé
    assert r.rewrite_line(raw) == raw


def test_champs_inconnus_preserves():
    r = rw(L2L, P.POSIX, P.POSIX)
    raw = b'{"type":"futur","zzz":{"deep":[{"p":"/home/a/Projets/MonProjet/x","n":1.50}]},"autre":42}'
    out = json.loads(r.rewrite_line(raw))
    assert out == {"type": "futur", "zzz": {"deep": [{"p": "/home/c/Travail/Projets/App/MonProjet/x", "n": 1.5}]}, "autre": 42}
    assert b'"n":1.50' in r.rewrite_line(raw)


def test_nouvel_id_partout_sauf_zones_protegees():
    old, new = "11111111-2222-4333-8444-555555555555", "99999999-2222-4333-8444-555555555555"
    r = rw(L2L, P.POSIX, P.POSIX, old_sid=old, new_sid=new)
    raw = dumps_js({"sessionId": old, "session_id": old, "x": "/tmp/claude-1/s/" + old + "/scratchpad",
                    "message": {"content": [{"type": "thinking", "thinking": old, "signature": "s"}]}}).encode()
    out = r.rewrite_line(raw).decode()
    assert old not in out.replace('"thinking":"{}"'.format(old), "")
    assert out.count(new) == 3


def test_url_file():
    r = rw([Rule("home", "/home/a", "C:\\Users\\b", P.POSIX, P.WINDOWS)], P.POSIX, P.WINDOWS)
    assert r.rewrite_string("ouvrir file:///home/a/x.html") == "ouvrir file:///C:/Users/b/x.html"
    r2 = rw([Rule("home", "C:\\Users\\b", "/home/a", P.WINDOWS, P.POSIX)], P.WINDOWS, P.POSIX)
    assert r2.rewrite_string("ouvrir file:///C:/Users/b/x.html") == "ouvrir file:///home/a/x.html"


def test_rapport_non_mappes():
    r = rw(L2L, P.POSIX, P.POSIX)
    r.rewrite_string("voir /srv/data/projet/x.txt et /usr/bin/python et /home/a/y")
    assert "/srv/data/projet/x.txt" in r.report.unmapped
    assert not any(k.startswith("/usr") for k in r.report.unmapped)
    assert r.report.counts["home"] == 1


def test_type_non_textuel_ne_plante_pas():
    # Régression trouvée sur un vrai transcript : schéma JSON avec "type" objet ou liste.
    r = rw(L2L, P.POSIX, P.POSIX)
    raw = dumps_js({"type": "assistant", "x": {"type": {"a": 1}, "p": "/home/a/x"},
                    "y": {"type": ["string", "null"], "q": "/home/a/y"}}).encode()
    out = json.loads(r.rewrite_line(raw))
    assert out["x"]["p"] == "/home/c/x" and out["y"]["q"] == "/home/c/y"
