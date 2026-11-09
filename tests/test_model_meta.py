"""filmmaker.model_meta: package metadata, override merge, type checks, status by date."""

from __future__ import annotations

import importlib.resources
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from filmmaker import model_meta
from filmmaker.storyboard import Scene, Storyboard

ROOT = Path(__file__).resolve().parent.parent
OLD_FALLBACK = {
    "video": [
        {"alias": "veo-fast", "max_duration": 8}, {"alias": "veo-quality", "max_duration": 8},
        {"alias": "veo-lite", "max_duration": 8}, {"alias": "veo-lite-lp", "max_duration": 8},
        {"alias": "omni-flash", "max_duration": 10}, {"alias": "omni-1.2-flash", "max_duration": 15},
    ],
    "image": [
        {"alias": "nano2", "label": "Nano Banana 2"}, {"alias": "nano-pro", "label": "Nano Banana Pro"},
        {"alias": "imagen4", "label": "Imagen 4"}, {"alias": "nano2-lite", "label": "Nano Banana 2 Lite"},
    ],
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(model_meta, "today", lambda: date(2026, 11, 9))
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("FILMMAKER_MODELS", raising=False)


def override(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "models.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_models_yaml_is_packaged():
    data = yaml.safe_load((importlib.resources.files("filmmaker") / "models.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict) and isinstance(data["models"], dict)
    assert '"models.yaml"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_package_metadata_content():
    m = model_meta.load()
    assert m.package_ok and m.errors == []
    assert m.plan == "ultra" and m.default_video_model == "veo-lite"
    assert m.fallback == OLD_FALLBACK
    for a in ("veo-fast", "veo-quality", "omni-flash"):
        assert m.retires(a) == date(2026, 12, 5)
    for a in ("veo-lite", "veo-lite-lp", "omni-1.2-flash"):
        assert m.retires(a) is None
    assert all(e["checked"] == date(2026, 11, 9) for e in m.models.values())
    assert set(m.models) == {"veo-lite", "veo-lite-lp", "veo-fast", "veo-quality", "omni-flash", "omni-1.2-flash"}
    assert m.durations("veo-lite") == [4, 6, 8] and m.durations("veo-lite-lp") == [4, 6, 8]
    assert m.entry("veo-lite")["price"] == {"unit": "clip", "ultra": 5, "default": 10}
    assert m.entry("veo-fast")["price"] == {"unit": "clip", "ultra": 10, "default": 20}
    assert m.entry("veo-quality")["price"] == {"unit": "clip", "default": 100}
    assert m.entry("omni-1.2-flash")["price"] == {"unit": "second", "default": 3}
    assert m.price_unit("omni-1.2-flash") == "second" and m.price_unit("veo-lite-lp") is None
    assert "11–15 giây cần gói trả phí" in m.entry("omni-1.2-flash")["note"]
    assert m.entry("omni-flash")["note"] == "Omni Flash 1.1"


def test_credits_by_plan_default_and_missing(tmp_path, monkeypatch):
    m = model_meta.load()
    assert m.credits("veo-lite") == 5           # price[plan]
    assert m.credits("veo-quality") == 100      # only default
    assert m.credits("veo-lite-lp") is None     # no price
    assert m.credits("nope") is None and m.credits(None) is None
    monkeypatch.setenv("FILMMAKER_MODELS", str(override(tmp_path, "plan: pro\n")))
    m = model_meta.load()
    assert m.plan == "pro" and m.credits("veo-lite") == 10


def test_status_by_date(monkeypatch):
    m = model_meta.load()
    assert m.status("veo-fast") == "retiring" and m.is_old("veo-fast")
    assert m.status("veo-lite") == "active" and not m.is_old("veo-lite")
    assert m.status("unknown") == "active" and m.status(None) == "active"
    assert m.retires_label("veo-fast") == "05/12" and m.retires_label("veo-lite") is None
    monkeypatch.setattr(model_meta, "today", lambda: date(2026, 12, 5))
    m = model_meta.load()
    assert m.today == date(2026, 12, 5)
    assert m.status("veo-fast") == "retired" and m.is_old("veo-fast")


def test_real_today_is_local_date():
    assert model_meta._real_today() == date.today()


def test_info_is_json_ready():
    m = model_meta.load()
    i = m.info("veo-fast")
    assert i["retires"] == "2026-12-05" and i["checked"] == "2026-11-09" and i["credits"] == 10
    assert m.info("nope") is None and m.info(None) is None
    i["price"]["ultra"] = 999                   # a copy, not the loaded entry
    assert m.credits("veo-fast") == 10


def test_override_path_recomputed_each_call(tmp_path, monkeypatch):
    assert model_meta.override_path() == tmp_path / "models.yaml"
    monkeypatch.setenv("FILMMAKER_SETTINGS", str(tmp_path / "other" / "settings.json"))
    assert model_meta.override_path() == tmp_path / "other" / "models.yaml"
    monkeypatch.setenv("FILMMAKER_MODELS", str(tmp_path / "x.yaml"))
    assert model_meta.override_path() == tmp_path / "x.yaml"


def test_override_next_to_settings_is_read(tmp_path):
    override(tmp_path, "models:\n  veo-lite:\n    retires: 2026-11-01\n")
    m = model_meta.load()
    assert m.status("veo-lite") == "retired" and m.errors == []


def test_override_merges_per_field(tmp_path):
    override(tmp_path, """
default_video_model: omni-1.2-flash
fallback:
  video:
    - {alias: veo-lite, max_duration: 8}
models:
  veo-lite:
    retires: 2026-12-20
  veo-fast:
    retires:
  brand-new:
    durations: [5]
""")
    m = model_meta.load()
    assert m.errors == []
    assert m.retires("veo-lite") == date(2026, 12, 20)
    assert m.durations("veo-lite") == [4, 6, 8] and m.credits("veo-lite") == 5
    assert m.status("veo-fast") == "active"     # empty value in override removes the date
    assert m.durations("brand-new") == [5]
    assert m.default_video_model == "omni-1.2-flash"
    assert m.fallback["video"] == [{"alias": "veo-lite", "max_duration": 8}]
    assert m.fallback["image"] == OLD_FALLBACK["image"]   # missing part comes from the package


def test_override_is_reread_each_load(tmp_path):
    p = override(tmp_path, "models:\n  veo-fast:\n    retires: 2027-01-01\n")
    assert model_meta.load().retires("veo-fast") == date(2027, 1, 1)
    p.write_text("models:\n  veo-fast:\n    retires: 2026-11-01\n", encoding="utf-8")
    assert model_meta.load().status("veo-fast") == "retired"


@pytest.mark.parametrize("text", ["models: [unclosed\n", "- just\n- a list\n", "plan: ultra\n  bad: indent\n"])
def test_bad_override_falls_back_to_package(tmp_path, text):
    p = override(tmp_path, text)
    m = model_meta.load()
    assert m.package_ok and m.status("veo-fast") == "retiring" and m.credits("veo-lite") == 5
    assert m.errors and str(p) in m.error_text
    w = model_meta.cli_warning(m)
    assert w.count("\n") == 0
    assert w.startswith(f"⚠ File metadata model lỗi: {p} (") and w.endswith("); dùng bản mặc định.")


def test_no_override_no_warning():
    m = model_meta.load()
    assert m.errors == [] and m.error_text is None and model_meta.cli_warning(m) is None


def test_empty_override_is_fine(tmp_path):
    override(tmp_path, "")
    assert model_meta.load().errors == []


WRONG = [
    ("models: {veo-fast: 2026-12-05}\n", "models.veo-fast"),
    ("models: [veo-fast]\n", "models"),
    ("models: {veo-lite: {price: 5}}\n", "models.veo-lite.price"),
    ("models: {veo-lite: {price: {unit: hour, ultra: 1}}}\n", "models.veo-lite.price.unit"),
    ("models: {veo-lite: {price: {unit: clip, ultra: x}}}\n", "models.veo-lite.price.ultra"),
    ("models: {veo-lite: {price: {ultra: true}}}\n", "models.veo-lite.price.ultra"),
    ("models: {veo-lite: {durations: 8}}\n", "models.veo-lite.durations"),
    ("models: {veo-lite: {durations: '4,6,8'}}\n", "models.veo-lite.durations"),
    ("models: {veo-lite: {durations: [4, x]}}\n", "models.veo-lite.durations"),
    ("models: {veo-lite: {durations: [true]}}\n", "models.veo-lite.durations"),
    ("models: {veo-fast: {retires: ngày mai}}\n", "models.veo-fast.retires"),
    ("models: {veo-fast: {checked: 5}}\n", "models.veo-fast.checked"),
    ("models: {veo-fast: {note: [1]}}\n", "models.veo-fast.note"),
    ("fallback: [veo-lite]\n", "fallback"),
    ("fallback: {video: 3}\n", "fallback.video"),
    ("fallback: {video: [{max_duration: 8}]}\n", "fallback.video[0]"),
    ("plan: 5\n", "plan"),
    ("default_video_model: ''\n", "default_video_model"),
]


@pytest.mark.parametrize("text,key", WRONG)
def test_override_wrong_types_ignored(tmp_path, text, key):
    p = override(tmp_path, text)
    m = model_meta.load()
    assert m.package_ok
    assert any(e.startswith(f"{p}: {key}") for e in m.errors), m.errors
    assert m.plan == "ultra" and m.default_video_model == "veo-lite"
    assert m.credits("veo-lite") == 5 and m.durations("veo-lite") == [4, 6, 8]
    assert m.status("veo-fast") == "retiring" and m.retires_label("veo-fast") == "05/12"
    assert m.fallback["video"] and m.fallback["image"]
    for a in m.models:                          # every accessor works on the cleaned data
        m.info(a), m.credits(a), m.durations(a), m.status(a), m.price_unit(a)
    assert model_meta.cli_warning(m).endswith("; dùng bản mặc định.")


def test_wrong_parts_dropped_good_parts_kept(tmp_path):
    override(tmp_path, """
plan: 5
models:
  veo-fast: 2026-12-05
  veo-lite:
    price: 5
    retires: 2026-11-01
  veo-quality:
    price: {default: x, ultra: 50}
""")
    m = model_meta.load()
    assert len(m.errors) == 4
    assert m.status("veo-lite") == "retired"    # valid field in the same entry still applies
    assert m.credits("veo-lite") == 5           # package price kept
    assert m.status("veo-fast") == "retiring"
    assert m.credits("veo-quality") == 50
    assert m.entry("veo-quality")["price"] == {"unit": "clip", "ultra": 50}
    w = model_meta.cli_warning(m)
    assert w.count("⚠") == 1 and w.count("; ") >= 3


def test_dates_accept_iso_strings_and_datetimes(tmp_path):
    override(tmp_path, "models:\n  veo-lite:\n    retires: '2026-12-01'\n  veo-fast:\n    retires: 2026-12-01 10:00:00\n")
    m = model_meta.load()
    assert m.errors == []
    assert m.retires("veo-lite") == date(2026, 12, 1) and m.retires("veo-fast") == date(2026, 12, 1)


@pytest.mark.parametrize("content", [None, "models: [bad\n", "- a\n", "plan: ultra\n", "models: 3\n"])
def test_broken_package_metadata(tmp_path, monkeypatch, content):
    pkg = tmp_path / "pkg.yaml"
    if content is not None:
        pkg.write_text(content, encoding="utf-8")
    monkeypatch.setattr(model_meta, "PACKAGE_FILE", pkg)
    override(tmp_path, "models:\n  veo-lite:\n    retires: 2026-11-01\n")  # ignored when the package is broken
    m = model_meta.load()
    assert not m.package_ok and m.models == {} and m.plan is None
    assert m.fallback == model_meta.MIN_FALLBACK and m.fallback is not model_meta.MIN_FALLBACK
    assert m.default_video_model == "veo-lite"
    assert m.info("veo-lite") is None and m.status("veo-fast") == "active" and m.credits("veo-lite") is None
    assert str(pkg) in m.error_text and str(tmp_path / "models.yaml") not in m.error_text
    assert model_meta.cli_warning(m).startswith(f"⚠ File metadata model lỗi: {pkg} (")


def test_load_never_raises_on_unexpected_error(tmp_path, monkeypatch):
    override(tmp_path, "models:\n  veo-lite:\n    retires: 2026-11-01\n")

    def boom(*_a, **_k):
        raise RuntimeError("bất ngờ")

    monkeypatch.setattr(model_meta, "_sanitize", boom)
    m = model_meta.load()   # package sanitize fails too -> minimal meta, still no exception
    assert not m.package_ok and "bất ngờ" in m.error_text


def test_unexpected_override_error_keeps_package(tmp_path, monkeypatch):
    p = override(tmp_path, "models:\n  veo-lite:\n    retires: 2026-11-01\n")
    real = model_meta._sanitize

    def flaky(data, source):
        if source == str(p):
            raise RuntimeError("bất ngờ")
        return real(data, source)

    monkeypatch.setattr(model_meta, "_sanitize", flaky)
    m = model_meta.load()
    assert m.package_ok and m.status("veo-lite") == "active" and m.credits("veo-lite") == 5
    assert m.errors == [f"{p}: bất ngờ"]


def sb(model, scene_models):
    return Storyboard(title="t", model=model,
                      scenes=[Scene(id=f"s{i}", mode="t2v", prompt="p", model=sm) for i, sm in enumerate(scene_models)])


def test_normalize_new_storyboard():
    m = model_meta.load()
    out = model_meta.normalize_new_storyboard(sb("veo-fast", ["veo-quality", "omni-1.2-flash", None]), m)
    assert out.model == "veo-lite"
    assert [s.model for s in out.scenes] == [None, "omni-1.2-flash", None]
    assert model_meta.normalize_new_storyboard(sb(None, [None]), m).model == "veo-lite"
    assert model_meta.normalize_new_storyboard(sb("omni-1.2-flash", [None]), m).model == "omni-1.2-flash"


def test_meta_does_not_import_fastapi():
    code = "import sys, filmmaker.model_meta; assert 'fastapi' not in sys.modules, 'fastapi imported'"
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --- after review: non-finite / negative prices, default_video_model checked ---------------

@pytest.mark.parametrize("value", [".inf", "-.inf", ".nan", "-5", "-0.5"])
def test_override_price_must_be_finite_and_not_negative(tmp_path, value):
    p = override(tmp_path, f"models:\n  veo-lite:\n    price: {{default: {value}, ultra: {value}}}\n"
                           f"  veo-quality:\n    price: {{default: {value}, ultra: 40}}\n")
    m = model_meta.load()
    assert any(e.startswith(f"{p}: models.veo-lite.price") for e in m.errors), m.errors
    assert m.credits("veo-lite") == 5                     # package price kept
    assert m.entry("veo-quality")["price"] == {"unit": "clip", "ultra": 40}
    assert m.credits("veo-quality") == 40


def test_fallback_drops_non_finite_values(tmp_path):
    override(tmp_path, "fallback:\n  video:\n    - {alias: veo-lite, max_duration: 8, extra: .inf}\n")
    m = model_meta.load()
    assert m.fallback["video"] == [{"alias": "veo-lite", "max_duration": 8}]


@pytest.mark.parametrize("text,why", [
    ("default_video_model: veo-fast\n", "sắp hoặc đã ngừng"),
    ("default_video_model: nope-model\n", "không có trong metadata"),
    ("default_video_model: veo-lite-lp\nmodels:\n  veo-lite-lp:\n    retires: 2026-11-01\n", "sắp hoặc đã ngừng"),
])
def test_override_default_video_model_old_or_unknown_ignored(tmp_path, text, why):
    p = override(tmp_path, text)
    m = model_meta.load()
    assert m.default_video_model == "veo-lite"
    assert any(e.startswith(f"{p}: default_video_model") and why in e for e in m.errors), m.errors
    sb = model_meta.normalize_new_storyboard(Storyboard(title="x", scenes=[Scene(id="s01", prompt="p")]), m)
    assert sb.model == "veo-lite"


def test_override_default_video_model_new_alias_in_same_file(tmp_path):
    override(tmp_path, "default_video_model: brand-new\nmodels:\n  brand-new:\n    durations: [8]\n")
    m = model_meta.load()
    assert m.errors == [] and m.default_video_model == "brand-new"
