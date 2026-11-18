"""Quick EN -> VI machine translation (no AI): online path mocked, offline glossary fallback real."""
from filmmaker import translate as tr


def test_online_path_used_when_endpoint_answers(monkeypatch):
    seen = []

    def fake_google(text, source, target, timeout):
        seen.append((text, source, target))
        return "[dịch] " + text

    monkeypatch.setattr(tr, "_google_translate", fake_google)
    out = tr.translate_texts(["A woman walks", "The city at night"], target="vi", source="auto")
    assert out["engine"] == "google"
    assert [t["text"] for t in out["translations"]] == ["[dịch] A woman walks", "[dịch] The city at night"]
    assert all(t["engine"] == "google" for t in out["translations"])
    assert seen == [("A woman walks", "auto", "vi"), ("The city at night", "auto", "vi")]


def test_falls_back_to_glossary_when_offline(monkeypatch):
    monkeypatch.setattr(tr, "_google_translate", lambda *a, **k: None)  # simulate no network
    out = tr.translate_texts(["The woman walks in the city"], target="vi")
    assert out["engine"] == "glossary"
    vi = out["translations"][0]["text"]
    # known glossary words are swapped; unknown words are left alone
    assert "người phụ nữ" in vi and "thành phố" in vi
    assert "woman" not in vi.lower() and "city" not in vi.lower()


def test_mixed_engine_when_some_fail(monkeypatch):
    def flaky(text, source, target, timeout):
        return "ok" if "online" in text else None

    monkeypatch.setattr(tr, "_google_translate", flaky)
    out = tr.translate_texts(["online text", "offline man"])
    assert out["engine"] == "mixed"
    assert out["translations"][0]["engine"] == "google"
    assert out["translations"][1]["engine"] == "glossary"


def test_blank_texts_are_passed_through():
    out = tr.translate_texts(["", "   "])
    assert out["engine"] == "none"
    assert [t["text"] for t in out["translations"]] == ["", ""]


def test_glossary_is_case_insensitive_and_keeps_names(monkeypatch):
    monkeypatch.setattr(tr, "_google_translate", lambda *a, **k: None)
    out = tr.translate_texts(["Lan SMILES at the SEA"])
    vi = out["translations"][0]["text"].lower()
    assert "lan" in vi  # proper noun left untouched
    assert "mỉm cười" in vi and "biển" in vi


def test_glossary_multiword_phrase_not_recut(monkeypatch):
    # "Wide shot" -> "toàn cảnh" must stay intact: the single-word pass must not carve
    # "to" out of "toàn" (which would wrongly become "đến" -> "đếnàn cảnh").
    monkeypatch.setattr(tr, "_google_translate", lambda *a, **k: None)
    out = tr.translate_texts(["Wide shot of the city"])
    vi = out["translations"][0]["text"]
    assert "toàn cảnh" in vi
    assert "đếnàn" not in vi and "đến" not in vi


def test_glossary_leaves_existing_vietnamese_untouched(monkeypatch):
    # Default app content is often already Vietnamese; the offline swap must not eat the
    # trailing letters of accented words ("chưa" -> "chư", "của" -> "củ") nor delete the name "An".
    monkeypatch.setattr(tr, "_google_translate", lambda *a, **k: None)
    src = "An chưa về nhà của mình giữa trưa mưa"
    out = tr.translate_texts([src])
    vi = out["translations"][0]["text"]
    for word in ["An", "chưa", "của", "giữa", "trưa", "mưa"]:
        assert word in vi, f"{word!r} corrupted in {vi!r}"


def test_short_circuits_after_endpoint_fails(monkeypatch):
    # Once the online endpoint fails to answer one item, the rest must skip the network
    # (no stacked timeouts) and fall straight to the offline glossary.
    calls = []

    def one_shot(text, source, target, timeout):
        calls.append(text)
        return None  # endpoint down

    monkeypatch.setattr(tr, "_google_translate", one_shot)
    out = tr.translate_texts(["a woman", "the city", "the sea"])
    assert len(calls) == 1  # only probed once, then gave up on the network
    assert out["engine"] == "glossary"
    assert all(t["engine"] == "glossary" for t in out["translations"])


def test_split_chunks_keeps_text_and_respects_limit():
    text = ("A sentence about the scene. " * 60 + "\n") * 5
    pieces = tr._split_chunks(text, 500)
    assert "".join(pieces) == text
    assert all(len(p) <= 500 for p in pieces)
    assert len(pieces) > 1


def test_long_prompt_is_translated_in_chunks(monkeypatch):
    calls = []

    def fake(text, source, target, timeout):
        calls.append(text)
        assert len(text) <= tr.CHUNK_CHARS
        return "VI(" + str(len(calls)) + ")"

    monkeypatch.setattr(tr, "_google_translate", fake)
    prompt = ("Close-up of the woman in the kitchen. " * 30 + "\n") * 4  # ~4.5k chars, like a full Flow prompt
    res = tr.translate_texts([prompt])
    assert res["engine"] == "google"
    assert len(calls) > 1
    out = res["translations"][0]["text"]
    assert all(f"VI({n})" in out for n in range(1, len(calls) + 1))


def test_long_prompt_falls_back_when_a_chunk_fails(monkeypatch):
    seen = {"n": 0}

    def flaky(text, source, target, timeout):
        seen["n"] += 1
        return None if seen["n"] == 2 else "ok"

    monkeypatch.setattr(tr, "_google_translate", flaky)
    prompt = "The woman walks. " * 200
    res = tr.translate_texts([prompt])
    assert res["engine"] == "glossary"
    assert "người phụ nữ" in res["translations"][0]["text"]
