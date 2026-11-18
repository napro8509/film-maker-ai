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

    As soon as the online endpoint fails to answer one item (offline / blocked / timed
    out), the rest skip the network and go straight to the offline glossary. This keeps a
    6–10 field scene from stacking up ``N × timeout`` seconds of dead waits on a flaky
    connection — the whole "dịch nhanh" stays quick and falls back at once.
    """
    results: list[dict[str, str]] = []
    engines: set[str] = set()
    target = target or "vi"
    source = source or "auto"
    online_down = False
    for raw in texts:
        text = (raw or "").strip()
        if not text:
            results.append({"source": raw or "", "text": "", "engine": "none"})
            continue
        got = None
        if not online_down:
            got = _google_translate(text, source, target, timeout)
            if got is None:
                online_down = True  # endpoint unreachable: stop hammering it
        engine = "google" if got is not None else "glossary"
        if got is None:
            got = _glossary_translate(text)
        results.append({"source": raw or "", "text": got, "engine": engine})
        engines.add(engine)
    if not engines:
        overall = "none"
    elif len(engines) == 1:
        overall = next(iter(engines))
    else:
        overall = "mixed"
    return {"translations": results, "engine": overall}
