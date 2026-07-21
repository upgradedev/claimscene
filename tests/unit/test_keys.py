from claimscene.keys import KeyStrategy, make_key, safe_component


def test_hierarchical_layout():
    key = make_key(KeyStrategy.HIERARCHICAL, case="demo", kind="schematic",
                   sha256="ab" * 32, name="schematic.svg")
    assert key == f"demo/schematic/ab/{'ab' * 32}/schematic.svg"


def test_flat_layout():
    key = make_key(KeyStrategy.FLAT, case="demo", kind="scene",
                   sha256="cd" * 32, name="scene.json")
    assert key == f"{'cd' * 32}-scene.json"


def test_traversal_neutralised():
    assert safe_component("../../etc/passwd") == "passwd"
    assert safe_component("..") == "asset"
    key = make_key(KeyStrategy.HIERARCHICAL, case="../../evil", kind="inputs",
                   sha256="ef" * 32, name="..\\..\\boot.ini")
    segments = key.split("/")
    assert ".." not in segments
    assert not any(s.startswith(".") for s in segments)


def test_unsafe_characters_collapsed():
    assert safe_component("a b\nc\x00d") == "a_b_c_d"
    assert "/" not in safe_component("x/y/z") and "\\" not in safe_component("x\\y")


def test_sha_anchor_always_present():
    key = make_key(KeyStrategy.HIERARCHICAL, case="c", kind="k",
                   sha256="12" * 32, name="n.bin")
    assert any(len(part) == 64 for part in key.split("/"))
