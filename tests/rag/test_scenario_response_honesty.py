from app.scenarios.culture_content.schemas import CultureContentResponse
from app.scenarios.weekly_report.schemas import WeeklyReportResponse


def test_weekly_report_response_does_not_fabricate_evidence_metadata() -> None:
    response = WeeklyReportResponse(period="2026-W33", summary="ok")
    dumped = response.model_dump()

    assert "confidence" not in dumped
    assert "has_evidence" not in dumped


def test_culture_content_response_does_not_fabricate_confidence() -> None:
    response = CultureContentResponse(news_article="a")

    assert "confidence" not in response.model_dump()
