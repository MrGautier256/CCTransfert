"""Tests de bout en bout export -> import entre fausses machines (Linux et Windows simulé)."""

import hashlib
import json
import os
import time
import zipfile

import pytest

from conftest import (SID, SIGNATURE, THINKING_TEXT, build_session, make_machine, parse_jsonl, read_lines,
                      session_info, write, line)
from cctransfert import paths as P
from cctransfert.bundle import BundleError, BundleReader
from cctransfert.exporter import export_session
from cctransfert.importer import ImportError_, ImportOptions, import_bundle, last_journal, undo, undo_check
from cctransfert.jsonio import dumps_js
from cctransfert.slug import compute_slug
from cctransfert.store import find_session_files, list_sessions

ROOT_A = "/home/a/Projets/MonProjet"
ROOT_B = "/home/bob/Travail/Projets/App/MonProjet"
ROOT_W = "C:\\Users\\alice\\Documents de travail\\Projets\\MonProjet"


def export(prof, slug, tmp_path, name="s.CCTransfert", **kw):
    out = str(tmp_path / name)
    kw.setdefault("with_memory", True)
    kw.setdefault("with_history", True)
    export_session(prof, session_info(prof, slug), out, **kw)
    return out


def tree(base):
    out = {}
    for d, _dirs, files in os.walk(str(base)):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, str(base))] = hashlib.sha256(fh.read()).hexdigest()
    return out


def transcript_bytes(prof, slug, sid=SID):
    with open(prof.fs(prof.join(prof.projects_dir, slug, sid + ".jsonl")), "rb") as f:
        return f.read()


def users(prof, slug, sid=SID):
    return [o for o in parse_jsonl(prof, prof.join(prof.projects_dir, slug, sid + ".jsonl")) if o and o.get("type") == "user"]


# -- scénarios de base -------------------------------------------------------------------------


def test_listing_et_titre(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    build_session(A, ROOT_A)
    (s,) = list_sessions(A)
    assert s.sid == SID and s.title == "Mon titre é" and s.root == ROOT_A
    assert s.first_prompt.startswith("Travaille dans")


def test_linux_vers_linux_autre_utilisateur(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    orig = transcript_bytes(A, slug_a)
    B = make_machine(tmp_path, "pcB", "linux", "/home/bob")
    bundle = export(A, slug_a, tmp_path)

    res = import_bundle(bundle, B, ImportOptions(ROOT_B))
    slug_b = "-home-bob-Travail-Projets-App-MonProjet"
    assert res.slug == slug_b
    data = transcript_bytes(B, slug_b)
    objs = parse_jsonl(B, B.join(B.projects_dir, slug_b, SID + ".jsonl"))

    assert all(o["cwd"] == ROOT_B for o in users(B, slug_b))
    first = users(B, slug_b)[0]["message"]["content"]
    # MonProjet -> nouveau projet, MonProjet2 -> seulement la règle home
    assert first == "Travaille dans {} et compare avec /home/bob/Projets/MonProjet2/README.md".format(ROOT_B)
    # bloc thinking identique octet par octet
    th = dumps_js({"type": "thinking", "thinking": THINKING_TEXT, "signature": SIGNATURE}).encode()
    assert th in orig and th in data
    # ligne corrompue et ligne api-request-blob recopiées telles quelles
    bad = [ln for ln in orig.split(b"\n") if ln.startswith(b"\x00")][0]
    assert bad in data.split(b"\n")
    blob = [ln for ln in orig.split(b"\n") if b"api-request-blob" in ln][0]
    assert blob in data.split(b"\n")
    # nombres conservés tels quels, champ inconnu préservé
    assert b'"nombres":[1.0,1e+21,12345678901234567890]' in data
    fut = [o for o in objs if o and o.get("type") == "type-du-futur"][0]
    assert list(fut["inconnu"]) == [ROOT_B + "/cle"] and b'2.5' in data
    # chemins vers les données de session, avec le nouveau slug
    tur = users(B, slug_b)[1]["toolUseResult"]
    assert tur["persistedOutputPath"] == "/home/bob/.claude/projects/{}/{}/tool-results/b1.txt".format(slug_b, SID)
    assert os.path.isfile(B.fs(tur["persistedOutputPath"]))
    snap = [o for o in objs if o and o.get("type") == "file-history-snapshot"][0]["snapshot"]["trackedFileBackups"]
    assert list(snap) == ["src/a.ts", "/tmp/claude-1000/{}/{}/scratchpad/p.php".format(slug_b, SID)]
    # fichiers annexes
    cfg = B.config_dir
    assert os.path.isfile(B.fs(B.join(B.projects_dir, slug_b, SID, "subagents", "agent-abc.jsonl")))
    with open(B.fs(B.join(cfg, "file-history", SID, "abcd@v1")), encoding="utf-8") as f:
        assert ROOT_A in f.read()  # contenu de fichier sauvegardé : jamais réécrit
    with open(B.fs(B.join(cfg, "plans", "happy-cat-dog.md")), encoding="utf-8") as f:
        assert ROOT_B + "/src/a.ts" in f.read()
    with open(B.fs(B.join(B.projects_dir, slug_b, "memory", "MEMORY.md")), encoding="utf-8") as f:
        assert ROOT_B in f.read()
    hist = [json.loads(x) for x in read_lines(B, B.join(cfg, "history.jsonl")) if x.strip()]
    assert hist == [{"display": "Travaille dans " + ROOT_B, "pastedContents": {}, "timestamp": 2, "project": ROOT_B, "sessionId": SID}]
    assert res.report.counts["projet"] > 5 and res.report.lines_bad == 1


def test_linux_vers_windows(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    W = make_machine(tmp_path, "pcW", "windows", "C:\\Users\\alice")
    res = import_bundle(export(A, slug_a, tmp_path), W, ImportOptions(ROOT_W))
    slug_w = "C--Users-alice-Documents-de-travail-Projets-MonProjet"
    assert res.slug == slug_w
    u = users(W, slug_w)
    assert u[0]["cwd"] == ROOT_W
    objs = parse_jsonl(W, W.join(W.projects_dir, slug_w, SID + ".jsonl"))
    asst = [o for o in objs if o and o.get("type") == "assistant"][0]["message"]["content"]
    assert asst[2]["input"]["file_path"] == ROOT_W + "\\src\\a.ts"
    fwd = ROOT_W.replace("\\", "/")
    assert asst[3]["input"]["command"] == "cd {} && ls {}/src".format(fwd, fwd)
    snap = [o for o in objs if o and o.get("type") == "file-history-snapshot"][0]["snapshot"]["trackedFileBackups"]
    assert list(snap) == ["src\\a.ts", "C:\\Users\\alice\\AppData\\Local\\Temp\\claude-0\\{}\\{}\\scratchpad\\p.php".format(slug_w, SID)]
    assert snap["src\\a.ts"]["realParentDir"] == ROOT_W + "\\src"
    tur = u[1]["toolUseResult"]
    assert tur["persistedOutputPath"] == "C:\\Users\\alice\\.claude\\projects\\{}\\{}\\tool-results\\b1.txt".format(slug_w, SID)
    assert os.path.isfile(W.fs(tur["persistedOutputPath"]))


def test_windows_vers_linux(tmp_path):
    W = make_machine(tmp_path, "pcW", "windows", "C:\\Users\\alice")
    slug_w = build_session(W, ROOT_W)
    B = make_machine(tmp_path, "pcB", "linux", "/home/bob")
    res = import_bundle(export(W, slug_w, tmp_path), B, ImportOptions(ROOT_B))
    slug_b = compute_slug(ROOT_B)
    objs = parse_jsonl(B, B.join(B.projects_dir, slug_b, SID + ".jsonl"))
    assert users(B, slug_b)[0]["cwd"] == ROOT_B
    asst = [o for o in objs if o and o.get("type") == "assistant"][0]["message"]["content"]
    assert asst[2]["input"]["file_path"] == ROOT_B + "/src/a.ts"
    snap = [o for o in objs if o and o.get("type") == "file-history-snapshot"][0]["snapshot"]["trackedFileBackups"]
    assert "src/a.ts" in snap
    assert users(B, slug_b)[0]["message"]["content"].endswith("/home/bob/Documents de travail/Projets/MonProjet2/README.md")
    assert not res.report.unmapped or all("alice" not in k for k in res.report.unmapped)


def test_espaces_accents_points(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    root = "/home/a/Mes Projets/Dossier.été_v2"
    slug_a = build_session(A, root)
    B = make_machine(tmp_path, "pcB", "linux", "/home/z")
    target = "/home/z/Trav ail/Dossier.été_v2"
    res = import_bundle(export(A, slug_a, tmp_path), B, ImportOptions(target))
    assert res.slug == "-home-z-Trav-ail-Dossier--t--v2"
    u = users(B, res.slug)
    assert u[0]["cwd"] == target
    assert u[0]["message"]["content"].startswith("Travaille dans " + target + " et")


# -- id, conflits, sessions en cours -------------------------------------------------------------


def test_new_id(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    res = import_bundle(export(A, slug_a, tmp_path), A, ImportOptions("/home/a/Copie/MonProjet", new_id=True))
    new = res.new_sid
    assert new != SID
    assert find_session_files(A, SID) == [slug_a]
    assert find_session_files(A, new) == [res.slug]
    data = transcript_bytes(A, res.slug, new)
    objs = parse_jsonl(A, A.join(A.projects_dir, res.slug, new + ".jsonl"))
    assert all(o["sessionId"] == new for o in objs if o and "sessionId" in o)
    assert SID.encode() not in data.replace(b"\x00", b"")  # même la ligne api-request-blob a le nouvel id
    assert os.path.isfile(A.fs(A.join(A.config_dir, "file-history", new, "abcd@v1")))
    assert os.path.isfile(A.fs(A.join(A.projects_dir, res.slug, new, "subagents", "agent-abc.jsonl")))
    sub = parse_jsonl(A, A.join(A.projects_dir, res.slug, new, "subagents", "agent-abc.jsonl"))
    assert sub[0]["sessionId"] == new and sub[0]["cwd"] == "/home/a/Copie/MonProjet"


def test_conflit_meme_slug_puis_replace_et_undo(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    bundle = export(A, slug_a, tmp_path)
    import_bundle(bundle, B, ImportOptions("/home/b/K"))
    first = transcript_bytes(B, compute_slug("/home/b/K"))
    with open(B.fs(B.join(B.projects_dir, compute_slug("/home/b/K"), SID + ".jsonl")), "ab") as f:
        f.write(b'{"type":"ajout-sur-B"}\n')
    with pytest.raises(ImportError_, match="--replace"):
        import_bundle(bundle, B, ImportOptions("/home/b/K"))
    res = import_bundle(bundle, B, ImportOptions("/home/b/K", replace=True))
    assert transcript_bytes(B, res.slug) == first
    d, data = last_journal()
    assert undo_check(data) == []
    undo(d, data)
    assert transcript_bytes(B, res.slug).endswith(b'{"type":"ajout-sur-B"}\n')


def test_conflit_autre_slug(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    bundle = export(A, slug_a, tmp_path)
    with pytest.raises(ImportError_, match="--new-id"):
        import_bundle(bundle, A, ImportOptions("/home/a/Ailleurs"))
    # question interactive refusée -> même erreur
    with pytest.raises(ImportError_):
        import_bundle(bundle, A, ImportOptions("/home/a/Ailleurs", confirm=lambda q: False))
    res = import_bundle(bundle, A, ImportOptions("/home/a/Ailleurs", confirm=lambda q: True))
    assert find_session_files(A, SID) == [res.slug]  # déplacée : une seule copie
    d, data = last_journal()
    undo(d, data)
    assert find_session_files(A, SID) == [slug_a]
    assert os.path.isfile(A.fs(A.join(A.projects_dir, slug_a, SID, "subagents", "agent-abc.jsonl")))


def test_session_en_cours_refusee(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    with open("/proc/self/stat") as f:
        raw = f.read()
    start = raw[raw.rfind(")") + 2:].split()[19]
    write(B, B.join(B.config_dir, "sessions", "{}.json".format(os.getpid())),
          json.dumps({"pid": os.getpid(), "sessionId": SID, "procStart": start, "cwd": "/home/b/K"}))
    with pytest.raises(ImportError_, match="en cours"):
        import_bundle(export(A, slug_a, tmp_path), B, ImportOptions("/home/b/K"))


# -- aller-retour, dry-run, undo, mtime -----------------------------------------------------------


@pytest.mark.parametrize("os_b,home_b,root_b", [
    ("linux", "/home/bob", ROOT_B),
    ("windows", "C:\\Users\\alice", ROOT_W),
])
def test_aller_retour_idempotent(tmp_path, os_b, home_b, root_b):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    before = tree(tmp_path / "pcA")
    orig = transcript_bytes(A, slug_a)
    B = make_machine(tmp_path, "pcB", os_b, home_b)
    res_b = import_bundle(export(A, slug_a, tmp_path, "aller.CCTransfert"), B, ImportOptions(root_b))
    assert transcript_bytes(B, res_b.slug) != orig
    back = export(B, res_b.slug, tmp_path, "retour.CCTransfert")
    res_a = import_bundle(back, A, ImportOptions(ROOT_A, replace=True))
    assert res_a.slug == slug_a
    assert transcript_bytes(A, slug_a) == orig
    after = tree(tmp_path / "pcA")
    # mêmes fichiers, même contenu (hors history.jsonl dédoublonné et mtime)
    assert {k: v for k, v in after.items() if "cct-" not in k} == before


def test_dry_run_n_ecrit_rien(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    bundle = export(A, slug_a, tmp_path)
    before = tree(tmp_path / "pcB")
    res = import_bundle(bundle, B, ImportOptions("/home/b/K", dry_run=True))
    assert tree(tmp_path / "pcB") == before
    assert any(op == "créer" for op, _p, _d in res.actions)
    assert res.report.counts["projet"] > 0 and res.report.examples


def test_undo_supprime_tout(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    write(B, B.join(B.config_dir, "history.jsonl"), line({"display": "x", "project": "/p", "sessionId": "z", "timestamp": 1, "pastedContents": {}}))
    before = tree(tmp_path / "pcB")
    import_bundle(export(A, slug_a, tmp_path), B, ImportOptions("/home/b/K"))
    assert tree(tmp_path / "pcB") != before
    d, data = last_journal()
    assert undo(d, data) == []
    assert tree(tmp_path / "pcB") == before
    assert last_journal() == (None, None)


def test_mtime(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    p = A.fs(A.join(A.projects_dir, slug_a, SID + ".jsonl"))
    old = time.time() - 90 * 86400
    os.utime(p, (old, old))
    bundle = export(A, slug_a, tmp_path)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    res = import_bundle(bundle, B, ImportOptions("/home/b/K"))
    assert abs(os.path.getmtime(B.fs(res.transcript)) - time.time()) < 60
    C = make_machine(tmp_path, "pcC", "linux", "/home/c")
    res = import_bundle(bundle, C, ImportOptions("/home/c/K", keep_mtime=True))
    assert abs(os.path.getmtime(C.fs(res.transcript)) - old) < 2
    assert any("cleanupPeriodDays" in w for w in res.warnings)


# -- placement sur la cible -------------------------------------------------------------------------


def test_convention_lettre_lecteur(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    W = make_machine(tmp_path, "pcW", "windows", "C:\\Users\\alice")
    os.makedirs(W.fs(W.join(W.projects_dir, "c--Users-alice-autre")))
    res = import_bundle(export(A, slug_a, tmp_path), W, ImportOptions(ROOT_W))
    assert res.slug == "c--Users-alice-Documents-de-travail-Projets-MonProjet"
    assert users(W, res.slug)[0]["cwd"] == "c" + ROOT_W[1:]


def test_dossier_existant_par_cwd(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    other = "22222222-2222-4333-8444-555555555555"
    write(B, B.join(B.projects_dir, "nom-personnalise", other + ".jsonl"),
          line({"type": "user", "cwd": "/home/b/K", "sessionId": other, "uuid": "x", "message": {"role": "user", "content": "hi"}}))
    res = import_bundle(export(A, slug_a, tmp_path), B, ImportOptions("/home/b/K"))
    assert res.slug == "nom-personnalise"


def test_map_utilisateur(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    res = import_bundle(export(A, slug_a, tmp_path), B,
                        ImportOptions("/home/b/K", maps=[("/home/a/Projets/MonProjet2", "/srv/monprojet2")]))
    assert users(B, res.slug)[0]["message"]["content"].endswith("/srv/monprojet2/README.md")


# -- sécurité du bundle -------------------------------------------------------------------------------


def test_zip_slip_refuse(tmp_path):
    p = str(tmp_path / "evil.CCTransfert")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("manifest.json", json.dumps({"format_version": 1, "files": [{"path": "session/../../x", "sha256": "0"}]}))
        z.writestr("session/../../x", "boom")
    with pytest.raises(BundleError, match="dangereux"):
        BundleReader(p)
    p2 = str(tmp_path / "evil2.CCTransfert")
    with zipfile.ZipFile(p2, "w") as z:
        z.writestr("manifest.json", json.dumps({"format_version": 1, "files": []}))
        z.writestr("/etc/passwd", "boom")
    with pytest.raises(BundleError, match="inattendu"):
        BundleReader(p2)


def test_sha256_verifie(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    bundle = export(A, slug_a, tmp_path)
    with zipfile.ZipFile(bundle) as z:
        items = {n: z.read(n) for n in z.namelist()}
    items["session/{}.jsonl".format(SID)] += b"\n"
    bad = str(tmp_path / "bad.CCTransfert")
    with zipfile.ZipFile(bad, "w") as z:
        for n, d in items.items():
            z.writestr(n, d)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    with pytest.raises(BundleError, match="sha256"):
        import_bundle(bad, B, ImportOptions("/home/b/K"))
    assert not os.path.exists(B.fs(B.join(B.projects_dir, compute_slug("/home/b/K"))))


def test_bundle_stocke_les_fichiers_tels_quels(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    bundle = export(A, slug_a, tmp_path)
    with BundleReader(bundle) as rd:
        rd.verify()
        assert rd.read("session/{}.jsonl".format(SID)) == transcript_bytes(A, slug_a)
        kinds = {e["kind"] for e in rd.manifest["files"]}
        assert {"transcript", "subagent", "session-dir", "file-history", "plan", "memory", "history"} <= kinds
        s = rd.manifest["source"]
        assert s["project_root"] == ROOT_A and s["slug"] == slug_a and s["style"] == P.POSIX
        assert rd.manifest["session"]["title"] == "Mon titre é"


def test_relocate_meme_machine_et_undo(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    before = tree(tmp_path / "pcA")
    bundle = export(A, slug_a, tmp_path, with_memory=False, with_history=False)
    res = import_bundle(bundle, A, ImportOptions("/home/a/Nouveau/MonProjet", relocate=True, replace=True))
    assert find_session_files(A, SID) == [res.slug] and res.slug != slug_a
    assert users(A, res.slug)[0]["cwd"] == "/home/a/Nouveau/MonProjet"
    with pytest.raises(ImportError_, match="déjà"):
        import_bundle(bundle, A, ImportOptions("/home/a/Nouveau/MonProjet", relocate=True, replace=True))
    d, data = last_journal()
    undo(d, data)
    assert {k: v for k, v in tree(tmp_path / "pcA").items()} == before


def test_undo_retire_les_dossiers_vides_ajoutes_par_claude(tmp_path):
    A = make_machine(tmp_path, "pcA", "linux", "/home/a")
    slug_a = build_session(A, ROOT_A)
    B = make_machine(tmp_path, "pcB", "linux", "/home/b")
    res = import_bundle(export(A, slug_a, tmp_path, with_memory=False), B, ImportOptions("/home/b/K"))
    # Claude Code crée ces dossiers vides en reprenant la session
    os.makedirs(B.fs(B.join(B.projects_dir, res.slug, "memory")))
    os.makedirs(B.fs(B.join(B.config_dir, "session-env", SID)))
    d, data = last_journal()
    undo(d, data)
    assert not os.path.exists(B.fs(B.join(B.projects_dir, res.slug)))
    assert not os.path.exists(B.fs(B.join(B.config_dir, "session-env", SID)))
