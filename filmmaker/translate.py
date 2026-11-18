"""Quick machine translation for the UI's "Dịch sang tiếng Việt" buttons.

No AI / LLM is involved and this never touches the Flow / Gemini credits the rest of
the app spends. A scene's text is translated with Google's free, key-less translate
endpoint over plain HTTP — the same statistical/NMT service behind translate.google.com,
not a language model. When the machine is offline (or the endpoint is unreachable) we
fall back to a small built-in glossary so the button still returns something useful
instead of failing outright.

The public entry point is :func:`translate_texts`; the HTTP call lives in
:func:`_google_translate` so tests can stub it out and run without a network.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
_UA = "Mozilla/5.0 (Film Maker local UI)"

# A tiny offline EN -> VI glossary, used only when the online endpoint is unreachable.
# It is deliberately small and focused on the words a film prompt tends to use; it is a
# best-effort word swap, never a real translation, and the UI labels it as such.
GLOSSARY: dict[str, str] = {
    # people / subjects
    "woman": "người phụ nữ", "man": "người đàn ông", "girl": "cô gái", "boy": "cậu bé",
    "child": "đứa trẻ", "children": "những đứa trẻ", "old": "già", "young": "trẻ",
    "people": "mọi người", "crowd": "đám đông", "family": "gia đình", "friend": "bạn",
    "mother": "người mẹ", "father": "người cha", "sister": "chị em gái", "brother": "anh em trai",
    # places / settings
    "city": "thành phố", "street": "con phố", "road": "con đường", "house": "ngôi nhà",
    "home": "nhà", "room": "căn phòng", "kitchen": "nhà bếp", "forest": "khu rừng",
    "mountain": "ngọn núi", "beach": "bãi biển", "sea": "biển", "river": "dòng sông",
    "sky": "bầu trời", "field": "cánh đồng", "village": "ngôi làng", "office": "văn phòng",
    "garden": "khu vườn", "bridge": "cây cầu", "market": "khu chợ",
    # time / light
    "morning": "buổi sáng", "noon": "buổi trưa", "afternoon": "buổi chiều",
    "evening": "buổi tối", "night": "ban đêm", "day": "ban ngày", "sunset": "hoàng hôn",
    "sunrise": "bình minh", "sunlight": "ánh nắng", "light": "ánh sáng", "shadow": "bóng tối",
    "dark": "tối", "bright": "sáng", "rain": "mưa", "snow": "tuyết", "wind": "gió",
    "fog": "sương mù", "warm": "ấm áp", "cold": "lạnh",
    # camera / film terms
    "close-up": "cận cảnh", "close up": "cận cảnh", "wide shot": "toàn cảnh",
    "wide": "toàn cảnh", "medium shot": "trung cảnh", "low angle": "góc thấp",
    "high angle": "góc cao", "slow motion": "quay chậm", "camera": "máy quay",
    "shot": "cảnh quay", "scene": "cảnh", "background": "hậu cảnh", "foreground": "tiền cảnh",
    "lens": "ống kính", "focus": "lấy nét", "golden hour": "giờ vàng",
    "dolly in": "đẩy máy vào", "dolly-in": "đẩy máy vào", "pan": "lia máy",
    # actions
    "walks": "bước đi", "walking": "đang đi", "walk": "đi", "runs": "chạy",
    "running": "đang chạy", "run": "chạy", "smiles": "mỉm cười", "smiling": "đang mỉm cười",
    "smile": "mỉm cười", "cries": "khóc", "crying": "đang khóc", "looks": "nhìn",
    "looking": "đang nhìn", "look": "nhìn", "stands": "đứng", "standing": "đang đứng",
    "sits": "ngồi", "sitting": "đang ngồi", "turns": "quay lại", "holds": "cầm",
    "holding": "đang cầm", "opens": "mở", "closes": "đóng", "speaks": "nói",
    "says": "nói", "talking": "đang nói", "waits": "chờ", "waiting": "đang chờ",
    # feelings / description
    "happy": "vui vẻ", "sad": "buồn", "angry": "tức giận", "afraid": "sợ hãi",
    "scared": "sợ hãi", "tired": "mệt mỏi", "quiet": "yên tĩnh", "silent": "im lặng",
    "beautiful": "xinh đẹp", "small": "nhỏ", "big": "lớn", "empty": "trống trải",
    "alone": "một mình", "slowly": "chậm rãi", "quickly": "nhanh chóng",
    "gently": "nhẹ nhàng", "suddenly": "bỗng nhiên", "softly": "khẽ khàng",
    # connective words
    "and": "và", "the": "", "a": "", "an": "", "in": "trong", "on": "trên",
    "at": "tại", "with": "với", "of": "của", "to": "đến", "into": "vào",
    "her": "cô ấy", "his": "anh ấy", "their": "của họ", "she": "cô ấy", "he": "anh ấy",
    "they": "họ", "as": "khi", "while": "trong khi", "then": "sau đó", "from": "từ",
}

# Longest phrases first so multi-word entries ("close-up") win over single words.
_GLOSSARY_KEYS = sorted(GLOSSARY, key=len, reverse=True)


def _google_translate(text: str, source: str, target: str, timeout: float) -> str | None:
    """Translate one string via Google's free endpoint. Returns None on any failure."""
    params = urllib.parse.urlencode(
        {"client": "gtx", "sl": source or "auto", "tl": target, "dt": "t", "q": text}
    )
    req = urllib.request.Request(f"{GOOGLE_URL}?{params}", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — offline, HTTP error, bad JSON: fall back to the glossary
        return None
    try:
        chunks = [seg[0] for seg in data[0] if seg and seg[0]]
    except (IndexError, TypeError):
        return None
    out = "".join(chunks).strip()
    return out or None


# The free endpoint is a GET, so the text rides in the URL. Full Flow prompts run to a few
# thousand characters (and Thai / Vietnamese percent-encode to ~9 bytes a letter), which can
# overflow the URL limit — longer texts are cut into pieces at line / sentence boundaries.
CHUNK_CHARS = 1200
MAX_PARALLEL = 6  # concurrent requests to the free endpoint for one call


def _split_chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Cut ``text`` into pieces of at most ``limit`` characters; ``"".join(pieces) == text``.

    Cuts after a newline when possible, else after a sentence end / space, else hard."""
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n") + 1
        if cut <= 0:
            m = None
            for m in re.finditer(r"[.!?;。]\s+", window):
                pass
            cut = m.end() if m else 0
        if cut <= 0:
            cut = window.rfind(" ") + 1
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


def _join_pieces(pieces: list[str], done: list[str]) -> str:
    """Glue translated chunks back, keeping the whitespace (line breaks) between them."""
    out: list[str] = []
    for piece, got in zip(pieces, done):
        if not piece.strip():
            out.append(piece)
            continue
        lead = piece[: len(piece) - len(piece.lstrip())]
        trail = piece[len(piece.rstrip()):]
        out.append(lead + got + (trail if "\n" in trail else " "))
    return "".join(out).strip()


def _glossary_translate(text: str) -> str:
    """Offline word-by-word swap. Crude by design; only used when the network fails.

    Only standalone ASCII (English) words are swapped. Unicode word boundaries keep the
    pass from biting into Vietnamese text — whether it is the Vietnamese we just injected
    for a multi-word phrase (``"Wide shot"`` -> ``"toàn cảnh"``) or Vietnamese the user
    already wrote (``"chưa"``, ``"của"``, ``"mưa"``): ``à``, ``ư``, ``ủ``… count as word
    characters, so ``"to"`` is never carved out of ``"toàn"``.
    """

    def swap(match: re.Match[str]) -> str:
        word = match.group(0)
        hit = GLOSSARY.get(word.lower())
        if hit is None:
            return word
        # Articles map to "" (dropped in Vietnamese). Never delete a Capitalised word —
        # it is likely a proper noun ("An", "The Fox") rather than a stray English article.
        if hit == "" and word[:1].isupper():
            return word
        return hit

    out = text
    for key in _GLOSSARY_KEYS:
        if " " in key or "-" in key:
            out = re.sub(rf"(?i)\b{re.escape(key)}\b", GLOSSARY[key], out)
    # (?<!\w)…(?!\w): Unicode-aware boundaries so only whole ASCII words are swapped.
    out = re.sub(r"(?<!\w)[A-Za-z][A-Za-z'-]*(?!\w)", swap, out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def translate_texts(
    texts: list[str], target: str = "vi", source: str = "auto", timeout: float = 4.0
) -> dict[str, Any]:
    """Translate a list of strings.

    Returns ``{"translations": [{"source", "text", "engine"}], "engine": <overall>}``.
    ``engine`` is ``"google"`` when every non-empty item came from the online service,
    ``"glossary"`` when the offline fallback had to be used, ``"mixed"`` for a blend and
    ``"none"`` when there was nothing to translate.

    Long texts (whole Flow prompts) are cut into URL-safe chunks. The first chunk probes the
    online endpoint; if it fails (offline / blocked / timed out) everything goes straight to
    the offline glossary, so a scene never stacks up ``N × timeout`` seconds of dead waits.
    Otherwise the remaining chunks are fetched in parallel. An item any of whose chunks
    failed falls back to the glossary as a whole.
    """
    target = target or "vi"
    source = source or "auto"
    # Every non-empty text, cut into URL-safe chunks; one network job per non-blank chunk.
    split: list[list[str] | None] = []
    jobs: list[tuple[int, int, str]] = []
    for idx, raw in enumerate(texts):
        text = (raw or "").strip()
        if not text:
            split.append(None)
            continue
        pieces = _split_chunks(text)
        split.append(pieces)
        jobs += [(idx, k, p.strip()) for k, p in enumerate(pieces) if p.strip()]

    answers: dict[tuple[int, int], str | None] = {}
    if jobs:
        # Probe with the first chunk: if the endpoint is down, skip the network for the
        # rest at once instead of stacking timeouts. Otherwise fetch the remaining chunks
        # in parallel so a whole scene (fields + full Flow prompts) comes back quickly.
        first = jobs[0]
        answers[first[:2]] = _google_translate(first[2], source, target, timeout)
        if answers[first[:2]] is not None and len(jobs) > 1:
            rest = jobs[1:]
            with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(rest))) as pool:
                got = pool.map(lambda j: _google_translate(j[2], source, target, timeout), rest)
                for j, g in zip(rest, got):
                    answers[j[:2]] = g

    results: list[dict[str, str]] = []
    engines: set[str] = set()
    for idx, raw in enumerate(texts):
        pieces = split[idx]
        if pieces is None:
            results.append({"source": raw or "", "text": "", "engine": "none"})
            continue
        done = [answers.get((idx, k)) if p.strip() else "" for k, p in enumerate(pieces)]
        if all(d is not None for d in done):
            text, engine = _join_pieces(pieces, done), "google"  # type: ignore[arg-type]
        else:
            text, engine = _glossary_translate((raw or "").strip()), "glossary"
        if not text:
            text, engine = _glossary_translate((raw or "").strip()), "glossary"
        results.append({"source": raw or "", "text": text, "engine": engine})
        engines.add(engine)
    if not engines:
        overall = "none"
    elif len(engines) == 1:
        overall = next(iter(engines))
    else:
        overall = "mixed"
    return {"translations": results, "engine": overall}
