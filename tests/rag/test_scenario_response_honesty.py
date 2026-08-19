from app.scenarios.culture_content.schemas import CultureContentResponse
from app.scenarios.weekly_report.schemas import WeeklyReportResponse


def test_weekly_report_response_uses_default_honest_confidence_shape() -> None:
    response = WeeklyReportResponse(period="2026-W33", summary="ok")

    assert response.confidence == 0.0
    assert response.has_evidence is False


def test_culture_content_response_uses_default_honest_confidence_shape() -> None:
    response = CultureContentResponse(news_article="a")

    assert response.confidence == 0.0
