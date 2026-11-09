"""Video model metadata: retirement dates, durations, credit prices (filmmaker/models.yaml).

The list of aliases still comes from `gflow models --json`; this only adds information.
Override file: $FILMMAKER_MODELS, else models.yaml next to the settings file. Both files are
read on every `load()` (no cache), so edits take effect immediately.

Invariant: `load()` and every `ModelMeta` method never raise, whatever the files contain. Wrong
parts of a file are dropped (the package value is used instead) and reported in `errors`.

Call the date helper as `model_meta.today()` (tests monkeypatch it), never `from ... import today`.
Does not depend on the web package.
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from . import settings as user_settings

if TYPE_CHECKING:
    from .storyboard import Storyboard

PACKAGE_FILE: Path = Path(__file__).with_name("models.yaml")
DEFAULT_VIDEO_MODEL = "veo-lite"
MIN_FALLBACK: dict[str, list[dict]] = {
    "video": [{"alias": "veo-lite", "max_duration": 8}],
    "image": [{"alias": "nano2", "label": "Nano Banana 2"}],
}
PRICE_UNITS = ("clip", "second")
_STR_FIELDS = ("plan_required", "note")
_DATE_FIELDS = ("retires", "checked")


def today() -> date:
    return date.today()


_real_today = today


def override_path() -> Path:
    env = os.environ.get("FILMMAKER_MODELS")
    if env:
        return Path(env)
    return user_settings.settings_path().parent / "models.yaml"


@dataclass
class ModelMeta:
    plan: str | None
    default_video_model: str
    fallback: dict[str, list[dict]]
    models: dict[str, dict]
    today: date
    package_ok: bool
    errors: list[str] = field(default_factory=list)
    # (file, message) pairs behind `errors`, so cli_warning can group by file.
    error_items: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def entry(self, alias: str | None) -> dict | None:
        if not isinstance(alias, str):
            return None
        return self.models.get(alias)

    def credits(self, alias: str | None) -> float | int | None:
        price = (self.entry(alias) or {}).get("price")
        if not isinstance(price, dict):
            return None
        if isinstance(self.plan, str) and self.plan in price and self.plan != "unit":
            return price[self.plan]
        return price.get("default")

    def price_unit(self, alias: str | None) -> str | None:
        price = (self.entry(alias) or {}).get("price")
        if not isinstance(price, dict):
            return None
        return price.get("unit") or "clip"

    def durations(self, alias: str | None) -> list[int] | None:
        d = (self.entry(alias) or {}).get("durations")
        return list(d) if d else None

    def retires(self, alias: str | None) -> date | None:
        d = (self.entry(alias) or {}).get("retires")
        return d if isinstance(d, date) else None

    def status(self, alias: str | None) -> str:
        d = self.retires(alias)
        if d is None:
            return "active"
        return "retired" if self.today >= d else "retiring"

    def is_old(self, alias: str | None) -> bool:
        return self.status(alias) in ("retiring", "retired")

    def retires_label(self, alias: str | None) -> str | None:
        d = self.retires(alias)
        return f"{d:%d/%m}" if d else None

    def info(self, alias: str | None) -> dict | None:
        e = self.entry(alias)
        if e is None:
            return None
        out = {k: _jsonable(v) for k, v in e.items()}
        out["credits"] = self.credits(alias)
        return out

    @property
    def error_text(self) -> str | None:
        return "; ".join(self.errors) if self.errors else None

    def _error(self, source: str, msg: str) -> None:
        self.error_items.append((source, msg))
        self.errors.append(f"{source}: {msg}")


def _jsonable(v: Any) -> Any:
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


# --- validation ---------------------------------------------------------------

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    """A finite int/float (inf/nan would break JSON responses)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _as_date(v: Any) -> tuple[bool, date | None]:
    if v is None:
        return True, None
    if isinstance(v, datetime):
        return True, v.date()
    if isinstance(v, date):
        return True, v
    if isinstance(v, str):
        try:
            return True, date.fromisoformat(v.strip())
        except ValueError:
            return False, None
    return False, None


def _sanitize_fallback_list(items: Any, where: str, errs: list[str]) -> list[dict] | None:
    if not isinstance(items, list):
        errs.append(f"{where} phải là danh sách")
        return None
    out: list[dict] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not isinstance(it.get("alias"), str) or not it["alias"]:
            errs.append(f"{where}[{i}] phải có alias (chuỗi)")
            continue
        if it.get("max_duration") is not None and not (_is_int(it["max_duration"]) and it["max_duration"] > 0):
            errs.append(f"{where}[{i}].max_duration phải là số nguyên")
            continue
        if it.get("label") is not None and not isinstance(it["label"], str):
            errs.append(f"{where}[{i}].label phải là chuỗi")
            continue
        out.append({k: v for k, v in it.items()
                    if isinstance(k, str) and (v is None or isinstance(v, (str, bool)) or _is_num(v))})
    return out


def _sanitize_price(price: Any, where: str, errs: list[str]) -> tuple[bool, dict | None]:
    if price is None:
        return True, None
    if not isinstance(price, dict):
        errs.append(f"{where} phải là dict hoặc để trống")
        return False, None
    unit = price.get("unit")
    if unit is None:
        unit = "clip"
    if unit not in PRICE_UNITS:
        errs.append(f"{where}.unit phải là clip hoặc second")
        return False, None
    out: dict[str, Any] = {"unit": unit}
    for k, v in price.items():
        if k == "unit":
            continue
        if not isinstance(k, str) or not _is_num(v):
            errs.append(f"{where}.{k} phải là số")
            continue
        if v < 0:
            errs.append(f"{where}.{k} không được âm")
            continue
        out[k] = v
    if len(out) == 1:  # no usable price left: keep the package price
        errs.append(f"{where} không còn giá hợp lệ")
        return False, None
    return True, out


def _sanitize_entry(alias: str, e: dict, errs: list[str]) -> dict:
    where = f"models.{alias}"
    out: dict[str, Any] = {}
    for k in _DATE_FIELDS:
        if k in e:
            ok, d = _as_date(e[k])
            if ok:
                out[k] = d
            else:
                errs.append(f"{where}.{k} không phải ngày (YYYY-MM-DD)")
    if "durations" in e:
        d = e["durations"]
        if d is None or d == []:
            out["durations"] = None
        elif isinstance(d, list) and all(_is_int(x) and x > 0 for x in d):
            out["durations"] = list(d)
        else:
            errs.append(f"{where}.durations phải là danh sách số giây nguyên dương")
    if "price" in e:
        ok, p = _sanitize_price(e["price"], f"{where}.price", errs)
        if ok:
            out["price"] = p
    for k in _STR_FIELDS:
        if k in e:
            if e[k] is None or isinstance(e[k], str):
                out[k] = e[k]
            else:
                errs.append(f"{where}.{k} phải là chuỗi")
    return out


def _sanitize(data: dict, source: str) -> tuple[dict, list[str]]:
    """Keep only well-typed parts of a metadata dict. Errors: "<source>: <key path> <problem>"."""
    errs: list[str] = []
    out: dict[str, Any] = {}
    if "plan" in data:
        if data["plan"] is None or isinstance(data["plan"], str):
            out["plan"] = data["plan"]
        else:
            errs.append("plan phải là chuỗi")
    if "default_video_model" in data:
        v = data["default_video_model"]
        if isinstance(v, str) and v.strip():
            out["default_video_model"] = v.strip()
        else:
            errs.append("default_video_model phải là chuỗi khác rỗng")
    if "fallback" in data:
        fb = data["fallback"]
        if isinstance(fb, dict):
            parts: dict[str, list[dict]] = {}
            for part in ("video", "image"):
                if part not in fb:
                    continue
                items = _sanitize_fallback_list(fb[part], f"fallback.{part}", errs)
                if items:
                    parts[part] = items
                elif items is not None:
                    errs.append(f"fallback.{part} rỗng")
            out["fallback"] = parts
        else:
            errs.append("fallback phải là dict")
    if "models" in data:
        ms = data["models"]
        if isinstance(ms, dict):
            models: dict[str, dict] = {}
            for alias, e in ms.items():
                if not isinstance(alias, str) or not alias:
                    errs.append(f"models.{alias} tên alias phải là chuỗi")
                    continue
                if e is None:
                    e = {}
                if not isinstance(e, dict):
                    errs.append(f"models.{alias} phải là dict")
                    continue
                models[alias] = _sanitize_entry(alias, e, errs)
            out["models"] = models
        else:
            errs.append("models phải là dict")
    return out, [f"{source}: {e}" for e in errs]


# --- loading ------------------------------------------------------------------

def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _min_meta(day: date) -> ModelMeta:
    return ModelMeta(plan=None, default_video_model=DEFAULT_VIDEO_MODEL, fallback=copy.deepcopy(MIN_FALLBACK),
                     models={}, today=day, package_ok=False)


def _add_errors(meta: ModelMeta, source: str, errs: list[str]) -> None:
    prefix = f"{source}: "
    for e in errs:
        meta._error(source, e[len(prefix):] if e.startswith(prefix) else e)


def _load_package(day: date) -> ModelMeta:
    src = str(PACKAGE_FILE)
    try:
        data = _read_yaml(PACKAGE_FILE)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        meta = _min_meta(day)
        meta._error(src, _short(e))
        return meta
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        meta = _min_meta(day)
        meta._error(src, "nội dung phải là dict có khoá models (dict)")
        return meta
    clean, errs = _sanitize(data, src)
    fb = clean.get("fallback") or {}
    meta = ModelMeta(
        plan=clean.get("plan"),
        default_video_model=clean.get("default_video_model") or DEFAULT_VIDEO_MODEL,
        fallback={p: fb.get(p) or copy.deepcopy(MIN_FALLBACK[p]) for p in ("video", "image")},
        models=clean.get("models", {}),
        today=day,
        package_ok=True,
    )
    _add_errors(meta, src, errs)
    return meta


def _apply_override(meta: ModelMeta, path: Path) -> None:
    src = str(path)
    if not path.exists():
        return
    try:
        data = _read_yaml(path)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        meta._error(src, _short(e))
        return
    if data is None:  # empty file: nothing to override
        return
    if not isinstance(data, dict):
        meta._error(src, "nội dung phải là dict")
        return
    clean, errs = _sanitize(data, src)
    _add_errors(meta, src, errs)
    if "plan" in clean:
        meta.plan = clean["plan"]
    if "fallback" in clean:
        fb = clean["fallback"]
        meta.fallback = {p: fb.get(p) or meta.fallback[p] for p in ("video", "image")}
    for alias, e in clean.get("models", {}).items():
        meta.models[alias] = {**meta.models.get(alias, {}), **e}
    if "default_video_model" in clean:  # checked after the models merge: new projects must not start on an old model
        v = clean["default_video_model"]
        if meta.entry(v) is None:
            meta._error(src, f"default_video_model {v} không có trong metadata")
        elif meta.is_old(v):
            meta._error(src, f"default_video_model {v} sắp hoặc đã ngừng")
        else:
            meta.default_video_model = v


def _short(e: BaseException) -> str:
    return " ".join(str(e).split()) or type(e).__name__


def load() -> ModelMeta:
    """Read the package file and the override file (every call, no cache). Never raises."""
    try:
        day = today()
    except Exception:  # a broken patched today() must not break callers
        day = date.today()
    try:
        meta = _load_package(day)
    except Exception as e:
        meta = _min_meta(day)
        meta._error(str(PACKAGE_FILE), _short(e))
        return meta
    if not meta.package_ok:
        return meta
    try:
        path = override_path()
    except Exception as e:
        meta._error("models.yaml", f"không xác định được file ghi đè ({_short(e)})")
        return meta
    snapshot = copy.deepcopy((meta.plan, meta.default_video_model, meta.fallback, meta.models))
    try:
        _apply_override(meta, path)
    except Exception as e:
        meta.plan, meta.default_video_model, meta.fallback, meta.models = snapshot
        meta._error(str(path), _short(e))
    return meta


def cli_warning(meta: ModelMeta) -> str | None:
    """One "⚠ File metadata model lỗi: …" line per file with errors, or None."""
    by_file: dict[str, list[str]] = {}
    for src, msg in meta.error_items:
        by_file.setdefault(src, []).append(msg)
    if not by_file:
        return None
    return "\n".join(f"⚠ File metadata model lỗi: {src} ({'; '.join(msgs)}); dùng bản mặc định."
                     for src, msgs in by_file.items())


def normalize_new_storyboard(sb: "Storyboard", meta: ModelMeta) -> "Storyboard":
    """New projects (planner output): never start on a retiring/retired model."""
    if not sb.model or meta.is_old(sb.model):
        sb.model = meta.default_video_model
    for s in sb.scenes:
        if s.model and meta.is_old(s.model):
            s.model = None
    return sb
