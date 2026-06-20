"""v0.37 适配顾问测试."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch, MagicMock


TEST_USER_ID = 0


def _ensure_test_user():
    global TEST_USER_ID
    if TEST_USER_ID:
        return TEST_USER_ID
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/user?username=test_advisor_user")
    TEST_USER_ID = r.json().get("user_id", 1)
    return TEST_USER_ID


def _create_test_report():
    """创建测试报告，返回 report_id"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    from models.database import SessionLocal
    from models.profile import JobProfile, CandidateProfile, FitAnalysisReport
    import json

    uid = _ensure_test_user()
    with SessionLocal() as session:
        jp = JobProfile(job_name="测试岗位", must_have_capabilities='["Python"]', confidence="medium")
        session.add(jp)
        session.flush()
        cp = CandidateProfile(user_id=uid, skill_stack='[{"skill":"Python"}]', confidence="medium")
        session.add(cp)
        session.flush()
        rpt = FitAnalysisReport(
            user_id=uid, job_profile_id=jp.id, candidate_profile_id=cp.id,
            overall_fit_level="moderate", overall_score=65.0,
            fit_summary="测试报告", strengths='["技能匹配"]', gaps='["缺少实习"]',
            learning_plan='["补充RAG技能"]', interview_strategy='["准备技术深度"]',
            confidence="medium",
        )
        session.add(rpt)
        session.commit()
        session.refresh(rpt)
        return rpt.id, jp.id, cp.id


def _cleanup_test_data(ids):
    from models.database import SessionLocal
    from models.profile import FitAnalysisReport, JobProfile, CandidateProfile
    rid, jid, cid = ids
    with SessionLocal() as session:
        session.query(FitAnalysisReport).filter(FitAnalysisReport.id == rid).delete()
        session.query(JobProfile).filter(JobProfile.id == jid).delete()
        session.query(CandidateProfile).filter(CandidateProfile.id == cid).delete()
        session.commit()


def test_advisor_not_found():
    """报告不存在返回 404"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.post("/fit_analysis_reports/999999/advisor", json={"user_id": 0, "question": "test"})
    assert r.status_code == 200
    assert r.json()["code"] == 404


def test_advisor_empty_question():
    """空问题返回 400"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    ids = _create_test_report()
    try:
        r = c.post(f"/fit_analysis_reports/{ids[0]}/advisor", json={"user_id": _ensure_test_user(), "question": ""})
        assert r.status_code == 200
        assert r.json()["code"] == 400
    finally:
        _cleanup_test_data(ids)


def test_advisor_wrong_user():
    """用户不能访问别人的报告"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    ids = _create_test_report()
    try:
        r = c.post(f"/fit_analysis_reports/{ids[0]}/advisor", json={"user_id": 99999, "question": "test"})
        assert r.status_code == 200
        assert r.json()["code"] == 404
    finally:
        _cleanup_test_data(ids)


def test_advisor_rule_fallback():
    """LLM 异常时规则兜底返回 200"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    ids = _create_test_report()
    try:
        with patch("services.agent_common.call_llm", return_value=None):
            r = c.post(f"/fit_analysis_reports/{ids[0]}/advisor", json={
                "user_id": _ensure_test_user(),
                "question": "为什么是这个适配等级？"
            })
        assert r.status_code == 200
        data = r.json()
        assert data["code"] == 200
        assert data["analysis_mode"] == "rule_fallback"
        assert len(data["answer"]) > 0
    finally:
        _cleanup_test_data(ids)


def test_advisor_mock_llm_success():
    """Mock LLM 成功返回"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    ids = _create_test_report()
    try:
        with patch("services.agent_common.call_llm", return_value="这是AI顾问的回答"):
            r = c.post(f"/fit_analysis_reports/{ids[0]}/advisor", json={
                "user_id": _ensure_test_user(),
                "question": "我最应该优先补什么？"
            })
        assert r.status_code == 200
        data = r.json()
        assert data["code"] == 200
        assert data["analysis_mode"] == "agent"
        assert "AI顾问的回答" in data["answer"]
    finally:
        _cleanup_test_data(ids)


def test_advisor_returns_evidence_refs():
    """回答包含 evidence_refs"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    ids = _create_test_report()
    try:
        with patch("services.agent_common.call_llm", return_value="分析结果"):
            r = c.post(f"/fit_analysis_reports/{ids[0]}/advisor", json={
                "user_id": _ensure_test_user(),
                "question": "为什么是这个适配等级？"
            })
        assert r.status_code == 200
        data = r.json()
        assert "evidence_refs" in data
    finally:
        _cleanup_test_data(ids)


def test_advisor_rule_fallback_why_level():
    """规则兜底: 为什么是这个适配等级"""
    from api.fastapi_app import _advisor_rule_fallback
    report = {
        "overall_fit_level": "moderate",
        "overall_score": 65,
        "strengths": ["技能匹配"],
        "gaps": ["缺少实习"],
    }
    answer = _advisor_rule_fallback("为什么是这个适配等级？", report)
    assert "moderate" in answer
    assert "65" in answer


def test_advisor_rule_fallback_priority():
    """规则兜底: 最应该优先补什么"""
    from api.fastapi_app import _advisor_rule_fallback
    report = {
        "gaps": ["缺少实习", "项目经验不足"],
        "learning_plan": ["补充RAG技能"],
    }
    answer = _advisor_rule_fallback("我最应该优先补什么？", report)
    assert "缺少实习" in answer


def test_advisor_rule_fallback_interview():
    """规则兜底: 面试准备"""
    from api.fastapi_app import _advisor_rule_fallback
    report = {
        "interview_strategy": ["准备技术深度", "准备项目STAR"],
    }
    answer = _advisor_rule_fallback("针对该岗位如何准备面试？", report)
    assert "技术深度" in answer
