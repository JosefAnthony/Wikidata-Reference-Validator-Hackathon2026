from app import build_search_url, get_search_state_from_querystring


def test_build_search_url_includes_qid_and_label():
    url = build_search_url(qid="Q937", label="Albert Einstein", base_path="/")
    assert url == "/?qid=Q937&label=Albert+Einstein"


def test_get_search_state_from_querystring_reads_values():
    state = get_search_state_from_querystring("?qid=Q937&label=Albert+Einstein")
    assert state == {"qid": "Q937", "label": "Albert Einstein"}


def test_get_search_state_from_querystring_handles_missing_values():
    state = get_search_state_from_querystring("")
    assert state == {"qid": "", "label": ""}
