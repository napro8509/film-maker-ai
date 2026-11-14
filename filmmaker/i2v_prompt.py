"""Structured image-to-video prompts for the basic formats (Phim điện ảnh, Phim bộ, Phim ngắn, Clip ngắn).

Follows the house I2V prompt guide ("Quy Tắc & Cấu Trúc Prompt Chuẩn Cho Hệ Thống Tạo Video AI Tự Động"):
1. only motion (the picture already holds the look), 2. people named by [position + visual tag], never by name,
3. silent binding for listeners, 4. one spoken line of at most 12 words, no quotes / apostrophes,
5. one or two physical action beats, 6. no long negatives (one short closing sentence),
7. no duration / aspect / resolution in the words.

Dialogue scenes also follow the eyeline guide ("Veo Dialogue & Eyeline Lock Specification"), against Lens-Locking
(the speaker turning to stare into the lens when the mouth opens) and Eyeline Mismatch: a locked-off viewpoint first,
positions and gaze in 2D frame coordinates (frame left / right, off-screen left / right), the gaze anchor right
before the speech verb, a lip lock after the line, and no camera / lens / viewer / cinematic words at all.

The scene keeps the parts (`VideoShot`); `assemble` joins them with the guide's templates and `clean_*` applies
the validation checklist (quotes, contractions, word count, silent binding) in code, before anything is sent.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

MAX_WORDS = 12
SILENT_BINDING = "remaining completely silent with lips closed, nodding slightly"  # eyeline guide, token map
NO_TEXT_SHORT = "(no subtitles, no captions, no on-screen text)"
# Still pictures of the basic formats: the short form of storyboard.NO_TEXT (no long negative, no "dialogue").
NO_TEXT_IMAGE = "No on-screen text, letters or watermarks."
CLOSING = "Single continuous shot, no scene cuts."
# Review 2: no "no dialogue, no music" (negatives made Veo add murmurs / odd music): silence is said with real sounds.
DEFAULT_SOUND = "quiet ambient room tone"
# Review 2: the start frame already holds the place and the light; "... matching the starting frame" only dilutes.
_FRAME_MATCH = re.compile(r"(?i),?\s*(?:\b(?:under|in|with)\b[^,.;]*?)?\bmatching the (?:starting|opening|previous|first|start) (?:frame|image|shot)")
# Sentence-start position anchors: two of them in `action` = two people acting at once (review 2: 1 active, 1 passive).
_ANCHOR = re.compile(r"(?i)(?:^|[.;!]\s+)(?:on the (?:far )?(?:left|right)|in the (?:center|centre|middle|back|background|foreground)|"
                     r"in front|at the (?:left|right|back|front))\b")

# Rule 4: "don't" -> "do not" before every apostrophe is dropped.
_CONTRACTIONS = [
    (r"\bcan't\b", "cannot"), (r"\bwon't\b", "will not"), (r"\bshan't\b", "shall not"), (r"\blet's\b", "let us"),
    (r"\b(\w+)n't\b", r"\1 not"), (r"\b(I)'m\b", r"\1 am"), (r"\b(\w+)'re\b", r"\1 are"), (r"\b(\w+)'ll\b", r"\1 will"),
    (r"\b(\w+)'ve\b", r"\1 have"), (r"\b(\w+)'d\b", r"\1 would"),
    (r"\b(it|that|he|she|there|here|what|who|where|how)'s\b", r"\1 is"),
]
_QUOTES = re.compile(r"[\"'“”‘’«»„`]")
# Rule 7: technical parameters do not belong in the words.
_TECH = re.compile(r"(?i)(?:\b(?:vertical|horizontal|portrait|landscape)\s+)?\b(?:9:16|16:9|1:1|4:3|3:4)\b(?:\s*(?:frame|format|aspect(?: ratio)?|video))?"
                   r"|(?:\b(?:in|for|over|lasting)\s+)?\b\d+(?:\.\d+)?\s*(?:-|\s)?(?:seconds?|secs?|s)\b(?:\s+(?:long|clip|video))?"
                   r"|\b(?:4k|8k|1080p|720p|60\s*fps|24\s*fps|30\s*fps)\b")


def _same_case(old: str, new: str) -> str:
    return new[0].upper() + new[1:] if old[:1].isupper() and new else new


def clean_text(text: str | None) -> str:
    """Apostrophe contractions expanded, quotes removed, technical numbers removed, spaces normalised."""
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    t = t.replace("’", "'").replace("‘", "'")
    for pat, rep in _CONTRACTIONS:
        t = re.sub(pat, lambda m, rep=rep: _same_case(m.group(0), m.expand(rep)), t, flags=re.IGNORECASE)
    t = _QUOTES.sub("", t)
    t = _TECH.sub("", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,.;:])\1+", r"\1", t)
    t = re.sub(r"^[\s,.;:]+", "", t)
    return " ".join(t.split()).strip(" ,;")


def strip_technical(text: str | None) -> str:
    """Rule 7 only (no aspect / duration / resolution words), for still-image prompts: apostrophes stay, so a
    possessive like "Dad's face" keeps the name the character references are found by."""
    t = _TECH.sub("", " ".join(str(text or "").split()))
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,.;:])\1+", r"\1", t)
    return re.sub(r"^[\s,.;:]+", "", t).strip(" ,;")


def clean_line(text: str | None) -> str:
    """A spoken line: clean_text, without the final full stop (the template adds one)."""
    return clean_text(text).rstrip(" .")


def word_count(text: str | None) -> int:
    return len(re.findall(r"[\w'-]+", str(text or ""), re.UNICODE))


class Speaker(BaseModel):
    position: str = Field(default="", description="Where the speaker is in the frame, in frame coordinates: "
                          "'frame left', 'frame right' or 'frame center'.")
    visual_tag: str = Field(description="What the camera sees, never a name: 'the little girl in the yellow dress'; "
                            "'The subject' / 'she' / 'he' only when one person is in the frame.")
    voice_style: str = Field(default="", description="Tone and pace, e.g. 'excited, clear'.")
    line: str = Field(description="ONE short sentence of at most 12 words, no quotes or apostrophes.")
    action: str = Field(default="", description="Physical delivery gesture in present tense, e.g. 'leans forward "
                        "slightly'.")
    head_angle: str = Field(default="", description="'three-quarter profile' (default) or 'side profile'. Never "
                            "facing the viewpoint straight on.")
    gaze: str = Field(default="", description="Where the speaker looks, in frame coordinates: 'frame right', 'frame "
                      "left', 'off-screen left', 'off-screen right', or the visual tag of a listener in the frame. "
                      "Empty: the tool picks the side facing the listener (or the opposite frame edge).")
    gaze_level: str = Field(default="", description="'at eye level' (default), 'slightly upward' or 'slightly downward'.")


class Listener(BaseModel):
    position: str = Field(default="", description="In frame coordinates: 'frame left', 'frame right', 'frame center'.")
    visual_tag: str = Field(description="e.g. 'the woman in the beige sweater'.")
    silent_action: str = Field(default="", description="A small silent reaction, e.g. 'listening attentively with a "
                               "gentle closed-mouth smile'. The lip lock is added by the tool.")


class VideoShot(BaseModel):
    """The parts of one image-to-video prompt (basic formats)."""
    camera: str = Field(default="", description="Shot size, depth of field, ONE continuous camera move and the ending "
                        "framing, e.g. 'Static locked-off medium shot, shallow depth of field'.")
    action: str = Field(default="", description="[position + visual tag] + 1-2 observable physical action beats in "
                        "present tense, mouth closed unless speaking. No names, no static look.")
    environment: str = Field(default="", description="Secondary motion and lighting continuity, e.g. 'curtains sway "
                             "softly under warm daylight matching the starting frame'.")
    audio: str = Field(default="", description="Ambient room tone and SFX tied to the movement. No music.")
    speaker: Speaker | None = Field(default=None, description="Only when someone on screen speaks.")
    listeners: list[Listener] | None = Field(default=None, description="Everyone else visible while someone speaks.")

    @field_validator("listeners", mode="before")
    @classmethod
    def _blank_listeners(cls, v: object) -> object:
        if isinstance(v, list):
            v = [x for x in v if not (isinstance(x, dict) and not str(x.get("visual_tag") or "").strip())]
            return v or None
        return v

    @field_validator("speaker", mode="before")
    @classmethod
    def _blank_speaker(cls, v: object) -> object:
        if isinstance(v, dict) and not str(v.get("line") or "").strip() and not str(v.get("visual_tag") or "").strip():
            return None
        return v

    def is_empty(self) -> bool:
        return not (self.camera.strip() or self.action.strip() or self.speaker)


def _sentence(text: str) -> str:
    t = clean_text(text).rstrip(" .")
    return f"{t[0].upper()}{t[1:]}." if t else ""


def _who(position: str, tag: str) -> str:
    """'On the right, the little girl in the pink dress' / 'The subject'."""
    pos, tag = clean_text(position).rstrip(" ,."), clean_text(tag).rstrip(" ,.")
    if pos and tag:
        return f"{pos[0].upper()}{pos[1:]}, {tag}"
    t = pos or tag
    return f"{t[0].upper()}{t[1:]}" if t else ""


# A listener's grin / laugh shows an open mouth, and Veo gives the line to a face it sees talking.
_OPEN_SMILE = [(re.compile(r"(?i)\bgrinning(?:\s+\w+ly)?\b"), "smiling with closed lips"),
               (re.compile(r"(?i)\bgrins?(?:\s+\w+ly)?\b"), "smiles with closed lips"),
               (re.compile(r"(?i)\blaughing(?:\s+\w+ly)?\b"), "smiling with closed lips"),
               (re.compile(r"(?i)\blaughs?(?:\s+\w+ly)?\b"), "smiles with closed lips")]


def _silent(action: str) -> str:
    a = _frame_words(clean_text(action).rstrip(" ."))
    for pat, rep in _OPEN_SMILE:
        a = pat.sub(rep, a)
    # an older wording of the lock is replaced by the eyeline guide's one
    a = re.sub(r"(?i),?\s*(?:keeps (?:her|his|their)? ?lips completely closed|listening silently|without speaking)", "", a).strip(" ,")
    if re.search(r"(?i)silent with lips closed", a):
        return a
    a = re.sub(r"(?i),?\s*(?:with\s+)?(?:her|his|their)?\s*(?:lips (?:completely )?closed|closed lips)\b", "", a).strip(" ,")
    lock = SILENT_BINDING
    # no "nodding slightly" when the head already moves, or for a listener who disagrees (a nod would say yes)
    if re.search(r"(?i)\b(?:head|nods?|nodding|shak(?:e|es|ing)|frown\w*|skeptic\w*|sceptic\w*|glar\w*|scowl\w*|"
                 r"defensive\w*|angr\w*|annoyed|doubt\w*|folds?|folding|cross(?:es|ing)? (?:her|his|their) arms)\b", a):
        lock = "remaining completely silent with lips closed"
    return f"{a}, {lock}" if a else lock


# --- eyeline lock (dialogue scenes) -------------------------------------------------------------------------

def frame_side(position: str | None) -> str:
    """'On the left' / 'frame left' / 'left' -> 'left'; right / center likewise; '' when unknown."""
    t = (position or "").lower()
    if re.search(r"\bleft\b", t):
        return "left"
    if re.search(r"\bright\b", t):
        return "right"
    if re.search(r"\b(?:center|centre|middle)\b", t):
        return "center"
    return ""


def _other(side: str) -> str:
    return {"left": "right", "right": "left"}.get(side, "")


def _level(text: str | None) -> str:
    t = (text or "").lower()
    return "slightly upward" if "up" in t else "slightly downward" if "down" in t else "at eye level"


def _head(text: str | None) -> str:
    return "side profile" if "side" in (text or "").lower() else "three-quarter profile"


def _tag(tag: str) -> str:
    return _lower_first(clean_text(tag).rstrip(" ,.")) or "the subject"


def _shot_block(shot: VideoShot) -> tuple[str, str]:
    """Block 1 (locked-off viewpoint + focal length) and the kind: 'single' | 'two' | 'group' | 'ots'."""
    cam = (shot.camera or "").lower()
    n = len(shot.listeners or [])
    if n == 1 and re.search(r"over[- ]the[- ]shoulder|\bots\b", cam):
        return "Static over-the-shoulder medium close-up, unmoving composition.", "ots"
    if n == 0:
        size = "close-up" if re.search(r"(?<!medium )close[- ]up", cam) and "medium" not in cam else "medium close-up"
        return f"Locked-off 85mm {size}, stationary viewpoint.", "single"
    if "wide" in cam or n >= 2:
        size = "wide shot" if "wide" in cam else "medium group shot"
        return f"Locked-off 50mm {size}, stationary viewpoint.", "group"
    return "Locked-off 50mm medium two-shot, stationary viewpoint.", "two"


def _gaze(sp: Speaker, shot: VideoShot, side: str) -> tuple[str, str]:
    """(where the speaker turns: 'left' | 'right', the screen-space gaze clause)."""
    level = _level(sp.gaze_level)
    g = clean_text(sp.gaze).lower()
    listeners = [x for x in shot.listeners or [] if clean_text(x.visual_tag)]
    target = None
    if g and not frame_side(g) and listeners:
        target = next((x for x in listeners if clean_text(x.visual_tag).lower() in g or g in clean_text(x.visual_tag).lower()), None)
    if g and target is None and frame_side(g) in ("left", "right") and "off" not in g:
        target = next((x for x in listeners if frame_side(x.position) == frame_side(g)), None)
    if g and target is None and frame_side(g) in ("left", "right"):
        d = frame_side(g)
        if "off" in g:
            return d, f"holds steady focus toward off-screen {d} {level}"
        return d, f"holds steady focus toward the {d} edge of the frame {level}"
    if target is None and listeners:
        target = listeners[0]
    if target is not None:
        where = frame_side(target.position) or _other(side) or "right"
        d = _other(side) or "right" if where == "center" else where  # a center partner: turn inward, say center
        return d, f"locks gaze onto {_tag(target.visual_tag)} on frame {where} {level}"
    d = _other(side) or "right"
    return d, f"holds steady focus toward the {d} edge of the frame {level}"


def _frame_words(t: str) -> str:
    """'toward the left' -> 'toward frame left' (a bare side is read as the frame's, the guide's coordinates)."""
    return re.sub(r"(?i)\b(toward|towards|to|at)\s+the\s+(left|right)\b(?!\s+(?:edge|of))", r"\1 frame \2", t)


def _gesture(text: str | None) -> str:
    """The delivery gesture without 'as she speaks' (the speech verb comes right after the gaze anchor)."""
    t = _frame_words(clean_text(text).rstrip(" ."))
    return re.sub(r"(?i),?\s*(?:as|while)\s+(?:he|she|they|the \w+)\s+speaks?\b", "", t).strip(" ,")


# Forbidden tokens of the eyeline guide: words about the lens / viewer pull the gaze into the lens, even negated.
_LENS_NEGATIONS = re.compile(
    r"(?i)\b(?:(?:do not|does not|never|avoid(?:s|ing)?|without|no)\s+(?:look(?:s|ing)?\s+(?:at|into|toward)|eye contact with|"
    r"glanc(?:e|es|ing)\s+at|break(?:ing)?)\s+(?:the\s+)?(?:camera|lens|viewer|audience|fourth wall))\b[^,.;]*")
_FORBIDDEN = [
    (re.compile(r"(?i)\b(?:looks?|looking|stares?|staring|gazes?|gazing)\s+(?:directly\s+|straight\s+)?(?:at|into|toward)\s+"
                r"(?:the\s+)?(?:camera|lens|viewer|audience)\b"), "turns to three-quarter profile"),
    (re.compile(r"(?i)\bturns?\s+away\s+from\s+(?:the\s+)?(?:camera|lens|viewer)\b"),
     "rotates 180 degrees showing only the back of head and shoulders"),
    (re.compile(r"(?i)\b(walks?|walking)\s+away\s+from\s+(?:the\s+)?(?:camera|lens|viewer)\b"),
     r"\1 off, rotating 180 degrees to show only the back of head and shoulders"),
    (re.compile(r"(?i)\b(?:dynamic|moving)\s+camera\b"), "steady viewpoint"),
    (re.compile(r"(?i)\bcinematic\s+(shots?)\b"), r"\1"),
    (re.compile(r"(?i)\bcinematic\s*,?\s*"), ""),
    (re.compile(r"(?i)\bthe\s+camera\b"), "the view"),
    (re.compile(r"(?i)\bcameras?\b"), "viewpoint"),
    (re.compile(r"(?i)\b(\d+\s*mm)\s+lens\b"), r"\1"),
    (re.compile(r"(?i)\b(wide[- ]angle|telephoto|macro|anamorphic|portrait)\s+lens\b"), r"\1 view"),
    (re.compile(r"(?i)\s*\blens(?:es)?\b"), ""),
    (re.compile(r"(?i)\s*\b(?:toward|at|to)\s+the\s+(?:viewer|audience)\b"), " toward frame center"),
    (re.compile(r"(?i)\s*\b(?:the\s+)?(?:viewer|audience|fourth wall)s?\b"), ""),
    (re.compile(r"(?i)\b(his|her|their)\s+eyes\s+(?:look|looks|stare|stares|are fixed|fix|fixes)\s+(on|at|toward)\b"),
     r"\1 gaze rests \2"),
]


def veo_safe(text: str) -> str:
    """Remove / replace the eyeline guide's forbidden tokens (camera, lens, viewer, audience, fourth wall,
    cinematic, 'do not look at the camera' ...)."""
    t = _LENS_NEGATIONS.sub("stays in three-quarter profile", text)
    for pat, rep_ in _FORBIDDEN:
        t = pat.sub(rep_, t)
    t = re.sub(r"[ \t]+([,.;:])", r"\1", t)
    t = re.sub(r"([,;])\s*([.;])", r"\2", t)
    t = re.sub(r"\.\s*\.", ".", t)
    t = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)  # a word dropped at a start
    return re.sub(r"[ \t]{2,}", " ", t).strip()


BODY_RELATIVE = re.compile(r"(?i)\b(?:to|toward|towards|at|on)\s+(?:his|her|their)\s+(?:left|right)\b|"
                           r"\b(?:his|her|their)\s+(?:left|right)(?!\s+(?:hand|arm|leg|foot|shoulder|side|cheek|ear|eye))\b")


def first_frame_staging(shot: VideoShot) -> str:
    """Eyeline guide, I2V first frame: the speaker already turned in profile toward the partner, every mouth closed
    ('' when the scene has no speaker)."""
    sp = shot.speaker
    if not sp or not clean_line(sp.line):
        return ""
    side = frame_side(sp.position)
    d, _ = _gaze(sp, shot, side)
    where = f"positioned on frame {side} " if side in ("left", "right", "center") else ""
    return (f"Staging for the dialogue: {_tag(sp.visual_tag)} {where}in {_head(sp.head_angle)}, head turned toward "
            f"frame {d}, gaze held there {_level(sp.gaze_level)}; everyone in the frame with neutral closed lips.")


def _a(phrase: str) -> str:
    return ("an " if phrase[:1].lower() in "aeiou" else "a ") + phrase


def _cap(t: str) -> str:
    return t[0].upper() + t[1:] if t else t


def _end(line: str) -> str:
    """The line with its own ! ? … kept, else a full stop."""
    return line if line[-1:] in "!?…" else f"{line}."


def _with_env(act: str, env: str) -> str:
    """'action, while environment' — but never a second 'while', and nothing when the environment repeats the action."""
    if not env:
        return act
    if not act:
        return env
    if _lower_first(env)[:40] in act.lower():
        return act
    return f"{act}. {_cap(env)}" if re.search(r"(?i)\bwhile\b", act) else f"{act}, while {_lower_first(env)}"


def _audio_line(sound: str) -> str:
    return f"Audio: {_cap(sound or DEFAULT_SOUND)}. {CLOSING}"


def environment(text: str | None) -> str:
    """Secondary motion only: the light / frame continuity clause the start frame already holds is dropped."""
    return clean_text(_FRAME_MATCH.sub("", str(text or ""))).rstrip(" .")


def active_people(action: str | None) -> int:
    """How many people the action sentence-starts with a position anchor (each one acts)."""
    return len(_ANCHOR.findall(clean_text(action)))


# Review 1: diffusion models merge "identical" look-alikes into one body; siblings are told apart by hair / clothes.
_IDENTICAL = [
    (re.compile(r"(?i)\bidentical\s+twin\s+(?:sister|brother)?\s*(?:to|of)\s+"), "sister of "),
    (re.compile(r"(?i)\b(two|the)\s+identical\s+twin\s+(girls|boys|sisters|brothers|children|kids)\b"), r"\1 \2 who look alike"),
    (re.compile(r"(?i)\bidentical\s+(twins?|sisters?|brothers?|siblings?|girls|boys)\b"), r"\1 who look alike"),
    (re.compile(r"(?i)\b(looks?|looking)\s+(?:exactly\s+)?identical\b"), r"\1 alike"),
    (re.compile(r"(?i)\b(?:exact|perfect)\s+(?:copy|copies|clone|clones|duplicate)s?\s+of\b"), "similar to"),
    (re.compile(r"(?i)\b(?:the\s+)?(?:exact\s+)?same\s+((?:\w+\s+)?face)\b"), r"a similar \1"),
]


# Google's child-safety filter blocks pictures of children described with an exact age ("age 8", "8-year-old"),
# above all as a full-body shot on a plain studio background. A minor's age becomes "young" (adults keep theirs).
_AGE = re.compile(r"(?i)\b(?:aged?\s+(\d{1,2})\b|(\d{1,2})\s*[- ]\s*(?:years?|yrs?)\s*[- ]\s*old\b|(\d{1,2})\s+years?\s+of\s+age\b)")


def child_safe(text: str) -> str:
    """Wording Google's child-safety filter lets through (tested on Flow: "Vietnamese girl, age 8, round cheeks"
    was blocked, "A cheerful Vietnamese schoolgirl ..." passed): a minor's exact age is dropped, girl / boy become
    schoolgirl / schoolboy, and cheek details go. Adults keep their age."""
    minor = False

    def sub(m: re.Match) -> str:
        nonlocal minor
        n = int(next(g for g in m.groups() if g))
        if n >= 18:
            return m.group(0)
        minor = True
        return "young"
    t = _AGE.sub(sub, text)
    if re.search(r"(?i)\b(?:school(?:girl|boy)|little (?:girl|boy)|child|kid|toddler)\b", t) or minor:
        # tested on Flow: the same child prompt passed without "lively expression" and was blocked with it
        t = re.sub(r"(?i),?\s*\b(?:lively|neutral|playful|innocent|cute|sweet|bright|blank)\s+expression\b", "", t)
    if minor:
        t = re.sub(r"(?i)\b(girl|boy)\b", lambda m: ("S" if m.group(0)[0].isupper() else "s") + "chool" + m.group(1).lower(), t)
        t = re.sub(r"(?i),\s*young\b", "", t)
    # tested on Flow: "A little Vietnamese girl ..." blocked three times, "A cheerful Vietnamese schoolgirl ..." passed
    t = re.sub(r"(?i)\b(?:little|young|small)\s+((?:[A-Z][\w-]*\s+)?)(girl|boy)\b",
               lambda m: f"{m.group(1)}school{m.group(2).lower()}", t)
    t = re.sub(r"(?i)\b(?:round|chubby|rosy|plump|soft)\s+cheeks\b,?\s*", "", t)
    if re.search(r"(?i)\bschool(?:girl|boy)\b|\b(?:child|kid|toddler|baby)\b", t):  # body words on a child
        t = re.sub(r"(?i)\b(?:round\s+)?(?:chubby|plump|pudgy)\s+(face|cheeks|arms|legs|body)\b", r"round \1", t)
        t = re.sub(r"(?i)\b(?:chubby|plump|pudgy),?\s*", "", t)
    t = re.sub(r"(?i)\ban young\b", lambda m: "A young" if m.group(0)[0] == "A" else "a young", t)
    t = re.sub(r"(?i)\byoung,?\s+young\b", "young", t)
    return re.sub(r"\s+([,.])", r"\1", t)


def soften_identical(text: str) -> str:
    """'two identical twin girls' -> 'two girls who look alike' (still pictures of the basic formats)."""
    for pat, rep in _IDENTICAL:
        text = pat.sub(rep, text)
    return text


def _lower_first(t: str) -> str:
    return t[0].lower() + t[1:] if t and not t[:2].isupper() else t


def assemble(shot: VideoShot, *, audio_mode: str | None, language: str | None, narration: str | None = None) -> str:
    """The prompt sent to Flow, from the guide's templates.

    audio_mode "dialogue" with a speaker -> template 2 (one speaker, silent binding for the others);
    "voiceover" with narration -> template 2 with a narrator and everyone on screen silent;
    anything else -> template 1 (the silence said by real sounds, no negatives)."""
    camera = _sentence(shot.camera)
    env = environment(shot.environment)
    sound = clean_text(shot.audio).rstrip(" .")
    lang = f" in {language}" if language else ""
    sp = shot.speaker
    if audio_mode == "dialogue" and sp and clean_line(sp.line):
        return veo_safe(_dialogue(shot, sp, language=language, env=env, sound=sound))
    if audio_mode == "voiceover" and clean_line(narration):
        voice_style = clean_text(sp.voice_style).rstrip(" .") if sp else ""
        head = (f"A narrator voice-over speaks{lang} in {_a(voice_style or 'calm, warm storytelling')} voice: "
                f"{_end(clean_line(narration))} {NO_TEXT_SHORT}")
        act = clean_text(shot.action).rstrip(" .")
        body = _with_env(act, env)
        parts = [p for p in (camera, _sentence(body), "Everyone in the frame keeps lips closed.") if p]
        return veo_safe(f"{head}\nVisual: {' '.join(parts)}\n{_audio_line(sound)}")
    act = clean_text(shot.action).rstrip(" .")
    body = _with_env(act, env)
    return veo_safe(" ".join(p for p in (camera, _sentence(body), _audio_line(sound)) if p))


def _dialogue(shot: VideoShot, sp: Speaker, *, language: str | None, env: str, sound: str) -> str:
    """The eyeline guide's 5 blocks: [1 locked-off viewpoint + focal length] [2 identity + frame position]
    [3 head angle + screen-space gaze anchor] [4 speech verb, tone, line] [5 lip lock + listeners]."""
    block1, kind = _shot_block(shot)
    side = frame_side(sp.position)
    tag = _tag(sp.visual_tag)
    d, gaze = _gaze(sp, shot, side)
    style = clean_text(sp.voice_style).rstrip(" .")
    lang = f" in {language}" if language else ""
    speech = f"speaking{lang} in {_a(style)} voice" if style else f"speaking{lang}"
    line = _end(clean_line(sp.line))
    gesture = _gesture(sp.action)
    listeners = [x for x in shot.listeners or [] if clean_text(x.visual_tag)]
    out: list[str] = []
    if kind == "ots":
        lst = listeners[0]
        lside = frame_side(lst.position) or _other(side) or "left"
        out.append(f"{block1} Softly out-of-focus in foreground {lside} is the shoulder of {_tag(lst.visual_tag)} seen "
                   "from behind; exactly two people present in the scene.")
        pos = f"Positioned on frame {side or _other(lside)} facing forward-{lside}"
        out.append(f"{pos}, {tag} {gesture + ', then ' if gesture else ''}tilts head {_level(sp.gaze_level)} and locks gaze "
                   f"onto the position of {_tag(lst.visual_tag)} at foreground {lside}, {speech}: {line} {NO_TEXT_SHORT}")
        out.append(f"After finishing the line, {tag} closes mouth while maintaining that exact eyeline.")
    else:
        if kind == "two":
            out.append("Characters face and look only at each other throughout the scene.")
        out.append(block1)
        pos = f"Positioned on frame {side} in {_head(sp.head_angle)}, " if side else f"In {_head(sp.head_angle)}, "
        out.append(f"{pos}{tag} {gesture + ', then ' if gesture else ''}turns head toward frame {d} and {gaze}, "
                   f"{speech}: {line} {NO_TEXT_SHORT}")
        out.append(f"After finishing the line, {tag} closes mouth while maintaining that exact eyeline.")
        for lst in listeners:
            lside = frame_side(lst.position)
            where = f"On frame {lside}, " if lside else ""
            # every listener faces the speaker: toward the speaker's side, or toward the center from a side
            if side in ("left", "right") and lside != side:
                toward = side
            elif lside in ("left", "right"):
                toward = _other(lside)
            else:
                toward = _other(d)
            many = re.search(r"(?i)\b(?:two|three|four|both|children|kids|people|family|girls|boys|twins|sisters|brothers)\b",
                             clean_text(lst.visual_tag))
            faces = f" face{'' if many else 's'} frame {toward}," if toward else ""
            out.append(_sentence(f"{where}{_tag(lst.visual_tag)}{faces} {_silent(lst.silent_action)}"))
    if env:
        out.append(_sentence(env))
    out.append(_audio_line(sound))
    return " ".join(p for p in out if p)


def axis_breaks(shots: list[tuple[str, VideoShot]]) -> dict[str, str]:
    """Eyeline guide check 5, the 180-degree axis: in consecutive dialogue scenes with different speakers (shot /
    reverse shot), one speaker is on frame left looking right and the other on frame right looking left.
    {scene id: why} for the second scene of a pair that breaks it."""
    out: dict[str, str] = {}
    prev: tuple[str, Speaker, str, str] | None = None  # (id, speaker, side, gaze direction)
    for sid, shot in shots:
        sp = shot.speaker
        if not sp or not clean_line(sp.line):
            prev = None
            continue
        side = frame_side(sp.position)
        d, _ = _gaze(sp, shot, side)
        if prev is not None and clean_text(sp.visual_tag).lower() != clean_text(prev[1].visual_tag).lower():
            if side in ("left", "right") and side == prev[2]:
                out[sid] = (f"speaker on frame {side} like the previous speaker ({prev[0]}): the reply must be on frame "
                            f"{_other(side)}")
            elif d == prev[3]:
                out[sid] = (f"speaker looks toward frame {d} like the previous speaker ({prev[0]}): the reply must "
                            f"look toward frame {_other(d)}")
        prev = (sid, sp, side, d)
    return out


def warnings(shot: VideoShot, *, audio_mode: str | None, narration: str | None, names: list[str]) -> list[str]:
    """What breaks the guide (Vietnamese, for the editor and `filmmaker check`). Quotes, contractions, technical
    numbers and silent binding are fixed by `assemble`, so only what code cannot fix is reported."""
    out: list[str] = []
    if not clean_text(shot.camera):
        out.append("Thiếu máy quay (cỡ cảnh + một chuyển động máy).")
    if not clean_text(shot.action) and not (shot.speaker and clean_text(shot.speaker.action)):
        out.append("Thiếu hành động (1–2 nhịp hành động nhìn thấy được).")
    sp = shot.speaker
    line = clean_line(sp.line) if sp else ""
    if audio_mode == "dialogue" and sp and line:
        if word_count(line) > MAX_WORDS:
            out.append(f"Câu thoại {word_count(line)} từ (tối đa {MAX_WORDS}): hãy rút gọn hoặc tách sang cảnh khác.")
        if not clean_text(sp.visual_tag):
            out.append("Người nói thiếu đặc điểm nhận dạng (vị trí + ngoại hình).")
        if len(shot.listeners or []) and not clean_text(sp.position):
            out.append("Có nhiều người trong khung hình: người nói cần vị trí (ví dụ On the right).")
    if audio_mode == "voiceover" and clean_line(narration) and word_count(clean_line(narration)) > MAX_WORDS:
        out.append(f"Lời thuyết minh {word_count(clean_line(narration))} từ (tối đa {MAX_WORDS}).")
    many = bool(sp and shot.listeners)
    talking = audio_mode == "dialogue" and bool(sp and line)  # then `action` is not sent (speaker + listeners are)
    if not talking and active_people(shot.action) >= 2:
        out.append("Nhiều người cùng hành động trong một clip: để 1 người chủ động, người kia chỉ phản ứng nhẹ "
                   "(tay + đồ vật nhỏ dễ biến dạng).")
    if talking and re.search(r"(?i)\b(?:gasps?|giggles?|giggling|laugh(?:s|ter|ing)?|cheers?|chatter|murmurs?|screams?|squeals?)\b",
                             shot.audio or ""):
        out.append("Cảnh có người nói mà âm thanh có tiếng người khác (cười, thở hắt…): Veo có thể giao câu thoại nhầm người.")
    if re.search(r"(?i)\b(?:no|without)\s+(?:music|dialogue|speech|voices?|talking)\b", shot.audio or ""):
        out.append("Âm thanh có câu phủ định (no music / no dialogue): hãy tả im lặng bằng âm thanh thật.")
    beats = len(re.findall(r"\b(?:then|before|after that|afterwards|finally)\b", shot.action or "", re.IGNORECASE))
    if beats >= 2:
        out.append("Hành động có vẻ quá nhiều nhịp: giữ 1–2 hành động mỗi clip để mặt không biến dạng.")
    texts = [shot.camera, shot.action, shot.environment, sp.visual_tag if sp else "", sp.action if sp else "",
             *[f"{x.visual_tag} {x.silent_action}" for x in shot.listeners or []]]
    blob = " ".join(t or "" for t in texts)
    used = [n for n in names if n.strip() and re.search(rf"\b{re.escape(n.strip())}\b", blob, re.IGNORECASE)]
    if used:
        out.append(f"Prompt video gọi tên riêng ({', '.join(used)}): Veo chỉ thấy ảnh, hãy dùng vị trí + ngoại hình.")
    if talking:
        said = " ".join(t or "" for t in (sp.action, sp.gaze, *[x.silent_action for x in shot.listeners or []]))
        if BODY_RELATIVE.search(said):
            out.append("Hướng nhìn theo cơ thể nhân vật (his/her left/right): hãy dùng tọa độ khung hình "
                       "(frame left/right, off-screen left/right).")
        if re.search(r"(?i)\beyes\b", said):
            out.append("Cảnh thoại nên tả ánh nhìn bằng gaze/focus thay vì “eyes” (dễ làm mắt nhìn thẳng ống kính).")
        if not frame_side(sp.position):
            out.append("Người nói chưa có vị trí trên khung hình (frame left / frame right).")
    if many and any(re.search(r"(?:^|[.;]\s*)(?:he|she)\b", (t or "").strip(), re.IGNORECASE)
                    for t in (shot.action, sp.visual_tag if sp else "", *[x.visual_tag for x in shot.listeners or []])):
        out.append("Cảnh nhiều người: tránh he/she mơ hồ, dùng vị trí + ngoại hình.")
    return out
