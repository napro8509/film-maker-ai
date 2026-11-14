"""Turn a one-line idea into a storyboard with Claude (optional step)."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile

from pydantic import BaseModel

from . import ai
from . import durations as durations_mod
from . import model_meta
from .ai import GEMINI_PRO, clean_ai_model, effective_ai_model
from .storyboard import (NO_DIALOGUE, NO_SUBTITLES, Storyboard, StoryboardDraft, _line_key, clean_dialogue_language,
                         clean_language)

MODEL = "claude-opus-5-5"

# plan()/plan_with_claude_code() without `durations`: ask the metadata for the default video model's list.
_DEFAULT = object()

SYSTEM = """You are a film director writing shot lists for Google Flow (Veo video model).
Each scene becomes ONE short clip (4-8 seconds). Rules:
- Write every `prompt` and `image_prompt` in English (Veo understands English best), even if the idea is in another language.
- `prompt` = one clear action (who does what), plus sound if useful. No scene cuts inside one clip.
- Prefer mode "keyframe": `image_prompt` describes the opening frame as a still photo (subject, setting, who stands where); `prompt` describes the action from that frame.
- Direct every scene visually in `camera` (English film terms; the tool adds them to the prompts, so do not repeat them in `prompt` / `image_prompt`):
  `shot` (wide shot, medium shot, close-up, extreme close-up, over-the-shoulder shot, point-of-view shot...), `angle` (eye-level, low angle, high angle, bird's-eye view, dutch angle...),
  `movement` (exactly ONE move: static locked-off camera, slow dolly-in, tracking shot, pan, tilt, orbit, crane up, handheld...), `lens` (24mm wide-angle, 50mm, 85mm portrait, macro, anamorphic...),
  and when useful `focus` (shallow depth of field, rack focus...), `composition` (rule of thirds, symmetry, foreground framing, silhouette...), `lighting` for this shot, `speed` (slow motion, time-lapse), `notes`.
- Hook first: scene s01 starts mid-conflict (a striking face reaction, a shocking action or a provocative line) within its first 2 seconds, in a medium or close shot, not a calm establishing shot; the stakes are clear by about 7 seconds; give a twist or reveal in the middle; the last scene lands an emotional payoff or a twist that makes people comment or rewatch. Establish places with a short shot later when needed.
- For 9:16, keep faces and key action in the upper-middle of the frame; leave the bottom fifth and the right edge free of important content (platform buttons and subtitles go there).
- Edit like a film: vary shot sizes between consecutive scenes (wide -> medium -> close-up), go closer for emotion, use low angles for power and high angles for vulnerability, keep the 180-degree rule in dialogue (over-the-shoulder / reverse shots), and match the camera move to the mood (static or slow for calm, handheld or tracking for tension).
- Define recurring people in `characters` with a fixed, specific look, and refer to them by exact name in prompts. Give every main character a distinct silhouette, hair and signature clothing item, so they never look alike. In `prompt`, also name people by what the camera sees (e.g. "Lan, the woman in the red coat on the left"): the video model sees the picture, not names.
- Define every place that appears in the film once in `locations` with a fixed, specific `description` (layout, main furniture, props, colors, fixed light sources); set each scene's `location` to the exact location name.
- In every `image_prompt` (and the `prompt` of t2v scenes) state where we are, the time of day and the light source, and every character visible in the frame by exact name with their position (left/right, foreground/background, sitting/standing), including people blurred in the background. Never name a character who is not in the frame.
- Continuity: each scene starts from the end state of the previous scene: the same positions of people, objects that were moved stay moved, the same light and time of day unless the story deliberately jumps.
- After a time jump set `continue_previous: false`; when the place changes, change `location`.
- Write sound in `prompt` as "SFX: ..." and "Ambient noise: ...".
- Never write music, a score, a song, a melody or a soundtrack in `prompt`: every clip is generated on its own, so Veo would compose different music for each one and the film's sound would jump at every cut. One background score is added for the whole film when editing. Sound in `prompt` is only what is heard in the place: voices, ambient noise and sound effects.
- Put the shared visual look in `style` once instead of repeating it.
- `narration` is an optional short subtitle line. Write it in the subtitle language given in the message (a "Subtitle (narration) language" or "Narration language" line); if there is no such line, in the SAME language as the user's idea. If the message says "Subtitles: none", the film has no subtitles: leave `narration` and `subtitles` empty in every scene. Subtitles are added from it later, so never ask for visible text in a prompt: no captions, subtitles, signs with words, title cards or written dialogue in `prompt` / `image_prompt`.
- The film always has three subtitle tracks: whenever a scene has `narration`, also fill `subtitles` with that same line in Vietnamese (`vi`), Thai (`th`) and English (`en`); the one in the narration's own language is exactly the `narration` text, the other two are natural translations (not transliterations).
- Direct the content of every scene in `content_direction` (English, 1-2 sentences): what the audience must understand and feel in this clip, the emotional beat, and how each character in it acts and reacts (expression, body language). Make each scene move the story forward.
- Spoken lines go ONLY in `dialogue`, never in `prompt` / `image_prompt`: one row per line in the order spoken, `character` = exact character name, `line` = the exact words, `delivery` = how it is said in English (emotion, tone, volume, pace, e.g. "whispering, holding back tears"). Keep it short enough for the clip (about 2-3 short lines in 8 seconds). Characters take turns: one speaker per row, nobody talks at the same time. Best lip-sync: in each scene only ONE character speaks (at most 2 short lines, about 6-12 words in total) with that speaker's face clearly visible (close-up or medium close-up); show a back-and-forth as shot / reverse-shot across separate scenes, the others in frame only react. Give each speaker a distinct voice in `delivery` (pitch, age, tone).
- Never repeat dialogue: no character says or echoes a line another character already said, nobody says the same line twice, and no line repeats one from an earlier scene. Each line adds something new (an answer, a question, a reveal).
- Spoken audio language: if the message gives a "Spoken dialogue language", write every `line` in ONLY that language. If it says "Spoken dialogue: none", no character speaks, whispers or sings words in any scene: leave `dialogue` empty everywhere and describe only ambient sound and sound effects in `prompt`. If neither is given, write `line` in the language the characters would naturally speak (the idea's language when unclear) and name that language in `delivery` (instead of "she says angrily in Thai, voice only" in the prompt, delivery = "angrily, in Thai"). Never put spoken words in quotation marks anywhere: Veo tends to burn quoted lines into the picture as subtitles.
- Children (Google's child-safety filter blocks many pictures of minors and the credits are lost): never an
  exact age ("4 years old", "age 8", "10 months old") nor body details (chubby, round or rosy cheeks, plump): write
  "a toddler girl", "a schoolgirl", "a baby boy" and describe them by hair, clothes and what they do. Keep children
  fully and casually dressed in everyday activities, usually in medium or wide shots together with an adult; never
  a child in a bath, swimwear, undressing, in bed up close, asleep or limp, alone and frightened, crying hard, or an
  adult kissing, feeding or holding a child's face up close. Fear or shock in a child is played as surprise or
  playful worry. Give close-ups and the main spoken lines to adults or teens when the story allows.
- Google Flow refuses or silently fails some content, and the credits are lost ("Audio generation failed" when it
  will not make the sound; the clip deleted after rendering): insults or slurs (above all about disability, body,
  race or family), threats, shouting matches, fights, bullying shown up close, injuries, pain, blood, weapons, and
  above all CHILDREN in distress (hurt, crying hard, mocked, fighting, sick up close). Keep hard story beats, but
  show them mildly: a hurtful moment as a look, a whisper or a turned back; a bully's line as mild teasing; pain
  as tiredness; conflict as a calm, firm stand. Use calm, quiet, sincere or soft `delivery`, never fierce,
  sneering, screaming or "raised". The hook can be a worried face or a surprising moment instead of cruelty.
- Scene ids: s01, s02, ...
- Fill `thumbnail`: `text` = a short, catchy video title (max ~6 words) written in the spoken dialogue language when the message gives one; when it says "Spoken dialogue: none", in the subtitle language, or, without a subtitle line, in the language of `narration` (the user's idea); when neither is given, in the language the characters speak in the video (if unclear, the narration language); `prompt` = an English description of an eye-catching cover picture with the main characters by exact name at the story's most dramatic moment (no text in the picture).
- Do not set project_id, image/end_image, or `image` of locations."""


def _clean_locations(data: dict) -> None:
    """Locations as Claude wrote them -> valid ones: no blank names, first of each name (case-insensitive) kept,
    no `image`; a scene `location` that matches none is dropped. Never fails the plan."""
    locs = data.get("locations")
    if locs is None:
        return
    kept: list[dict] = []
    keys: set[str] = set()
    for loc in locs if isinstance(locs, list) else []:
        if not isinstance(loc, dict) or not isinstance(loc.get("name"), str) or not loc["name"].strip():
            continue
        key = loc["name"].strip().casefold()
        if key in keys:
            continue
        keys.add(key)
        loc.pop("image", None)
        kept.append(loc)
    if kept:
        data["locations"] = kept
    else:
        data.pop("locations")
    for scene in data.get("scenes") or []:
        if isinstance(scene, dict) and "location" in scene:
            name = scene["location"]
            if not isinstance(name, str) or name.strip().casefold() not in keys:
                scene.pop("location")


def _to_storyboard(data: dict) -> Storyboard:
    """Planner output -> Storyboard. Claude does not pick scene `characters` (detection by name stays the default),
    so drop any it invents in a scene; the storyboard's own `characters` list is kept. Locations are cleaned
    (`_clean_locations`) so a slip of Claude never fails the plan."""
    data = copy.deepcopy(data)
    data.pop("dialogue_language", None)  # set from the request by plan_auto, never by Claude
    data.pop("subtitle_language", None)
    data.pop("ai_model", None)
    _clean_locations(data)
    said: set[str] = set()
    for scene in data.get("scenes") or []:
        if isinstance(scene, dict):
            scene.pop("characters", None)
            # Safety net for the "never repeat a line" rule: drop a line already said anywhere earlier.
            rows = []
            for row in scene.get("dialogue") or []:
                key = _line_key(str(row.get("line") or "")) if isinstance(row, dict) else ""
                if key and key in said:
                    continue
                said.add(key)
                rows.append(row)
            if "dialogue" in scene:
                scene["dialogue"] = rows or None
    return Storyboard.model_validate(data)


def _user_message(idea: str, scenes: int, aspect: str, language_hint: str | None,
                  dialogue_language: str | None = None, durations: list[int] | None = None) -> str:
    user = f"Idea: {idea}\nNumber of scenes: {scenes}\nAspect ratio: {aspect}"
    if durations:  # right after the aspect line, so the message still ends with the language line
        user += "\nAllowed clip durations (seconds): " + durations_mod.allowed_text(list(durations))
    no_subs = bool(language_hint) and language_hint.strip().casefold() == NO_SUBTITLES
    if dialogue_language is None and not no_subs:  # old clients / CLI without --dialogue-lang: unchanged message
        if language_hint:
            user += f"\nNarration language: {language_hint}"
        return user
    if dialogue_language is not None and dialogue_language.strip().casefold() == NO_DIALOGUE:
        user += "\nSpoken dialogue: none (no character speaks; only ambient sound and sound effects)"
    elif dialogue_language is not None:
        user += (f"\nSpoken dialogue language: {dialogue_language} "
                 f"(every character who speaks speaks only {dialogue_language})")
    if no_subs:
        user += "\nSubtitles: none (the film has no subtitles: leave `narration` and `subtitles` empty in every scene)"
    elif language_hint:
        user += f"\nSubtitle (narration) language: {language_hint}"
    return user


def default_durations() -> list[int] | None:
    """Durations accepted by the model new projects start on (`normalize_new_storyboard`), or None."""
    m = model_meta.load()
    return m.durations(m.default_video_model)


def _durations_arg(durations: "list[int] | None | object") -> list[int] | None:
    return default_durations() if durations is _DEFAULT else durations  # type: ignore[return-value]


# Long films: Claude writes the storyboard in parts of at most PART_SCENES scenes (one reply cannot hold
# 50+ scenes), each part seeing what was already written. MAX_SCENES caps a request.
PART_SCENES = 15
MAX_SCENES = 80


def _sid(i: int) -> str:
    return f"s{i:02d}"


def _written_so_far(draft: dict) -> str:
    """Compact recap of the parts already written, for the next part's message."""
    lines = [f"Title: {draft.get('title', '')}"]
    if draft.get("style"):
        lines.append(f"Style: {draft['style']}")
    for c in draft.get("characters") or []:
        lines.append(f"Character {c.get('name')}: {c.get('description', '')}")
    for loc in draft.get("locations") or []:
        lines.append(f"Location {loc.get('name')}: {loc.get('description', '')}")
    for sc in draft.get("scenes") or []:
        what = sc.get("content_direction") or sc.get("prompt") or ""
        where = f" [at {sc['location']}]" if sc.get("location") else ""
        lines.append(f"{sc.get('id')}{where}: {' '.join(str(what).split())[:220]}")
        for row in sc.get("dialogue") or []:
            if isinstance(row, dict) and row.get("line"):
                lines.append(f"   {row.get('character')}: {row.get('line')}")
    return "\n".join(lines)


def _part_message(base: str, total: int, start: int, count: int, draft: dict | None) -> str:
    """The user message for scenes start..start+count-1 (1-based) of a `total`-scene film."""
    first, last = _sid(start), _sid(start + count - 1)
    end = start + count - 1
    if draft is None:
        return (f"{base}\n\nThis film is long, so it is written in parts. Plan the whole story arc across all "
                f"{total} scenes, but in this reply write ONLY scenes {first}-{last} (the opening of the story), "
                "plus title, style, thumbnail, EVERY character and EVERY location the whole film needs.")
    tail = ("This is the LAST part: bring the story to its ending and emotional payoff."
            if end >= total else
            f"Scenes after {last} come in a later part, so do not end the story yet.")
    return (f"{base}\n\nThis film is long, so it is written in parts. Scenes {_sid(1)}-{_sid(start - 1)} are "
            f"already written (summary below). In this reply write ONLY scenes {first}-{last} of {total}, "
            "continuing the story right where it stopped. Keep the same title, style, characters and locations "
            "(exact names and looks; add a character or location only if the story needs a new one). Do not repeat any beat or "
            f"dialogue line from the earlier scenes. {tail}\n\nAlready written:\n{_written_so_far(draft)}")


def _draft_in_parts(draft_fn, base: str, total: int, log=None, *, model_label: str = "Claude") -> dict:
    """Ask `draft_fn(user_message) -> draft dict` for the storyboard, in parts when `total` is large."""
    if total <= PART_SCENES:
        return draft_fn(base)
    parts = -(-total // PART_SCENES)
    merged: dict | None = None
    tries = 0
    while merged is None or len(merged["scenes"]) < total:
        done = 0 if merged is None else len(merged["scenes"])
        count = min(PART_SCENES, total - done)
        part_no = done // PART_SCENES + 1
        if log:
            log(f"Phần {part_no}/{parts}: viết cảnh {_sid(done + 1)}–{_sid(done + count)}…")
        out = draft_fn(_part_message(base, total, done + 1, count, merged))
        new = [sc for sc in (out.get("scenes") or []) if isinstance(sc, dict)][:count]
        if merged is None:
            merged = {k: v for k, v in out.items() if k != "scenes"}
            merged["scenes"] = []
        else:  # keep part 1's title/style/thumbnail; add only characters and locations that are new
            for field in ("characters", "locations"):
                known = {str(x.get("name", "")).casefold() for x in merged.get(field) or []}
                for x in out.get(field) or []:
                    if isinstance(x, dict) and str(x.get("name", "")).casefold() not in known:
                        merged.setdefault(field, []).append(x)
                        known.add(str(x.get("name", "")).casefold())
        for sc in new:
            sc["id"] = _sid(len(merged["scenes"]) + 1)
            merged["scenes"].append(sc)
        if not new:
            tries += 1
            if tries >= 2:
                raise RuntimeError(f"{model_label} dừng ở {len(merged['scenes'])}/{total} cảnh; hãy thử lại.")
        else:
            tries = 0
    if log:
        log(f"Đã ghép {len(merged['scenes'])} cảnh từ {parts} phần.")
    return merged


def claude_code_binary() -> str | None:
    return shutil.which(os.environ.get("CLAUDE_BIN", "claude"))


def _subscription_env() -> dict[str, str]:
    # Without these, Claude Code uses the logged-in claude.ai account (Pro/Max plan)
    # instead of billing an API key.
    return {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}


def claude_code_status() -> dict:
    """{"available": bool, "logged_in": bool, "method": "claude.ai" | ...}"""
    binary = claude_code_binary()
    if not binary:
        return {"available": False, "logged_in": False, "method": None}
    try:
        proc = subprocess.run([binary, "auth", "status"], capture_output=True, text=True, timeout=30,
                              env=_subscription_env())
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"available": True, "logged_in": False, "method": None}
    return {"available": True, "logged_in": bool(data.get("loggedIn")), "method": data.get("authMethod")}


def _draft_claude_code(user: str, timeout: int = 900, *, system: str | None = None, schema_model=None) -> dict:
    """One planner call through the local `claude` CLI -> the raw draft dict (drama SYSTEM / schema by default)."""
    binary = claude_code_binary()
    if not binary:
        raise RuntimeError("không thấy lệnh `claude` (Claude Code). Cài tại https://claude.com/claude-code")
    schema = json.dumps((schema_model or StoryboardDraft).model_json_schema())
    cmd = [binary, "-p", user, "--output-format", "json", "--json-schema", schema, "--system-prompt", system or SYSTEM,
           "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
    with tempfile.TemporaryDirectory() as cwd:  # keep any project CLAUDE.md out of the prompt
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                                  env=_subscription_env())
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Claude Code chạy quá lâu; hãy thử lại hoặc giảm số cảnh.") from exc
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        tail = (proc.stderr or proc.stdout).strip()[-600:]
        raise RuntimeError(f"Claude Code lỗi (exit {proc.returncode}): {tail}") from None
    if data.get("is_error") or not data.get("structured_output"):
        msg = str(data.get("result") or data.get("subtype") or "không có kết quả")[:600]
        if "login" in msg.lower() or "auth" in msg.lower():
            msg += " — chạy `claude` trong Terminal và đăng nhập (/login) bằng tài khoản Claude của bạn."
        raise RuntimeError(f"Claude Code không viết được storyboard: {msg}")
    return data["structured_output"]


def plan_with_claude_code(idea: str, *, scenes: int = 6, aspect: str = "16:9", language_hint: str | None = None,
                          dialogue_language: str | None = None, timeout: int = 900,
                          durations: list[int] | None | object = _DEFAULT, log=None) -> Storyboard:
    """Same as plan(), but through the local `claude` CLI so it runs on the user's Claude subscription."""
    base = _user_message(idea, scenes, aspect, language_hint, dialogue_language, _durations_arg(durations))
    sb = _to_storyboard(_draft_in_parts(lambda user: _draft_claude_code(user, timeout), base, scenes, log))
    sb.aspect = aspect  # type: ignore[assignment]
    return sb


def _draft_gemini(user: str, timeout: int = 900, *, system: str | None = None, schema_model=None,
                  backend: str = "auto", gemini_api_key: str | None = None) -> dict:
    """One planner call through Gemini Pro (Gemini API or Gemini CLI) -> raw draft dict."""
    return ai.draft_json(system or SYSTEM, user, schema_model or StoryboardDraft,
                         backend=backend, gemini_api_key=gemini_api_key, timeout=timeout)


def plan_with_gemini(idea: str, *, scenes: int = 6, aspect: str = "16:9", language_hint: str | None = None,
                     dialogue_language: str | None = None, timeout: int = 900,
                     durations: list[int] | None | object = _DEFAULT, backend: str = "auto",
                     gemini_api_key: str | None = None, log=None) -> Storyboard:
    """Write a storyboard using Gemini Pro (Gemini API key or Gemini CLI)."""
    base = _user_message(idea, scenes, aspect, language_hint, dialogue_language, _durations_arg(durations))
    draft = _draft_in_parts(
        lambda user: _draft_gemini(user, timeout, backend=backend, gemini_api_key=gemini_api_key),
        base, scenes, log, model_label="Gemini Pro",
    )
    sb = _to_storyboard(draft)
    sb.aspect = aspect  # type: ignore[assignment]
    sb.ai_model = GEMINI_PRO
    return sb


def plan_auto(idea: str, *, backend: str = "auto", api_key: str | None = None, dialogue_language: str | None = None,
              ai_model: str | None = None, gemini_api_key: str | None = None, **kw) -> tuple[Storyboard, str]:
    """Plan a storyboard with `ai_model` ('claude' default or 'gemini-pro').
    backend: auto | claude_code | api | gemini_api | gemini_cli.
    dialogue_language (and language_hint as subtitle_language) are stored on the storyboard from the request,
    overriding anything the backend returned; without dialogue_language neither field is set."""
    dialogue_language = clean_dialogue_language(dialogue_language)  # ValueError before any AI call
    chosen_model = clean_ai_model(ai_model)
    if dialogue_language is not None:
        kw["dialogue_language"] = dialogue_language
    if chosen_model == GEMINI_PRO:
        used = ai.pick_backend(backend, ai_model=GEMINI_PRO, gemini_api_key=gemini_api_key)
        if used is None:
            raise RuntimeError(ai.not_ready_message(GEMINI_PRO))
        sb = plan_with_gemini(idea, backend=used, gemini_api_key=gemini_api_key, **kw)
        sb.ai_model = GEMINI_PRO
    else:
        if backend == "auto":
            backend = "claude_code" if claude_code_binary() else "api"
        if backend == "claude_code":
            sb, used = plan_with_claude_code(idea, **kw), "claude_code"
        else:
            if api_key:
                os.environ["ANTHROPIC_API_KEY"] = api_key
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("chưa có ANTHROPIC_API_KEY (hoặc cài Claude Code để dùng gói Claude của bạn)")
            sb, used = plan(idea, **kw), "api"
    sb.dialogue_language = dialogue_language
    sb.subtitle_language = clean_language(kw.get("language_hint")) if sb.dialogue_language else None
    if (kw.get("language_hint") or "").strip().casefold() == NO_SUBTITLES:
        sb.subtitle_language = NO_SUBTITLES
        for sc in sb.scenes:  # "Không phụ đề": nothing to show even if AI wrote a line anyway
            sc.narration, sc.subtitles = None, None
    sb = fix_vietnamese_diacritics(sb, backend=used, api_key=api_key, ai_model=chosen_model,
                                   gemini_api_key=gemini_api_key, log=kw.get("log"))
    # New projects never start on a model Flow is retiring (default: veo-lite).
    meta = model_meta.load()
    sb = model_meta.normalize_new_storyboard(sb, meta)
    # Safety net: a duration the effective model does not accept is rounded up (even on a model Claude picked).
    return durations_mod.round_new_storyboard(sb, meta), used


# --- Vietnamese without diacritics -------------------------------------------------------------------
# Claude now and then writes Vietnamese lines without their tone marks ("Ca nha lam gi tren do vay?"): Veo then
# speaks the wrong tones and the subtitles are wrong. Spotted here and sent back once to get the marks added.

class _VnItem(BaseModel):
    key: str
    text: str


class _VnFix(BaseModel):
    items: list[_VnItem]


VN_FIX_SYSTEM = """You restore Vietnamese diacritics (dấu). Each item is Vietnamese written without its tone marks.
Return every item with the same `key` and the same words, punctuation and meaning, written with full, correct
Vietnamese diacritics. Do not translate, shorten or rephrase."""


def _is_vietnamese(lang: str | None) -> bool:
    return (lang or "").strip().casefold() in ("vietnamese", "tiếng việt", "vi")


def _plain_vietnamese(text: str | None) -> bool:
    """Words but no letter outside ASCII (dashes and quotes do not count): Vietnamese of three words or more is
    never like that."""
    t = (text or "").strip()
    return len(re.findall(r"[A-Za-z]+", t)) >= 3 and not any(ch.isalpha() and ord(ch) > 127 for ch in t)


def vietnamese_without_marks(sb: Storyboard) -> dict[str, str]:
    """{key: text} of the Vietnamese lines / subtitles written without diacritics (empty when there are few)."""
    out: dict[str, str] = {}
    total = 0
    viet_lines = _is_vietnamese(sb.dialogue_language)
    viet_subs = _is_vietnamese(sb.subtitle_language) or sb.primary_subtitle_code() == "vi"
    for sc in sb.scenes:
        if viet_lines:
            for i, d in enumerate(sc.dialogue or []):
                if d.line and d.line.strip():
                    total += 1
                    if _plain_vietnamese(d.line):
                        out[f"{sc.id}.line.{i}"] = d.line
        sp = sc.video_shot.speaker if sc.video_shot is not None else None
        if viet_lines and sp and sp.line.strip():
            total += 1
            if _plain_vietnamese(sp.line):
                out[f"{sc.id}.speaker"] = sp.line
        if viet_subs and sc.narration and sc.narration.strip():
            total += 1
            if _plain_vietnamese(sc.narration):
                out[f"{sc.id}.narration"] = sc.narration
        if sc.subtitles and sc.subtitles.vi and _plain_vietnamese(sc.subtitles.vi):
            out[f"{sc.id}.vi"] = sc.subtitles.vi
    if (viet_lines or viet_subs) and sb.thumbnail.text and _plain_vietnamese(sb.thumbnail.text):
        out["thumbnail"] = sb.thumbnail.text
    # one slip ("OK la") is not worth a call; most lines without marks is the failure
    return out if len(out) >= 2 and len(out) * 2 >= max(1, total) else {}


def fix_vietnamese_diacritics(sb: Storyboard, *, backend: str, api_key: str | None = None,
                              ai_model: str | None = None, gemini_api_key: str | None = None, log=None) -> Storyboard:
    bad = vietnamese_without_marks(sb)
    if not bad:
        return sb
    use_gemini = effective_ai_model(ai_model or sb.ai_model) == GEMINI_PRO or backend in ("gemini_api", "gemini_cli")
    if log:
        log(f"⚠ {len(bad)} câu tiếng Việt bị thiếu dấu: nhờ {'Gemini Pro' if use_gemini else 'Claude'} thêm dấu lại…")
    user = "Items:\n" + json.dumps([{"key": k, "text": v} for k, v in bad.items()], ensure_ascii=False)
    try:
        if use_gemini:
            out = _draft_gemini(user, 300, system=VN_FIX_SYSTEM, schema_model=_VnFix,
                                backend=backend, gemini_api_key=gemini_api_key)
        elif backend == "claude_code":
            out = _draft_claude_code(user, 300, system=VN_FIX_SYSTEM, schema_model=_VnFix)
        else:
            if api_key:
                os.environ["ANTHROPIC_API_KEY"] = api_key
            out = _draft_api(user, system=VN_FIX_SYSTEM, schema_model=_VnFix)
    except RuntimeError as exc:
        if log:
            log(f"⚠ Không thêm được dấu: {exc}")
        return sb
    fixed = {str(it.get("key")): str(it.get("text") or "").strip() for it in out.get("items") or [] if isinstance(it, dict)}
    by_id = {sc.id: sc for sc in sb.scenes}
    n = 0
    for key, text in fixed.items():
        if key not in bad or not text or _plain_vietnamese(text):
            continue
        if key == "thumbnail":
            sb.thumbnail.text = text
        else:
            sid, _, part = key.partition(".")
            sc = by_id.get(sid)
            if sc is None:
                continue
            if part == "narration":
                sc.narration = text
            elif part == "speaker" and sc.video_shot is not None and sc.video_shot.speaker:
                sc.video_shot.speaker.line = text
            elif part == "vi" and sc.subtitles:
                sc.subtitles.vi = text
            elif part.startswith("line.") and sc.dialogue:
                i = int(part.split(".")[1])
                if i < len(sc.dialogue):
                    sc.dialogue[i].line = text
            else:
                continue
        n += 1
    if log:
        log(f"Đã thêm dấu cho {n}/{len(bad)} câu.")
    return sb


def _draft_api(user: str, *, system: str | None = None, schema_model=None) -> dict:
    """One planner call through the Anthropic API -> the raw draft dict (drama SYSTEM / schema by default)."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=system or SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=schema_model or StoryboardDraft,
        output_config={"effort": "medium"},
        # If a safety classifier declines, let the API retry on a fallback model.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude từ chối ý tưởng này; hãy thử diễn đạt lại.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Kịch bản quá dài; hãy giảm số cảnh.")
    return response.parsed_output.model_dump(exclude_none=True)


def plan(idea: str, *, scenes: int = 6, aspect: str = "16:9", language_hint: str | None = None,
         dialogue_language: str | None = None, durations: list[int] | None | object = _DEFAULT,
         log=None) -> Storyboard:
    base = _user_message(idea, scenes, aspect, language_hint, dialogue_language, _durations_arg(durations))
    sb = _to_storyboard(_draft_in_parts(_draft_api, base, scenes, log))
    sb.aspect = aspect  # type: ignore[assignment]
    return sb


# --- subtitle translations (vi / th / en tracks) ---------------------------

TRANSLATE_SYSTEM = """You translate short film subtitle lines. For every line you get, return it in
Vietnamese (`vi`), Thai (`th`) and English (`en`): natural, short subtitle wording that keeps the meaning
and tone; keep character names as they are. Return one item per input id, nothing else."""


class _SubLine(BaseModel):
    id: str
    vi: str
    th: str
    en: str


class _SubLines(BaseModel):
    lines: list[_SubLine]


def _translate_items(lines: dict[str, str], *, backend: str, api_key: str | None, timeout: int,
                     ai_model: str | None = None, gemini_api_key: str | None = None) -> dict[str, dict]:
    user = "Subtitle lines (id: text):\n" + "\n".join(f"{k}: {v}" for k, v in lines.items())
    if effective_ai_model(ai_model) == GEMINI_PRO or backend in ("gemini_api", "gemini_cli"):
        out = _draft_gemini(user, timeout, system=TRANSLATE_SYSTEM, schema_model=_SubLines,
                            backend=backend, gemini_api_key=gemini_api_key)
        items = out.get("lines") or []
        return {str(it.get("id")): it for it in items if isinstance(it, dict)}
    if backend == "auto":
        backend = "claude_code" if claude_code_binary() else "api"
    if backend == "claude_code":
        binary = claude_code_binary()
        if not binary:
            raise RuntimeError("không thấy lệnh `claude` (Claude Code)")
        item = {"type": "object", "properties": {k: {"type": "string"} for k in ("id", "vi", "th", "en")},
                "required": ["id", "vi", "th", "en"]}
        schema = json.dumps({"type": "object", "properties": {"lines": {"type": "array", "items": item}},
                             "required": ["lines"]})
        cmd = [binary, "-p", user, "--output-format", "json", "--json-schema", schema,
               "--system-prompt", TRANSLATE_SYSTEM, "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
        with tempfile.TemporaryDirectory() as cwd:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                                      env=_subscription_env())
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Claude Code dịch phụ đề quá lâu") from exc
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(f"Claude Code lỗi (exit {proc.returncode})") from None
        if data.get("is_error") or not data.get("structured_output"):
            raise RuntimeError("Claude Code không dịch được phụ đề: " + str(data.get("result") or "")[:300])
        items = data["structured_output"].get("lines") or []
    else:
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("chưa có ANTHROPIC_API_KEY (hoặc cài Claude Code để dùng gói Claude của bạn)")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc

        response = anthropic.Anthropic().messages.parse(
            model=MODEL, max_tokens=8000, system=TRANSLATE_SYSTEM, messages=[{"role": "user", "content": user}],
            output_format=_SubLines, output_config={"effort": "low"},
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
        )
        if response.stop_reason == "refusal" or not response.parsed_output:
            raise RuntimeError("Claude không dịch được phụ đề.")
        items = [x.model_dump() for x in response.parsed_output.lines]
    return {str(it.get("id")): it for it in items if isinstance(it, dict)}


def translate_subtitles(sb: Storyboard, *, backend: str = "auto", api_key: str | None = None,
                        timeout: int = 300, ai_model: str | None = None,
                        gemini_api_key: str | None = None) -> dict[str, dict[str, str]]:
    """Fill the missing vi / th / en lines of scenes with narration (in place).
    Returns {scene id: {code: line}} of what was added; {} when nothing was missing."""
    from .storyboard import Subtitles

    missing = sb.missing_subtitles()
    if not missing:
        return {}
    scenes = {s.id: s for s in sb.scenes}
    model = effective_ai_model(ai_model or sb.ai_model)
    got = _translate_items({sid: scenes[sid].narration.strip() for sid in missing},
                           backend=backend, api_key=api_key, timeout=timeout,
                           ai_model=model, gemini_api_key=gemini_api_key)
    added: dict[str, dict[str, str]] = {}
    for sid, codes in missing.items():
        it = got.get(sid) or {}
        new = {c: v for c in codes if (v := " ".join(str(it.get(c) or "").split()))}
        if not new:
            continue
        sc = scenes[sid]
        sc.subtitles = Subtitles(**{**(sc.subtitles.model_dump() if sc.subtitles else {}), **new})
        added[sid] = new
    return added


# --- video titles for the thumbnail -------------------------------------------

TITLE_SYSTEM = """You write titles for short drama videos posted on Facebook / TikTok / YouTube Shorts.
Given a storyboard, propose catchy, emotional, curiosity-driven titles that make people click.
Write each title in the VIDEO'S LANGUAGE: the language the characters speak in the video.
If the storyboard has a "Spoken language:" line, it decides the language and overrides any hint in the
scene prompts (such as "says ... in Thai"); "Spoken language: none; write titles in X" means write in X.
Otherwise look for hints such as "says ... in Thai" in the scene prompts. Only if nothing
says what language is spoken, use the language of the narration lines, else of the film title.
Each title: at most ~8 words, no hashtags, no emojis, no quotation marks, no ending period.
For each title also give `vi`: a natural Vietnamese translation, so a Vietnamese editor understands it
(leave `vi` empty if the title is already in Vietnamese).
Vary the angle: the secret, the twist, the conflict, the emotion. Return only the titles."""


def _story_summary(sb: Storyboard) -> str:
    lines = [f"Film title: {sb.title}"]
    if sb.dialogue_language == NO_DIALOGUE:
        lines.append(f"Spoken language: none; write titles in {sb.subtitle_language}"
                     if sb.subtitle_language and sb.has_subtitles
                     else "Spoken language: none; write titles in the language of the film title" if sb.subtitle_language
                     else "Spoken language: none; write titles in the language of the narration lines")
    elif sb.dialogue_language:
        lines.append(f"Spoken language: {sb.dialogue_language}")
    if sb.characters:
        lines.append("Characters: " + "; ".join(f"{c.name} ({c.description[:80]})" for c in sb.characters))
    narr = [s.narration for s in sb.scenes if s.narration]
    if narr:
        lines.append("Narration lines (subtitle language): " + " / ".join(narr))
    spoken = [f"{d.character}: {d.line}" for s in sb.scenes for d in s.spoken_lines()]
    if spoken:
        lines.append("Dialogue (spoken language): " + " / ".join(spoken)[:1500])
    lines.append("Scenes: " + " | ".join(
        f"{s.id}: {' / '.join(x[:300] for x in (s.image_prompt, s.prompt) if x)}" for s in sb.scenes))
    return "\n".join(lines)


def _clean_line(t) -> str:
    return " ".join(str(t or "").split()).strip(" \"'“”‘’«».。")


def _clean_titles(items, n: int) -> list[dict[str, str]]:
    """[{"text": title in the video's language, "vi": Vietnamese translation or ""}]. Accepts plain strings too."""
    out: list[dict[str, str]] = []
    for it in items or []:
        if isinstance(it, dict):
            text, vi = _clean_line(it.get("text") or it.get("title")), _clean_line(it.get("vi"))
        else:
            text, vi = _clean_line(it), ""
        if text and all(o["text"] != text for o in out):
            out.append({"text": text, "vi": "" if vi == text else vi})
    return out[:n]


class _TitleOpt(BaseModel):
    text: str
    vi: str


class _TitleOpts(BaseModel):
    titles: list[_TitleOpt]


def suggest_titles(sb: Storyboard, *, backend: str = "auto", api_key: str | None = None, n: int = 5,
                   timeout: int = 180, ai_model: str | None = None,
                   gemini_api_key: str | None = None) -> list[dict[str, str]]:
    """A few title ideas for the thumbnail, written by Claude or Gemini Pro."""
    user = f"{_story_summary(sb)}\n\nWrite {n} title options."
    model = effective_ai_model(ai_model or sb.ai_model)
    if model == GEMINI_PRO or backend in ("gemini_api", "gemini_cli"):
        out = _draft_gemini(user, timeout, system=TITLE_SYSTEM, schema_model=_TitleOpts,
                            backend=backend, gemini_api_key=gemini_api_key)
        return _clean_titles(out.get("titles"), n)
    if backend == "auto":
        backend = "claude_code" if claude_code_binary() else "api"
    if backend == "claude_code":
        binary = claude_code_binary()
        if not binary:
            raise RuntimeError("không thấy lệnh `claude` (Claude Code)")
        item = {"type": "object", "properties": {"text": {"type": "string"}, "vi": {"type": "string"}},
                "required": ["text", "vi"]}
        schema = json.dumps({"type": "object", "properties": {"titles": {"type": "array", "items": item}},
                             "required": ["titles"]})
        cmd = [binary, "-p", user, "--output-format", "json", "--json-schema", schema, "--system-prompt", TITLE_SYSTEM,
               "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
        with tempfile.TemporaryDirectory() as cwd:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                                      env=_subscription_env())
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Claude Code chạy quá lâu; hãy thử lại.") from exc
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(f"Claude Code lỗi (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[-400:]}") from None
        if data.get("is_error") or not data.get("structured_output"):
            raise RuntimeError("Claude Code không gợi ý được tiêu đề: " + str(data.get("result") or "không có kết quả")[:400])
        return _clean_titles(data["structured_output"].get("titles"), n)

    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("chưa có ANTHROPIC_API_KEY (hoặc cài Claude Code để dùng gói Claude của bạn)")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("cần cài thêm: pip install 'film-maker-ai[ai]' (gói anthropic)") from exc

    response = anthropic.Anthropic().messages.parse(
        model=MODEL, max_tokens=2000, system=TITLE_SYSTEM, messages=[{"role": "user", "content": user}],
        output_format=_TitleOpts, output_config={"effort": "low"},
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal" or not response.parsed_output:
        raise RuntimeError("Claude không gợi ý được tiêu đề cho phim này.")
    return _clean_titles([t.model_dump() for t in response.parsed_output.titles], n)
