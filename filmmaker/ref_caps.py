"""How many reference images gflow-cli accepts per generation (`--ref`), by model.

Google Flow's own CLI (`gflow image i2i` / `gflow video r2v`) refuses more than a model's
cap and exits with code 2 (e.g. "veo-lite allows at most 3 reference image(s); got 4.").

Characters are now made as Flow characters and attached BY NAME on the Flow chat composer
(flow_ui.py), where there is no such cap, so the pipeline drives the chat page whenever a
scene uses a Flow character or needs more references than the CLI model allows. Only when the
chat driver is unavailable does the pipeline fall back to the CLI; there it trims the extra
references to the model's cap (logging what it dropped) rather than crash the scene.

Source: Flow help + gflow-cli error messages (checked 2026-11-14). Keep in sync with the
copy hard-coded in tests/fake_gflow.py (that stand-in cannot import this package).
"""

from __future__ import annotations

# `gflow image i2i --ref` caps, per image model alias. Nano Banana 2 / Pro take up to 10; the
# lighter Imagen 4 and Nano Banana 2 Lite take 3.
IMAGE_REF_CAPS: dict[str, int] = {
    "nano2": 10,
    "nano-pro": 10,
    "nanopro": 10,
    "imagen4": 3,
    "nano2-lite": 3,
}
IMAGE_REF_DEFAULT = 3

# `gflow video r2v --ref` caps, per video model alias. The Veo 3.1 models take at most 3.
VIDEO_REF_CAPS: dict[str, int] = {
    "veo-lite": 3,
    "veo-lite-lp": 3,
    "veo-fast": 3,
    "veo-quality": 3,
}
VIDEO_REF_DEFAULT = 3


def image_cap(model: str | None) -> int:
    """Max `--ref` images `gflow image i2i` accepts for this image model."""
    return IMAGE_REF_CAPS.get(model or "", IMAGE_REF_DEFAULT)


def video_cap(model: str | None) -> int:
    """Max `--ref` images `gflow video r2v` accepts for this video model."""
    return VIDEO_REF_CAPS.get(model or "", VIDEO_REF_DEFAULT)
