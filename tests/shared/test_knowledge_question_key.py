"""KNOW-01: normalized question keys must not collide for long distinct texts."""

from app.scenarios.knowledge_feedback.service import _question_key


def test_question_key_collapses_whitespace_and_case() -> None:
    assert _question_key("  加班费 规则  ") == _question_key("加班费  规则")


def test_question_key_distinguishes_long_distinct_questions() -> None:
    # 200-char strings sharing a 100-char prefix must produce different keys.
    base = "同".join("甲" for _ in range(200))
    variant = base[:-1] + "乙"
    assert _question_key(base) != _question_key(variant)


def test_question_key_stays_within_column_limit() -> None:
    key = _question_key("很" * 500)
    assert len(key) <= 255
