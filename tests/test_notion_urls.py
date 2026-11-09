from poda_app.connectors.notion.urls import parse_notion_reference, legacy_last_match_id, primary_candidates, to_dashed

DB = "aaaaaaaabbbb4ccc8dddeeeeeeeeeeee"
DB_DASHED = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
VIEW = "9f1e2d3c4b5a69788796a5b4c3d2e1f0"
BLOCK = "0123456789abcdef0123456789abcdef"


def test_path_id_and_view_id_are_separated():
    url = f"https://www.notion.so/myws/To-Do-List-{DB}?v={VIEW}"
    parsed = parse_notion_reference(url)
    kinds = {c["kind"]: c["id"] for c in parsed["candidates"]}
    assert kinds["path_id"] == DB_DASHED
    assert kinds["view_id"] == to_dashed(VIEW)
    assert any("?v=" in w for w in parsed["warnings"])


def test_legacy_regex_picked_the_view_id_but_new_parser_picks_path_id():
    url = f"https://www.notion.so/ws/{DB}?v={VIEW}"
    assert legacy_last_match_id(url) == to_dashed(VIEW)  # the v0.3.11 failure mode
    assert primary_candidates(parse_notion_reference(url))[0]["id"] == DB_DASHED


def test_block_fragment_detected():
    parsed = parse_notion_reference(f"https://notion.so/ws/{DB}#{BLOCK}")
    assert {c["kind"] for c in parsed["candidates"]} == {"path_id", "block_fragment"}


def test_bare_id_is_a_raw_candidate_never_assumed_valid():
    parsed = parse_notion_reference(DB_DASHED)
    assert parsed["candidates"] == [{"id": DB_DASHED, "kind": "raw_id", "position": "text", "note": parsed["candidates"][0]["note"]}]
    assert "unknown until probed" in parsed["candidates"][0]["note"]


def test_explicit_data_source_prefix():
    parsed = parse_notion_reference(f"ds:{DB}")
    assert parsed["candidates"][0]["kind"] == "data_source_id"


def test_nested_path_parent_is_not_primary():
    parent = "aaaaaaaabbbbccccddddeeeeeeeeeeee"
    parsed = parse_notion_reference(f"https://www.notion.so/ws/Parent-{parent}/Child-{DB}")
    prim = primary_candidates(parsed)
    assert prim[0]["id"] == DB_DASHED and prim[0]["kind"] == "path_id"
    assert any(c["kind"] == "path_parent_id" for c in parsed["candidates"])


def test_garbage_input():
    parsed = parse_notion_reference("hello world")
    assert parsed["candidates"] == [] and parsed["warnings"]
