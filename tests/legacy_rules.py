"""The prompt rules as they were before the 2026-11-18 research changes (docs/nghien-cuu-quy-tac.md): golden tests
run old storyboards with them, to prove these rules still send exactly what was recorded."""

LEGACY_RULES = {
    "image": {"numbered_refs": False, "setting_last": False},
    "video": {"ref_opener": False, "silence": "laugh"},
}

LEGACY_YAML = """
prompt_rules:
  image: {numbered_refs: false, setting_last: false}
  video: {ref_opener: false, silence: laugh}
"""

# A log line added by the research changes (more people in a keyframe than the image model keeps recognisable).
FACE_CAP_WARNING = "giữ giống khuôn mặt tốt nhất"
