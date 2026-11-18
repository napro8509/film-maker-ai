"""filmmaker/rules.py is the one place for the film-making limits and the fixed prompt sentences."""

import re
from pathlib import Path

from click.testing import CliRunner

from filmmaker import formats, i2v_prompt, planner, qc, reviewer, rules, storyboard
from filmmaker.cli import main

SRC = Path(rules.__file__).parent


def test_every_catalogued_rule_exists_once():
    names = [r.name for r in rules.RULES]
    assert len(names) == len(set(names))
    for r in rules.RULES:
        assert hasattr(rules, r.name), r.name
        assert r.why and r.used_in and r.kind in ("giới hạn", "chèn", "dặn LLM")


def test_every_rule_constant_is_catalogued():
    consts = {k for k, v in vars(rules).items()
              if k.isupper() and isinstance(v, (str, int, float, re.Pattern))}
    assert consts == {r.name for r in rules.RULES}


def test_modules_use_the_same_objects():
    assert storyboard.NO_TEXT is rules.NO_TEXT
    assert storyboard.DIALOGUE_RULE is rules.DIALOGUE_RULE
    assert storyboard.MAX_REFS == rules.MAX_REFS
    assert i2v_prompt.MAX_WORDS == rules.MAX_WORDS
    assert i2v_prompt.SILENT_BINDING is rules.SILENT_BINDING
    assert formats.SILENT_RULE is rules.SILENT_RULE
    assert formats.AUDIO["dialogue"]["rule"] is rules.AUDIO_DIALOGUE_RULE
    assert formats.AUDIO["voiceover"]["rule"] is rules.VOICEOVER_RULE
    assert qc.WRONG_START == rules.START_MATCH_MIN


def test_shared_children_guide_in_both_prompts():
    assert rules.CHILDREN_GUIDE in planner.SYSTEM
    assert rules.CHILDREN_GUIDE in reviewer.STORYBOARD_SYSTEM


def test_rule_texts_live_only_in_rules_py():
    """No module writes a rule sentence again instead of importing it."""
    texts = {r.name: r.value for r in rules.RULES if isinstance(r.value, str) and len(r.value) > 20}
    for path in SRC.glob("*.py"):
        if path.name in ("rules.py", "flow_ui.py", "muse_ui.py"):
            continue
        src = path.read_text()
        for name, text in texts.items():
            assert text not in src, f"{path.name} repeats rules.{name}"


def test_standalone_script_copies_match():
    """flow_ui.py runs with gflow-cli's Python and cannot import rules.py: its copy must stay equal."""
    src = (SRC / "flow_ui.py").read_text()
    m = re.search(r"^START_MATCH_MIN = ([0-9.]+)", src, re.M)
    assert m and float(m.group(1)) == rules.START_MATCH_MIN


def test_cli_lists_rules():
    res = CliRunner().invoke(main, ["rules"])
    assert res.exit_code == 0
    assert "## Thoại" in res.output and "MAX_WORDS" in res.output and "CHILDREN_GUIDE" in res.output
