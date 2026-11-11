"""Web music / sound effects for the editor agent: search filter, ranking, download cache, credits (no real network)."""
import json
import urllib.error

import pytest

from filmmaker import resources as r

from .test_editor import media  # noqa: F401  (fixture)


def hit(**kw):
    base = {"url": "https://cdn.example/a.mp3", "filetype": "mp3", "duration": 3000, "title": "Door", "creator": "ann",
            "license": "cc0", "foreign_landing_url": "https://example/a", "filesize": 1000}
    return {**base, **kw}


def test_search_keeps_only_usable_results_and_sends_only_the_phrase():
    seen = []

    def get(url):
        seen.append(url)
        return json.dumps({"results": [
            hit(), hit(license="by-nc", url="https://cdn.example/nc.mp3"), hit(url="http://plain.example/x.mp3"),
            hit(filetype="mid"), hit(filesize=10**9), hit(license="by", url="https://cdn.example/b.ogg", filetype=None)]}).encode()

    out = r.search("door slam", "sfx", get=get)
    assert [(h["url"], h["type"], h["license"]) for h in out] == [
        ("https://cdn.example/a.mp3", "mp3", "cc0"), ("https://cdn.example/b.ogg", "ogg", "by")]
    assert out[0]["duration"] == 3.0
    assert seen[0].startswith(r.API + "?") and "q=door+slam" in seen[0] and "length=shortest" in seen[0]
    assert "license=cc0%2Cpdm%2Cby" in seen[0]
    r.search("sad piano", "music", get=get)
    assert "length=medium%2Clong" in seen[1]


def test_rank_prefers_short_free_effects_and_music_long_enough_for_the_film():
    def h(d, lic="cc0"):
        return {"duration": d, "license": lic}
    assert [x["duration"] for x in r.rank([h(25), h(3, "by"), h(2.5), h(0.1)], "sfx")] == [2.5, 3, 25]
    assert [x["duration"] for x in r.rank([h(20), h(100, "by"), h(150), h(2000)], "music", want=140)] == [150, 100, 2000, 20]
    # Among sounds of a usable length, the one that really is what was asked for wins.
    named = [dict(h(3), title="Angry door", text="angry door impact deep bass"),
             dict(h(4), title="Bass impact 01", text="bass impact 01 boom"), dict(h(40), title="Deep bass impact", text="deep bass impact")]
    assert [x["title"] for x in r.rank(named, "sfx", query="deep bass impact")] == ["Bass impact 01", "Angry door", "Deep bass impact"]
    assert r.phrases("tense emotional piano strings") == [
        "tense emotional piano strings", "tense emotional piano", "tense emotional", "emotional piano", "piano strings"]
    assert r.phrases("door slam") == ["door slam"]


def test_library_downloads_once_keeps_credits_and_survives_bad_files(media, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.delenv("FILMMAKER_OFFLINE", raising=False)
    audio = (media / "m.mp3").read_bytes()
    calls, logs = [], []

    def get(url):
        calls.append(url)
        if url.startswith(r.API):
            return json.dumps({"results": [hit(url="https://cdn.example/broken.mp3", title="Broken"),
                                           hit(url="https://cdn.example/good.mp3", title="Good", license="by")]}).encode()
        return b"not audio" if "broken" in url else audio

    lib = r.Library(tmp_path / "assets", log=logs.append, get=get, pause=0)
    a = lib.find("door slam", "sfx")
    assert a.title == "Good" and a.license == "by" and 1.9 < a.duration < 2.2 and lib.path(a).is_file()
    assert a.file == "sfx-door-slam.mp3" and "door slam" in logs[-1]
    n = len(calls)
    assert r.Library(tmp_path / "assets", get=get, pause=0).find("door slam", "sfx").file == a.file and len(calls) == n
    credits = lib.write_credits([a, a]).read_text(encoding="utf-8")
    assert credits.count("Good") == 1 and "ann" in credits and "phải ghi tên tác giả" in credits
    assert lib.write_credits([]) is None and not (tmp_path / "assets" / "CREDITS.txt").exists()


def test_library_offline_and_network_errors_never_break_the_edit(tmp_path, monkeypatch):
    def boom(url):
        raise AssertionError("no network in offline mode")

    assert r.Library(tmp_path / "a", get=boom).find("rain", "sfx") is None  # FILMMAKER_OFFLINE=1 (conftest)
    monkeypatch.delenv("FILMMAKER_OFFLINE", raising=False)
    calls, logs = [], []

    def down(url):
        calls.append(url)
        raise urllib.error.URLError("no route")

    lib = r.Library(tmp_path / "a", log=logs.append, get=down, pause=0)
    assert lib.find("rain", "sfx") is None and lib.find("wind", "sfx") is None
    assert len(calls) == 1 and "Không tìm được" in logs[0]  # asked once, then left alone for this edit
