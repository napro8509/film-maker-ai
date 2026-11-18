"""Film-making rules in one place: the limits and the fixed sentences the code adds to prompts.

Before this module the same rule was written in several files (planner, storyboard, i2v_prompt, formats,
reviewer, qc). Everything here is used by those modules through an import, so changing a rule here changes it
everywhere. The texts are sent as they are to Flow / Veo / Muse / the planner LLM: change a word only on purpose.

`RULES` lists every rule with its topic, why it exists (the failure that led to it) and where it is applied;
`filmmaker rules` prints it. See docs/quy-tac-hien-tai.md for the rules still written inside the long LLM prompts
(planner SYSTEM, basic_planner SYSTEM, reviewer, qc) and the open contradictions.

Standalone scripts that run with gflow-cli's Python (flow_ui.py, muse_ui.py) cannot import this module: they keep a
copy, and tests/test_rules.py checks the copies match.

Stdlib only: imported by storyboard / i2v_prompt / formats, so it must not import anything from filmmaker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- limits ----------------------------------------------------------------------------------------------------

#: Words in one spoken line / one narration or caption (I2V prompt guide rule 4).
MAX_WORDS = 12
#: Dialogue lines in one scene (a clip is only a few seconds).
DIALOGUE_MAX = 12
#: Words of a character description used to tell who is who in a dialogue scene.
SPEAKER_LOOK_WORDS = 40
#: Reference images sent with one generation (Flow accepts only a few).
MAX_REFS = 3
#: Characters attached to a clip that starts from a picture (keyframe / i2v): Flow takes 3 images in all on Veo
#: Lite, and the start picture is one of them.
START_FRAME_CHARS = MAX_REFS - 1
#: First frame of a clip vs its start picture: the right clip scores ~0.99, another scene's picture <= 0.45.
#: Below this the clip was made from another picture (flow_ui keeps a copy: START_MATCH_MIN).
START_MATCH_MIN = 0.75

# --- on-screen text --------------------------------------------------------------------------------------------

#: Added to every prompt of the short-drama flow: models like to burn dialogue / titles into the picture.
NO_TEXT = ("No captions, no subtitles, no on-screen text or lettering, no title cards, no watermarks, no logos; "
           "any dialogue is spoken audio only.")
#: Still pictures of the basic formats: the short form of NO_TEXT (no long negative, no "dialogue").
NO_TEXT_IMAGE = "No on-screen text, letters or watermarks."
#: Right after a spoken line / voice-over in the structured I2V templates.
NO_TEXT_SHORT = "(no subtitles, no captions, no on-screen text)"

# --- who speaks ------------------------------------------------------------------------------------------------

#: Spoken language of a short-drama video prompt (never image prompts or fingerprints). Says the language of the
#: lines, never that "characters speak": "characters speak only Thai" read as everyone talking, and listeners
#: mouthed or echoed the speaker's line.
DIALOGUE_RULE = ("Spoken language: {lang}. Each line is said in {lang} only by the character it belongs to; "
                 "nobody else repeats it, mouths it or talks along.")
#: The sentence used before; prompts that still carry it get DIALOGUE_RULE instead.
OLD_DIALOGUE_RULE = re.compile(r"All spoken dialogue is in [^;.]+; characters speak only [^.]+\.?")
#: Before the lines of a short-drama scene.
DIALOGUE_TURNS = ("Each line is spoken exactly once, only by the character named before it; no character repeats, "
                  "echoes or says another character's line, and they never speak at the same time.")
#: Ambient "chatter" or a cheering crowd made Veo give part of the line to a listener (s05: the coach mouthed the
#: boy's "Thầy ơi…"): the background makes sounds, never words.
BACKGROUND_NO_WORDS = ("Nobody else says any words: people in the background only cheer, laugh or murmur "
                       "without words, and the line is never shared, echoed or finished by anyone else.")
#: Sound rule of the basic formats with dialogue (formats.AUDIO["dialogue"]["rule"]).
AUDIO_DIALOGUE_RULE = "Spoken language: {lang}. Only one person speaks; everyone else keeps lips closed."
#: Listeners in the structured I2V templates (eyeline guide, token map).
SILENT_BINDING = "remaining completely silent with lips closed, nodding slightly"

# --- silence ---------------------------------------------------------------------------------------------------

#: Short-drama film set to "Không thoại". No "music": every clip is generated alone, so Veo composed different
#: music for each and the score jumped at every cut; one score is added for the whole film when editing.
NO_DIALOGUE_RULE = "No spoken dialogue: characters do not talk; only ambient sound and sound effects."
#: The sentence used before; prompts that still carry it get NO_DIALOGUE_RULE instead.
OLD_NO_DIALOGUE_RULE = "No spoken dialogue: characters do not talk; only ambient sound, sound effects and music."
#: Short-drama scene without lines in a film with dialogue. Sending the language rule there made Veo invent lines
#: in half of such scenes (a mother saying "it's ok, I'm here" in a scene meant to be silent).
SILENT_SCENE_RULE = ("Nobody speaks in this scene: no dialogue, no words, no voice-over; the characters stay silent "
                     "(breathing, sighs, crying, laughing and wordless humming are fine); background voices, if any, "
                     "are an indistinct murmur; "
                     "only ambient sound and sound effects.")
#: The same for the structured I2V prompts of the basic formats, said positively (those templates avoid
#: negatives). Veo made characters speak English in about half the scenes of a Thai film (2026-11-15).
I2V_SILENT_RULE = ("Everyone in the frame keeps lips closed and stays wordless the whole time; the only human sounds "
                   "are breathing, sighs or soft crying.")
#: Basic formats without speech (silent / on-screen text / music). Said with what IS heard: "no music" made Veo
#: add odd music.
SILENT_RULE = "Nobody speaks; the sound is only ambient sound and sound effects."
#: The sentence used before; saved prompts get SILENT_RULE instead.
OLD_SILENT_RULE = "No dialogue, no music."
#: Basic formats with a narrator.
VOICEOVER_RULE = ("Voice-over: one calm narrator's voice speaks in {lang}; the characters on screen do not talk "
                  "and their lips do not move.")

# --- shot and sound --------------------------------------------------------------------------------------------

#: Last sentence of every structured I2V prompt.
CLOSING = "Single continuous shot, no scene cuts."
#: Review 2: no "no dialogue, no music" (negatives made Veo add murmurs / odd music): silence is said with real
#: sounds.
DEFAULT_SOUND = "quiet ambient room tone"

# --- guidance shared by the LLM prompts ----------------------------------------------------------------------

#: Children: the clause after "...(Google's child-safety filter blocks many pictures of minors and the credits are
#: lost): " in the planner and storyboard-reviewer prompts. Indented for a "- " bullet.
CHILDREN_GUIDE = """never an
  exact age ("4 years old", "age 8", "10 months old") nor body details (chubby, round or rosy cheeks, plump): write
  "a toddler girl", "a schoolgirl", "a baby boy" and describe them by hair, clothes and what they do. Keep children
  fully and casually dressed in everyday activities, usually in medium or wide shots together with an adult; never
  a child in a bath, swimwear, undressing, in bed up close, asleep or limp, alone and frightened, crying hard, or an
  adult kissing, feeding or holding a child's face up close. Fear or shock in a child is played as surprise or
  playful worry. Give close-ups and the main spoken lines to adults or teens when the story allows."""


# --- catalogue -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    name: str  # constant in this module
    topic: str  # Vietnamese topic, as in docs/quy-tac-hien-tai.md
    kind: str  # "giới hạn" (a number), "chèn" (added to prompts), "dặn LLM" (planner / reviewer guidance)
    why: str  # Vietnamese: what went wrong without it
    used_in: tuple[str, ...]  # where it is applied

    @property
    def value(self) -> object:
        v = globals()[self.name]
        return v.pattern if isinstance(v, re.Pattern) else v


RULES: tuple[Rule, ...] = (
    Rule("MAX_WORDS", "Thoại", "giới hạn", "Câu dài thì Veo nói không hết trong clip, khớp môi kém",
         ("i2v_prompt.warnings", "basic_planner.problems")),
    Rule("DIALOGUE_MAX", "Thoại", "giới hạn", "Một clip chỉ vài giây", ("storyboard.Scene",)),
    Rule("SPEAKER_LOOK_WORDS", "Khớp môi", "giới hạn", "Đủ để Veo nhận ra ai là ai", ("storyboard.speaker_cues",)),
    Rule("MAX_REFS", "Nhân vật & tham chiếu", "giới hạn", "Flow chỉ nhận vài ảnh tham chiếu",
         ("storyboard", "pipeline")),
    Rule("START_FRAME_CHARS", "Nhân vật & tham chiếu", "giới hạn",
         "Veo Lite nhận 3 ảnh, ảnh mở đầu chiếm 1; quá số thì Flow hỏi chế độ và treo",
         ("storyboard.start_frame_char_refs",)),
    Rule("START_MATCH_MIN", "Thử lại & QC", "giới hạn", "Dưới ngưỡng là clip làm từ ảnh khác",
         ("qc.check_scene", "flow_ui (bản sao)")),
    Rule("NO_TEXT", "Chữ trên hình", "chèn", "Model hay in thoại / tiêu đề lên hình", ("storyboard.for_flow (drama)",)),
    Rule("NO_TEXT_IMAGE", "Chữ trên hình", "chèn", "Bản ngắn, không phủ định dài", ("storyboard.for_flow (basic)",)),
    Rule("NO_TEXT_SHORT", "Chữ trên hình", "chèn", "Veo in câu thoại thành phụ đề", ("i2v_prompt.assemble",)),
    Rule("DIALOGUE_RULE", "Ngôn ngữ", "chèn", "“characters speak only Thai” làm người nghe mấp máy theo",
         ("storyboard.dialogue_rule (drama)",)),
    Rule("OLD_DIALOGUE_RULE", "Ngôn ngữ", "chèn", "Câu cũ, prompt đã lưu được thay bằng DIALOGUE_RULE",
         ("storyboard.for_flow_video",)),
    Rule("DIALOGUE_TURNS", "Thoại", "chèn", "Người nghe lặp / nói thay câu của người nói",
         ("storyboard.performance (drama)",)),
    Rule("BACKGROUND_NO_WORDS", "Khớp môi", "chèn", "Ông thầy mấp máy câu “Thầy ơi…” của cậu bé (s05)",
         ("storyboard.speaker_cues",)),
    Rule("AUDIO_DIALOGUE_RULE", "Thoại", "chèn", "Một người nói / cảnh", ("formats.AUDIO['dialogue']",)),
    Rule("SILENT_BINDING", "Khớp môi", "chèn", "Người nghe cũng mấp máy môi", ("i2v_prompt.assemble",)),
    Rule("NO_DIALOGUE_RULE", "Âm thanh", "chèn", "Veo tự sáng tác nhạc khác nhau cho mỗi clip",
         ("storyboard.dialogue_rule (drama, Không thoại)",)),
    Rule("OLD_NO_DIALOGUE_RULE", "Âm thanh", "chèn", "Câu cũ, prompt đã lưu được thay", ("storyboard.for_flow_video",)),
    Rule("SILENT_SCENE_RULE", "Âm thanh", "chèn", "Veo bịa thoại ở ~½ số cảnh không thoại",
         ("storyboard.for_flow_video (drama)",)),
    Rule("I2V_SILENT_RULE", "Âm thanh", "chèn", "Veo cho nhân vật nói tiếng Anh ở ~½ số cảnh của phim Thái",
         ("storyboard._silent_shot (basic)",)),
    Rule("SILENT_RULE", "Âm thanh", "chèn", "“no music” làm Veo thêm nhạc lạ", ("formats.AUDIO",)),
    Rule("OLD_SILENT_RULE", "Âm thanh", "chèn", "Câu cũ, prompt đã lưu được thay", ("storyboard.for_flow_video",)),
    Rule("VOICEOVER_RULE", "Âm thanh", "chèn", "Nhân vật không mấp máy khi có thuyết minh",
         ("formats.AUDIO['voiceover']",)),
    Rule("CLOSING", "Máy quay", "chèn", "Veo tự cắt cảnh trong một clip",
         ("i2v_prompt.assemble", "storyboard._silent_shot")),
    Rule("DEFAULT_SOUND", "Âm thanh", "chèn", "Câu phủ định làm Veo thêm tiếng rì rầm / nhạc", ("i2v_prompt.assemble",)),
    Rule("CHILDREN_GUIDE", "Chính sách & trẻ em", "dặn LLM", "Bộ lọc trẻ em của Google chặn ảnh, mất credit",
         ("planner.SYSTEM", "reviewer.STORYBOARD_SYSTEM")),
)


def catalogue() -> str:
    """Every rule, grouped by topic, as plain text (for `filmmaker rules`)."""
    lines: list[str] = []
    for topic in dict.fromkeys(r.topic for r in RULES):
        lines.append(f"## {topic}")
        for r in (r for r in RULES if r.topic == topic):
            v = str(r.value)
            short = v if len(v) <= 110 else v[:107] + "..."
            lines.append(f"- {r.name} [{r.kind}] = {short}")
            lines.append(f"    vì: {r.why} · dùng ở: {', '.join(r.used_in)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- per-film rule sets for image and video prompts --------------------------------------------------------------
#
# What the "Quy tắc prompt" popup edits (Storyboard.prompt_rules = {"image": {...}, "video": {...}}). A rule that does
# not clash with any other is a Toggle (on by default); rules that cannot all hold at once are one Choice (radio):
# "auto" keeps what each film format did before (short drama vs the basic formats). Only values that differ from the
# default are stored. The prompt helpers in storyboard.py / i2v_prompt.py read them through Storyboard.rule().
# Fingerprints are always computed with the defaults, so changing a rule never marks finished scenes for
# re-rendering: it applies to what is made next.

FLOWS = ("drama", "basic")


@dataclass(frozen=True)
class Toggle:
    id: str
    label: str  # Vietnamese, shown in the popup
    help: str  # Vietnamese: what it adds / why
    flows: tuple[str, ...] = FLOWS  # film flows it applies to
    default: bool = True


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    help: str
    options: tuple[tuple[str, str, str], ...]  # (value, label, help)
    flows: tuple[str, ...] = FLOWS
    default: str = "auto"


_NO_TEXT_OPTIONS = (
    ("auto", "Theo định dạng phim", "Phim drama: câu dài; các định dạng khác: câu ngắn."),
    ("long", "Câu dài", "Liệt kê đủ: không caption, phụ đề, chữ, title card, watermark, logo."),
    ("short", "Câu ngắn", "Một câu ngắn, ít phủ định (Veo dễ hiểu hơn)."),
    ("off", "Không thêm", "Không nhắc gì về chữ trên hình."),
)

IMAGE_RULES: tuple[Toggle | Choice, ...] = (
    Choice("no_text", "Chữ trên ảnh", "Model ảnh hay tự vẽ chữ, phụ đề, watermark lên hình.", _NO_TEXT_OPTIONS),
    Choice("staging", "Dàn cảnh khung đầu của cảnh thoại",
           "Veo giao câu thoại cho khuôn mặt nó thấy rõ nhất trong ảnh mở đầu.",
           (("auto", "Theo định dạng phim", "Drama: người nói nhìn về camera; định dạng khác: nghiêng 3/4."),
            ("camera", "Người nói nhìn về camera", "Mặt to nhất, chính diện hoặc 3/4 về camera, thấy rõ miệng."),
            ("profile", "Người nói nghiêng 3/4 về người nghe",
             "Không nhìn ống kính; mọi người môi khép tự nhiên."),
            ("off", "Không thêm", "Ảnh chỉ theo prompt của cảnh."))),
    Toggle("child_safe", "Viết lại mô tả trẻ em cho qua bộ lọc Google",
           "Bỏ tuổi chính xác và chi tiết cơ thể, girl → schoolgirl. Thiếu là dễ bị chặn, mất credit."),
    Toggle("portrait_everyday", "Ảnh chân dung kiểu cảnh đời thường",
           "Không chụp studio toàn thân (bộ lọc trẻ em chặn). Drama: chỉ áp cho trẻ em; định dạng khác: mọi nhân vật "
           "chưa có prompt chân dung riêng."),
    Toggle("no_identical", "Tránh “sinh đôi giống hệt”",
           "“identical twins / same face” → “trông giống nhau”: model gộp hai người giống hệt thành một thân."),
    Toggle("strip_technical", "Bỏ tỉ lệ khung, thời lượng, độ phân giải khỏi prompt",
           "Các chữ như 9:16, 8 seconds, 4K làm loãng prompt ảnh.", flows=("basic",)),
    Toggle("looks", "Thêm ngoại hình nhân vật có trong cảnh",
           "“Tên: mô tả” giữ khuôn mặt, tóc, áo đồng nhất giữa các cảnh."),
    Toggle("setting", "Thêm mô tả bối cảnh (Setting: …)", "Giữ căn phòng giống nhau từ cảnh này sang cảnh khác."),
    Toggle("style", "Thêm phong cách chung (Style: …)", "Phong cách hình ảnh của phim, ghi một lần cho mọi ảnh."),
    Toggle("camera", "Thêm cỡ cảnh, góc, ống kính vào ảnh", "Từ mục máy quay của cảnh.", flows=("drama",)),
    Toggle("content_direction", "Thêm chỉ đạo diễn xuất vào ảnh",
           "Content direction đặt biểu cảm, dáng người của khung mở đầu.", flows=("drama",)),
)

VIDEO_RULES: tuple[Toggle | Choice, ...] = (
    Choice("no_text", "Chữ trên video", "Veo hay in câu thoại thành phụ đề, nhất là khi có ngoặc kép.",
           _NO_TEXT_OPTIONS),
    Choice("facing", "Hướng mặt người nói",
           "Nhìn camera giúp Veo giao câu đúng người; nghiêng 3/4 tránh người nói nhìn chằm vào ống kính.",
           (("auto", "Theo định dạng phim", "Drama: quay mặt về camera; định dạng khác: nghiêng 3/4."),
            ("camera", "Quay mặt về camera", "Mặt và miệng thấy rõ, môi cử động theo lời."),
            ("profile", "Nghiêng 3/4 về người nghe", "Không nhìn ống kính; ánh nhìn theo tọa độ khung hình."))),
    Choice("silence", "Cảnh không có câu thoại (phim có thoại)",
           "Không dặn gì thì Veo hay bịa thoại (kể cả tiếng Anh) ở khoảng nửa số cảnh im lặng.",
           (("auto", "Theo định dạng phim", "Drama: cho phép cười, ngân nga; định dạng khác: chỉ thở, khóc nhẹ."),
            ("laugh", "Cho phép cười, ngân nga không lời", "Không ai nói; vẫn có thể cười, thở dài, khóc."),
            ("breath", "Chỉ thở, thở dài, khóc nhẹ", "Mọi người môi khép suốt cảnh."),
            ("off", "Không thêm", "Không dặn gì về im lặng."))),
    Toggle("language", "Chốt ngôn ngữ thoại",
           "“Spoken language: Thai…”: thiếu thì Veo đoán ngôn ngữ, hay ra tiếng Anh."),
    Toggle("who_is_who", "Ai là ai, chỉ người có thoại được nói",
           "Tả ngoại hình từng người, người nói mấp máy môi, người khác ngậm miệng. Veo chỉ thấy ảnh, không thấy tên.",
           flows=("drama",)),
    Toggle("background_no_words", "Hậu cảnh không nói thành lời",
           "Đám đông chỉ reo, cười, rì rầm; không ai nói tiếp câu của người nói.", flows=("drama",)),
    Toggle("dialogue_turns", "Mỗi câu nói một lần, đúng người, lần lượt",
           "Kèm “X stays silent” cho người không có thoại.", flows=("drama",)),
    Toggle("locked_camera", "Khoá máy quay trong cảnh thoại",
           "Thay máy quay của cảnh bằng cú máy đứng yên (85mm cận / 50mm hai người).", flows=("basic",)),
    Toggle("lens_words", "Xoá chữ camera, lens, viewer, cinematic",
           "Nhắc tới máy quay (kể cả câu phủ định) kéo ánh mắt người nói vào ống kính.", flows=("basic",)),
    Toggle("listener_silent", "Người nghe ngậm miệng",
           "“remaining completely silent with lips closed” cho mỗi người nghe.", flows=("basic",)),
    Toggle("closed_smile", "Người nghe chỉ cười mím",
           "Đổi grin / laugh của người nghe thành cười môi khép: miệng há thì Veo giao câu cho người đó.",
           flows=("basic",)),
    Toggle("lip_lock", "Người nói ngậm miệng sau khi nói xong", "Giữ nguyên hướng nhìn sau câu thoại.",
           flows=("basic",)),
    Toggle("default_sound", "Tả im lặng bằng âm thanh thật",
           "Cảnh chưa ghi âm thanh thì thêm “quiet ambient room tone” thay vì câu phủ định.", flows=("basic",)),
    Toggle("closing", "Một cú máy liền, không cắt cảnh", "“Single continuous shot, no scene cuts.” ở cuối prompt.",
           flows=("basic",)),
    Toggle("child_safe", "Viết lại mô tả trẻ em cho qua bộ lọc Google",
           "Bỏ tuổi chính xác và chi tiết cơ thể. Thiếu là dễ bị chặn, mất credit."),
    Toggle("looks", "Thêm ngoại hình nhân vật (video từ chữ)", "Chỉ cho cảnh không có ảnh mở đầu."),
    Toggle("setting", "Thêm mô tả bối cảnh (video từ chữ)", "Chỉ cho cảnh không có ảnh mở đầu."),
    Toggle("style", "Thêm phong cách chung (video từ chữ)", "Chỉ cho cảnh không có ảnh mở đầu."),
    Toggle("camera", "Thêm chuyển động máy quay", "Từ mục máy quay của cảnh.", flows=("drama",)),
)

RULE_SETS: dict[str, tuple[Toggle | Choice, ...]] = {"image": IMAGE_RULES, "video": VIDEO_RULES}


def rule_spec(kind: str, rule_id: str) -> Toggle | Choice:
    for r in RULE_SETS[kind]:
        if r.id == rule_id:
            return r
    raise KeyError(f"{kind}.{rule_id}")


def clean_prompt_rules(value: object) -> dict[str, dict[str, bool | str]] | None:
    """Stored form of Storyboard.prompt_rules: only known rules with a valid value that differs from the default
    ({} / None -> None). ValueError on anything else, so a typo never silently does nothing."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("prompt_rules phải là {image: {...}, video: {...}}")
    out: dict[str, dict[str, bool | str]] = {}
    for kind, vals in value.items():
        if kind not in RULE_SETS:
            raise ValueError(f"prompt_rules: không có bộ quy tắc “{kind}” (chỉ có image, video)")
        if vals is None:
            continue
        if not isinstance(vals, dict):
            raise ValueError(f"prompt_rules.{kind} phải là một bảng")
        for rid, v in vals.items():
            try:
                spec = rule_spec(kind, rid)
            except KeyError:
                raise ValueError(f"prompt_rules.{kind}: không có quy tắc “{rid}”") from None
            if isinstance(spec, Toggle):
                if not isinstance(v, bool):
                    raise ValueError(f"prompt_rules.{kind}.{rid} phải là true/false")
            elif v not in {o[0] for o in spec.options}:
                raise ValueError(f"prompt_rules.{kind}.{rid} phải là một trong: "
                                 f"{', '.join(o[0] for o in spec.options)}")
            if v != spec.default:
                out.setdefault(kind, {})[rid] = v
    return out or None


def rule_schema() -> dict[str, list[dict]]:
    """The two rule sets for the popup (web UI): toggles and choices with labels, help, flows and defaults."""
    def one(r: Toggle | Choice) -> dict:
        d = {"id": r.id, "label": r.label, "help": r.help, "flows": list(r.flows), "default": r.default,
             "type": "toggle" if isinstance(r, Toggle) else "choice"}
        if isinstance(r, Choice):
            d["options"] = [{"value": v, "label": lbl, "help": h} for v, lbl, h in r.options]
        return d
    return {kind: [one(r) for r in rs] for kind, rs in RULE_SETS.items()}
