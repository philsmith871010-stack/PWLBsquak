from pwlbsquawk import dedupe
from tests.conftest import make_event


def test_normalise_strips_urls_handles_and_punctuation():
    t = "BREAKING: UK CPI 3.4% in Aug, via @bruceandy.bsky.social https://reut.rs/abc — more soon!"
    assert dedupe.normalise(t) == "breaking uk cpi 3 4% in aug via more soon"


def test_exact_normalised_match_wins():
    a = "Bank of England holds Bank Rate at 4%"
    b = "bank of england holds bank rate at 4%!!"
    dup = dedupe.find_duplicate(dedupe.normalise(b), [("e1", dedupe.normalise(a))])
    assert dup == ("e1", 1.0)


def test_near_duplicate_detected_across_accounts():
    reuters = (
        "Bank of England holds Bank Rate at 4%, votes 7-2 to keep policy unchanged, signals gradual path"
    )
    ft = "Bank of England holds Bank Rate at 4% and votes 7-2 to keep policy unchanged, signals gradual path ahead"
    dup = dedupe.find_duplicate(dedupe.normalise(ft), [("e1", dedupe.normalise(reuters))])
    assert dup is not None and dup[0] == "e1" and dup[1] >= 0.6


def test_heavy_rewording_is_not_a_duplicate():
    # Different framings of the same story are left to the writer to merge.
    reuters = (
        "Bank of England holds Bank Rate at 4%, votes 7-2 to keep policy unchanged, signals gradual path"
    )
    ft = "BoE keeps rates on hold; two dissenters wanted a cut and Bailey stresses caution"
    assert dedupe.find_duplicate(dedupe.normalise(ft), [("e1", dedupe.normalise(reuters))]) is None


def test_different_stories_not_duplicates():
    a = "UK CPI inflation rises to 3.8% in August, above the 3.6% consensus, driven by food and airfares"
    b = "Gilt yields fall after weak retail sales data; 10-year down 6bp to 4.55%"
    dup = dedupe.find_duplicate(dedupe.normalise(b), [("e1", dedupe.normalise(a))])
    assert dup is None


def test_short_texts_only_match_exactly():
    a = "Big move in gilts"
    b = "Big move in gilts today"
    assert dedupe.find_duplicate(dedupe.normalise(b), [("e1", dedupe.normalise(a))]) is None
    assert dedupe.find_duplicate(dedupe.normalise(a), [("e1", dedupe.normalise(a))]) == ("e1", 1.0)


def test_store_marks_near_duplicate_and_writer_only_sees_canonical(store):
    e1 = make_event(
        "Bank of England holds Bank Rate at 4%, votes 7-2 to keep policy unchanged, signals gradual path",
        author="reuters.com",
        rkey="r1",
        did="did:plc:reuters",
    )
    e2 = make_event(
        "Bank of England holds Bank Rate at 4% and votes 7-2 to keep policy unchanged, signals gradual path",
        author="financialtimes.com",
        rkey="f1",
        did="did:plc:ft",
        ts="2026-09-28T09:03:00Z",
    )
    e3 = make_event(
        "Gilt yields fall after weak retail sales data; 10-year down 6bp to 4.55%",
        author="iankmsmith.ft.com",
        rkey="i1",
        did="did:plc:ian",
        ts="2026-09-28T09:05:00Z",
    )
    r1 = store.insert_event(e1)
    r2 = store.insert_event(e2)
    r3 = store.insert_event(e3)
    assert r1.status == "inserted"
    assert r2.status == "near_duplicate" and r2.dedupe_of == e1.id and r2.score >= 0.6
    assert r3.status == "inserted"
    # every raw item is still in the log
    assert store.count_events() == 3
    pending = store.unprocessed_events()
    assert [e.id for e in pending] == [e1.id, e3.id]


def test_store_ignores_exact_uri_duplicate(store):
    e = make_event("Same post delivered twice")
    assert store.insert_event(e).status == "inserted"
    assert store.insert_event(e).status == "duplicate_uri"
    assert store.count_events() == 1


def test_dedupe_window_respected(store):
    e1 = make_event(
        "Chancellor confirms Budget date of 26 November in a letter to the OBR",
        rkey="a",
        ts="2026-09-28T09:00:00Z",
    )
    e2 = make_event(
        "Chancellor confirms Budget date of 26 November in a letter to the OBR",
        rkey="b",
        ts="2026-09-29T09:00:00Z",
    )  # 24h later: same text, but outside the window
    store.insert_event(e1)
    assert store.insert_event(e2, dedupe_window_hours=6).status == "inserted"
