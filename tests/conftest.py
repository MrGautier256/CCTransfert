"""Fixtures : fausses machines (Linux ou Windows simulé) avec un dossier de config Claude Code.

Les chemins *logiques* sont ceux de la machine simulée (`C:\\Users\\b\\…` pour Windows) ;
`Profile.fs()` les traduit vers un dossier temporaire du disque de test.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cctransfert import paths as P  # noqa: E402
from cctransfert.jsonio import dumps_js  # noqa: E402
from cctransfert.profile import Profile  # noqa: E402
from cctransfert.slug import compute_slug  # noqa: E402

SID = "11111111-2222-4333-8444-555555555555"
THINKING_TEXT = "Je dois lire /home/a/Projets/MonProjet/src/a.ts puis C:\\x. Rien à changer ici."
SIGNATURE = "EqQBCkgIBRABGAIiQL0/home/a/Projets/MonProjet+signature+opaque=="


@pytest.fixture(autouse=True)
def tool_home(tmp_path, monkeypatch):
    d = tmp_path / "cct-home"
    monkeypatch.setenv("CCTRANSFERT_HOME", str(d))
    return d


def make_machine(tmp_path, name, os_name, home, temp_root=None):
    """Profil d'une machine simulée dont le disque est sous `tmp_path/name`."""
    base = tmp_path / name
    style = P.WINDOWS if os_name == "windows" else P.POSIX

    def fs_map(p):
        if style == P.WINDOWS:
            q = p.replace("\\", "/")
            return str(base / q[0].upper() / q[3:]) if len(q) > 3 else str(base / q[0].upper())
        return str(base) + p

    if temp_root is None:
        temp_root = (home + "\\AppData\\Local\\Temp\\claude-0") if style == P.WINDOWS else "/tmp/claude-1000"
    prof = Profile(os_name, home, P.join(style, home, ".claude"), temp_root, hostname=name,
                   user=P.basename(home, style), fs_map=fs_map)
    os.makedirs(prof.fs(prof.projects_dir), exist_ok=True)
    return prof


def write(prof, logical, data):
    p = prof.fs(logical)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(p, mode, **({} if mode == "wb" else {"encoding": "utf-8", "newline": "\n"})) as f:
        f.write(data)
    return p


def line(obj):
    return dumps_js(obj) + "\n"


def build_session(prof, root, sid=SID, with_extras=True):
    """Crée une session réaliste dans la machine `prof`, lancée dans `root`. Renvoie le slug."""
    j = prof.join
    slug = compute_slug(root)
    cfg = prof.config_dir
    tmp_proj = j(prof.temp_root, slug, sid)
    tool_res = j(cfg, "projects", slug, sid, "tool-results", "b1.txt")
    sep = "\\" if prof.style == P.WINDOWS else "/"
    src_file = j(root, "src", "a.ts")
    other = root + "2" + sep + "README.md"  # collision de préfixe MonProjet / MonProjet2
    base = {"cwd": root, "sessionId": sid, "version": "2.1.301", "gitBranch": "main",
            "userType": "external", "entrypoint": "cli", "isSidechain": False}
    L = []
    L.append(line({"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-10-01T10:00:00.000Z",
                   "sessionId": sid, "content": "regarde " + root}))
    L.append(line(dict(base, type="user", uuid="u1", parentUuid=None, timestamp="2026-10-01T10:00:01.000Z",
                       promptId="p1", message={"role": "user", "content": "Travaille dans {} et compare avec {}".format(root, other)})))
    L.append(line(dict(base, type="assistant", uuid="a1", parentUuid="u1", timestamp="2026-10-01T10:00:02.000Z",
                       requestId="req_1", message={
                           "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-x",
                           "content": [
                               {"type": "thinking", "thinking": THINKING_TEXT, "signature": SIGNATURE},
                               {"type": "text", "text": "Je lis {}.".format(src_file)},
                               {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": src_file}},
                               {"type": "tool_use", "id": "toolu_2", "name": "Bash",
                                "input": {"command": "cd {} && ls {}".format(root, j(root, "src")), "description": "liste"}},
                           ]},
                       fooFutur={"chemin": j(root, "x"), "nombres": [1.0, 1e21, 12345678901234567890]})))
    L.append(line(dict(base, type="user", uuid="u2", parentUuid="a1", timestamp="2026-10-01T10:00:03.000Z",
                       sourceToolAssistantUUID="a1",
                       message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                             "content": "contenu de " + src_file}]},
                       toolUseResult={"type": "text", "file": {"filePath": src_file, "content": "x"},
                                      "persistedOutputPath": tool_res})))
    L.append(line({"type": "file-history-snapshot", "messageId": "u2", "isSnapshotUpdate": False, "snapshot": {
        "messageId": "u2", "timestamp": "2026-10-01T10:00:03.000Z",
        "trackedFileBackups": {
            "src" + sep + "a.ts": {"backupFileName": "abcd@v1", "version": 1, "backupTime": "2026-10-01T10:00:03.000Z",
                                   "realParentDir": j(root, "src")},
            j(tmp_proj, "scratchpad", "p.php"): {"backupFileName": "ef01@v1", "version": 1,
                                                 "backupTime": "2026-10-01T10:00:03.000Z",
                                                 "realParentDir": j(tmp_proj, "scratchpad")},
        }}}))
    L.append(line(dict(base, type="user", uuid="u3", parentUuid="u2", timestamp="2026-10-01T10:00:04.000Z",
                       message={"role": "user", "content": [
                           {"type": "text", "text": "voici une image"},
                           {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                        "data": "iVBOR" + root.replace("\\", "/").replace(":", "") + "AAAA"}}]})))
    L.append(line({"type": "api-request-blob", "sessionId": sid, "hash": "deadbeef",
                   "message": {"role": "user", "content": [{"type": "text", "text": "cwd " + root}]}}))
    L.append(line({"type": "type-du-futur", "sessionId": sid, "inconnu": {j(root, "cle"): ["v", 2.50]}}))
    L.append(line(dict(base, type="assistant", uuid="a2", parentUuid="u3", timestamp="2026-10-01T10:05:00.000Z",
                       slug="happy-cat-dog",
                       message={"id": "msg_2", "type": "message", "role": "assistant",
                                "content": [{"type": "text", "text": "Fini. Voir {}.".format(j(root, "src"))}]})))
    raw = "".join(L).encode("utf-8")
    raw += b'\x00\x00\x00{"type":"last-prompt","lastPrompt":"' + root.replace("\\", "\\\\").encode() + b'"}\n'  # corrompue
    raw += line({"type": "custom-title", "customTitle": "Mon titre é", "sessionId": sid}).encode("utf-8")
    raw += line({"type": "last-prompt", "lastPrompt": "dernier " + root, "leafUuid": "a2", "sessionId": sid}).encode("utf-8")
    write(prof, j(cfg, "projects", slug, sid + ".jsonl"), raw)

    if with_extras:
        sub = j(cfg, "projects", slug, sid, "subagents")
        write(prof, j(sub, "agent-abc.jsonl"), line(dict(base, type="user", uuid="s1", parentUuid=None, isSidechain=True,
                                                         agentId="abc", timestamp="2026-10-01T10:01:00.000Z",
                                                         message={"role": "user", "content": "explore " + root})))
        write(prof, j(sub, "agent-abc.meta.json"), dumps_js({"agentType": "Explore", "description": "x"}))
        write(prof, tool_res, "sortie longue avec {}\n".format(src_file))
        write(prof, j(cfg, "file-history", sid, "abcd@v1"), "contenu original avec " + root + "\n")
        write(prof, j(cfg, "plans", "happy-cat-dog.md"), "# Plan\nModifier {}\n".format(src_file))
        write(prof, j(cfg, "projects", slug, "memory", "MEMORY.md"), "- projet dans {}\n".format(root))
        write(prof, j(cfg, "history.jsonl"),
              line({"display": "autre", "pastedContents": {}, "timestamp": 1, "project": "/ailleurs", "sessionId": "x"})
              + line({"display": "Travaille dans " + root, "pastedContents": {}, "timestamp": 2, "project": root, "sessionId": sid}))
    return slug


def session_info(prof, slug, sid=SID):
    from cctransfert.store import summarize

    return summarize(prof, slug, sid)


def read_lines(prof, logical):
    with open(prof.fs(logical), "rb") as f:
        return f.read().split(b"\n")


def parse_jsonl(prof, logical):
    out = []
    for raw in read_lines(prof, logical):
        if raw.strip():
            try:
                out.append(json.loads(raw.decode("utf-8")))
            except ValueError:
                out.append(None)
    return out
