"""Parcours complet de la CLI sur une fausse config « locale » (HOME et CLAUDE_CONFIG_DIR redirigés)."""

import os

from conftest import SID, build_session
from cctransfert.cli import main
from cctransfert.profile import Profile, host_os
from cctransfert.store import find_session_files, list_sessions


def test_cli_parcours_complet(tmp_path, monkeypatch, capsys):
    tmp_path = type(tmp_path)(os.path.realpath(str(tmp_path)))  # Windows : pas de noms courts RUNNER~1
    home = tmp_path / "home"
    cfg = home / ".claude"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CLAUDE_CODE_PROJECT_DIR_NAME", raising=False)
    prof = Profile(host_os(), str(home), str(cfg), "/tmp/claude-test", hostname="h", user="u")
    root = str(home / "Projets" / "MonProjet")
    os.makedirs(root)
    slug = build_session(prof, root)
    monkeypatch.chdir(root)

    assert main(["list"]) == 0
    assert "Mon titre é" in capsys.readouterr().out
    bundle = str(tmp_path / "b.CCTransfert")
    assert main(["export", "--latest", "-o", bundle, "-y", "--with-memory"]) == 0
    assert "Bundle créé" in capsys.readouterr().out
    assert main(["inspect", bundle, "--files"]) == 0
    assert "sha256 vérifiés" in capsys.readouterr().out

    target = str(home / "Autre machine" / "MonProjet")
    assert main(["import", bundle, "--to", target, "--new-id", "-y", "--dry-run"]) == 0
    assert "rien n'a été écrit" in capsys.readouterr().out
    assert not os.path.exists(target)
    assert main(["import", bundle, "--to", target, "--new-id", "-y"]) == 0
    out = capsys.readouterr().out
    assert "Session importée" in out and "claude -r" in out
    assert main(["undo", "-y"]) == 0
    assert find_session_files(prof, SID) == [slug]

    # même ID dans un autre dossier sans option : refus explicite
    assert main(["import", bundle, "--to", target, "-y"]) == 1
    assert "--new-id" in capsys.readouterr().err

    assert main(["relocate", SID[:8], "--to", target, "-y"]) == 0
    assert find_session_files(prof, SID) != [slug]
    assert main(["undo", "-y"]) == 0
    assert find_session_files(prof, SID) == [slug]

    # duplication dans le même dossier, avec un intitulé
    assert main(["duplicate", SID[:8], "--title", "Essai copie", "-y"]) == 0
    assert "Copie créée" in capsys.readouterr().out
    assert len(list_sessions(prof)) == 2 and any(x.title == "Essai copie" for x in list_sessions(prof))
    assert main(["undo", "-y"]) == 0
    assert len(list_sessions(prof)) == 1

    assert main(["doctor", "--json"]) == 0
    assert '"slugs_conformes": 1' in capsys.readouterr().out
