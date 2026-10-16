from cctransfert import paths as P
from cctransfert.slug import compute_slug, drive_case_of_slug, java_hash, sanitize, slug_matches, valid_project_dir_name


def test_slug_exemples_observes():
    # Slugs relevés sur une vraie machine (Claude Code 2.1.301), voir docs/FORMAT.md
    assert compute_slug("/home/alice/Projets/MonProjet") == "-home-alice-Projets-MonProjet"
    assert compute_slug("/home/alice/Téléchargements/image") == "-home-alice-T-l-chargements-image"
    assert compute_slug("/tmp/CCTransfert test/Dossier.été_v2") == "-tmp-CCTransfert-test-Dossier--t--v2"


def test_slug_unites_utf16():
    # emoji = 2 unités UTF-16 -> 2 tirets ; é décomposé (NFD) -> "e-"
    assert compute_slug("/tmp/CCTransfert test/nfd_e\u0301_emoji_\U0001F600") == "-tmp-CCTransfert-test-nfd-e--emoji---"
    assert sanitize("\U0001F600") == "--"


def test_slug_long_hash_verifie_empiriquement():
    p = "/tmp/CCTransfert test/" + "/".join("segment_tres_long_numero_%02d_pour_depasser" % i for i in range(6))
    s = compute_slug(p)
    assert s.endswith("-erxhkv")  # suffixe observé avec claude -p
    assert len(s) == 200 + 1 + len("erxhkv")


def test_java_hash():
    assert java_hash("") == 0
    assert java_hash("a") == 97
    assert java_hash("hello") == 99162322  # valeur de String.hashCode en Java


def test_slug_windows():
    assert compute_slug("C:\\Users\\x\\Documents de travail\\MonProjet") == "C--Users-x-Documents-de-travail-MonProjet"
    assert drive_case_of_slug("C--Users-x") == "upper"
    assert drive_case_of_slug("c--Users-x") == "lower"
    assert drive_case_of_slug("-home-x") is None
    assert slug_matches("c--Users-x-K", "C:\\Users\\x\\K", case_insensitive=True)
    assert not slug_matches("c--Users-x-K", "C:\\Users\\x\\K", case_insensitive=False)


def test_project_dir_name():
    assert valid_project_dir_name("mon-projet_1")
    assert not valid_project_dir_name("con")
    assert not valid_project_dir_name("a/b")
    assert not valid_project_dir_name("x" * 65)


def test_paths_styles():
    assert P.style_of("C:\\a") == P.WINDOWS
    assert P.style_of("c:/a") == P.WINDOWS
    assert P.style_of("/home/a") == P.POSIX
    assert P.normalize("C:/Users/b/", P.WINDOWS) == "C:\\Users\\b"
    assert P.normalize("/home/a/", P.POSIX) == "/home/a"
    assert P.same_path("C:\\Users\\B", "c:/users/b", P.WINDOWS)
    assert not P.same_path("/home/B", "/home/b", P.POSIX)
    assert P.is_under("C:\\a\\b\\c", "c:\\A\\b", P.WINDOWS)
    assert not P.is_under("/home/a/MonProjet2", "/home/a/MonProjet", P.POSIX)
    assert P.with_drive_case("C:\\x", "lower") == "c:\\x"
