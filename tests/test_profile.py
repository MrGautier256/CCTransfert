"""Détection de Claude Code hors du PATH (cas d'un lancement depuis une icône de bureau)."""

import json
import os
import sys

import pytest

from cctransfert import profile as prof

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="scripts shell")


def _script(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, 0o755)


def test_trouve_claude_hors_du_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    _script(str(tmp_path / ".local" / "bin" / "claude"), 'echo "9.8.7 (Claude Code)"')
    exe, where = prof.find_claude()
    assert exe == str(tmp_path / ".local" / "bin" / "claude") and "hors du PATH" in where
    assert prof.claude_version() == "9.8.7"


def test_version_depuis_la_cible_du_lien(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    target = tmp_path / ".local" / "share" / "claude" / "versions" / "2.3.4"
    _script(str(target), "exit 1")  # --version échoue
    os.makedirs(tmp_path / ".local" / "bin")
    os.symlink(str(target), str(tmp_path / ".local" / "bin" / "claude"))
    assert prof.claude_version() == "2.3.4"


def test_version_depuis_les_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/nulle-part")
    cfg = tmp_path / "cfg"
    os.makedirs(cfg / "projects" / "p")
    with open(cfg / "projects" / "p" / "11111111-2222-4333-8444-555555555555.jsonl", "w") as f:
        f.write(json.dumps({"type": "user", "version": "2.1.290"}) + "\n" + json.dumps({"type": "assistant", "version": "2.1.301"}) + "\n")
    info = prof.claude_info(str(cfg))
    assert info == {"exe": None, "exe_source": None, "version": "2.1.301", "version_source": "sessions"}
    os.makedirs(cfg / "sessions")
    with open(cfg / "sessions" / "123.json", "w") as f:
        json.dump({"pid": 123, "version": "2.2.0"}, f)
    assert prof.claude_version_from_sessions(str(cfg)) == "2.2.0"
