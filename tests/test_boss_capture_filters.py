from services.boss_capture_service import (
    _activity_days,
    _matches_capture_filters,
    _normalize_search_keywords,
)


def test_normalize_search_keywords_merges_and_deduplicates():
    assert _normalize_search_keywords(
        "AI 产品经理",
        ["大模型产品，智能体产品", "AI 产品经理"],
    ) == ["AI 产品经理", "大模型产品", "智能体产品"]


def test_activity_days_understands_common_boss_labels():
    assert _activity_days("刚刚活跃") == 0
    assert _activity_days("今日活跃") == 0
    assert _activity_days("3日内活跃") == 3
    assert _activity_days("本周活跃") == 7
    assert _activity_days("本月活跃") == 30


def test_capture_filters_company_size_and_hr_activity():
    job = {"company_size": "20-99人", "hr_active": "3日内活跃"}
    assert _matches_capture_filters(
        job, {"company_size": "20-99人", "hr_activity": "7d"}
    )
    assert not _matches_capture_filters(
        job, {"company_size": "100-499人", "hr_activity": "7d"}
    )
    assert not _matches_capture_filters(job, {"hr_activity": "today"})
