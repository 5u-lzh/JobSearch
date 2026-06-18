"""v0.36 岗位画像与候选人画像 Agent 测试（mock LLM，不调用付费 API）."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from unittest.mock import patch, MagicMock


# ── 公共工具函数测试 ──

def test_parse_json_from_llm_plain():
    """纯 JSON 字符串解析"""
    from services.agent_common import parse_json_from_llm
    data = parse_json_from_llm('{"key": "value"}')
    assert data == {"key": "value"}


def test_parse_json_from_llm_markdown():
    """Markdown code fence 包裹的 JSON 解析"""
    from services.agent_common import parse_json_from_llm
    raw = '```json\n{"key": "value"}\n```'
    data = parse_json_from_llm(raw)
    assert data == {"key": "value"}


def test_parse_json_from_llm_extra_text():
    """包含多余文本的 JSON 解析"""
    from services.agent_common import parse_json_from_llm
    raw = 'Here is the result: {"key": "value"} done.'
    data = parse_json_from_llm(raw)
    assert data == {"key": "value"}


def test_parse_json_from_llm_invalid():
    """非法 JSON 返回 None"""
    from services.agent_common import parse_json_from_llm
    data = parse_json_from_llm("not json at all")
    assert data is None


def test_validate_pydantic_success():
    """Pydantic 校验成功"""
    from services.agent_common import validate_pydantic
    from services.profile_schemas import JobProfileResult
    data = {"job_name": "Python后端", "must_have_capabilities": ["Python"]}
    result = validate_pydantic(data, JobProfileResult)
    assert result is not None
    assert result.job_name == "Python后端"


def test_validate_pydantic_failure():
    """Pydantic 校验失败返回 None"""
    from services.agent_common import validate_pydantic
    from services.profile_schemas import JobProfileResult
    # 缺少必需字段
    data = {"invalid_field": "value"}
    result = validate_pydantic(data, JobProfileResult)
    # 可能返回 None 或使用默认值，取决于 schema
    assert result is None or result.job_name == ""


# ── 岗位画像 Agent 测试 ──

def test_job_profile_agent_fallback_on_llm_failure():
    """LLM 调用失败时返回规则画像"""
    from services.job_profile_agent import JobProfileAgent
    from services.profile_schemas import JobProfileResult

    fallback = JobProfileResult(
        job_name="Python后端",
        must_have_capabilities=["Python", "FastAPI"],
        sample_count=5,
    )

    agent = JobProfileAgent()
    with patch("services.job_profile_agent.call_llm", return_value=None):
        result, mode = agent.analyze("Python后端", ["JD text"], fallback_profile=fallback)

    assert mode == "rule_fallback"
    assert result.job_name == "Python后端"


def test_job_profile_agent_fallback_on_invalid_json():
    """LLM 返回非法 JSON 时返回规则画像"""
    from services.job_profile_agent import JobProfileAgent
    from services.profile_schemas import JobProfileResult

    fallback = JobProfileResult(job_name="测试", sample_count=3)

    agent = JobProfileAgent()
    with patch("services.job_profile_agent.call_llm", return_value="not json"):
        result, mode = agent.analyze("测试", ["JD text"], fallback_profile=fallback)

    assert mode == "rule_fallback"


def test_job_profile_agent_success():
    """LLM 成功返回结构化结果"""
    from services.job_profile_agent import JobProfileAgent
    from services.profile_schemas import JobProfileResult

    llm_response = json.dumps({
        "job_name": "Python后端",
        "employment_type": "全职",
        "target_audience": "有经验候选人",
        "responsibilities": ["负责后端开发", "参与架构设计"],
        "must_have_capabilities": ["Python", "FastAPI", "MySQL"],
        "nice_to_have_capabilities": ["Docker", "Redis"],
        "education_preference": "本科",
        "major_preference": "计算机",
        "experience_requirement": "3年以上",
        "business_context": ["后台系统"],
        "growth_context": [],
        "confidence": "high",
        "quality_flags": [],
        "sample_count": 5,
        "valid_sample_count": 5,
        "filtered_sample_count": 0,
    })

    jd_text = "岗位职责：负责公司核心业务系统的后端开发与维护。参与系统架构设计与优化。任职要求：本科及以上学历，计算机相关专业。3年以上Python后端开发经验。熟悉Python、FastAPI、MySQL、Redis。"

    agent = JobProfileAgent()
    with patch("services.job_profile_agent.call_llm", return_value=llm_response):
        result, mode = agent.analyze("Python后端", [jd_text])

    assert mode == "agent"
    assert result.job_name == "Python后端"
    assert "Python" in result.must_have_capabilities
    assert result.employment_type == "全职"


def test_job_profile_agent_caps_must_have_at_6():
    """必备能力不超过 6 项"""
    from services.job_profile_agent import JobProfileAgent

    llm_response = json.dumps({
        "job_name": "测试",
        "must_have_capabilities": ["A", "B", "C", "D", "E", "F", "G", "H"],
        "nice_to_have_capabilities": [],
        "sample_count": 1,
    })

    agent = JobProfileAgent()
    with patch("services.job_profile_agent.call_llm", return_value=llm_response):
        result, mode = agent.analyze("测试", ["JD text"])

    assert len(result.must_have_capabilities) <= 6


def test_job_profile_agent_caps_nice_to_have_at_5():
    """加分能力不超过 5 项"""
    from services.job_profile_agent import JobProfileAgent

    llm_response = json.dumps({
        "job_name": "测试",
        "must_have_capabilities": [],
        "nice_to_have_capabilities": ["A", "B", "C", "D", "E", "F", "G"],
        "sample_count": 1,
    })

    agent = JobProfileAgent()
    with patch("services.job_profile_agent.call_llm", return_value=llm_response):
        result, mode = agent.analyze("测试", ["JD text"])

    assert len(result.nice_to_have_capabilities) <= 5


def test_job_profile_agent_dedup_must_and_nice():
    """必备与加分不重复"""
    from services.job_profile_agent import JobProfileAgent

    llm_response = json.dumps({
        "job_name": "测试",
        "must_have_capabilities": ["Python", "FastAPI"],
        "nice_to_have_capabilities": ["Python", "Docker"],
        "sample_count": 1,
    })

    agent = JobProfileAgent()
    with patch("services.job_profile_agent.call_llm", return_value=llm_response):
        result, mode = agent.analyze("测试", ["JD text"])

    must_set = {s.lower() for s in result.must_have_capabilities}
    nice_set = {s.lower() for s in result.nice_to_have_capabilities}
    assert must_set.isdisjoint(nice_set)


# ── 候选人画像 Agent 测试 ──

def test_candidate_agent_fallback_on_llm_failure():
    """LLM 失败时返回规则画像"""
    from services.candidate_profile_agent import CandidateProfileAgent
    from services.profile_schemas import CandidateProfileResult

    fallback = CandidateProfileResult(
        skill_stack=[{"skill": "Python", "confidence": "explicit"}],
        confidence="medium",
    )

    agent = CandidateProfileAgent()
    with patch("services.candidate_profile_agent.call_llm", return_value=None):
        result, mode = agent.analyze("简历文本", fallback_profile=fallback)

    assert mode == "rule_fallback"


def test_candidate_agent_success():
    """LLM 成功返回结构化结果"""
    from services.candidate_profile_agent import CandidateProfileAgent

    llm_response = json.dumps({
        "education_background": {"degree": "本科", "major": "计算机", "school": "浙江大学", "graduation_year": "2024"},
        "skill_stack": [{"skill": "Python", "confidence": "explicit"}, {"skill": "FastAPI", "confidence": "explicit"}],
        "projects": [{"name": "求职平台", "description": "FastAPI后端开发", "confidence": "explicit"}],
        "internships": [],
        "work_experiences": [],
        "achievements": [{"description": "日活500+", "has_metric": True}],
        "learning_signals": ["开源参与"],
        "transferable_strengths": [],
        "risk_points": [],
        "confidence": "high",
    })

    resume = "张三，本科计算机科学与技术专业，2024年毕业于浙江大学。技能：Python、FastAPI、MySQL、Redis、Docker。项目经历：1. 基于FastAPI的求职分析平台后端开发，使用MySQL存储数据，Redis缓存，Docker容器化部署，上线后日活用户500+。2. 智能问答系统，基于LangChain构建RAG问答系统。"

    agent = CandidateProfileAgent()
    with patch("services.candidate_profile_agent.call_llm", return_value=llm_response):
        result, mode = agent.analyze(resume)

    assert mode == "agent"
    assert result.education_background.get("school") == "浙江大学"


def test_candidate_agent_rejects_fabricated_school():
    """LLM 编造不存在的院校时回退到规则画像"""
    from services.candidate_profile_agent import CandidateProfileAgent

    llm_response = json.dumps({
        "education_background": {"degree": "本科", "major": "计算机", "school": "不存在的大学", "graduation_year": "2024"},
        "skill_stack": [],
        "projects": [],
        "internships": [],
        "work_experiences": [],
        "achievements": [],
        "learning_signals": [],
        "transferable_strengths": [],
        "risk_points": [],
        "confidence": "low",
    })

    agent = CandidateProfileAgent()
    # 简历中没有"不存在的大学"
    with patch("services.candidate_profile_agent.call_llm", return_value=llm_response):
        result, mode = agent.analyze("张三，本科计算机专业，2024年毕业于浙江大学。")

    assert mode == "rule_fallback"


def test_candidate_agent_dedup_skills():
    """技能去重"""
    from services.candidate_profile_agent import CandidateProfileAgent

    llm_response = json.dumps({
        "education_background": {},
        "skill_stack": [
            {"skill": "Python", "confidence": "explicit"},
            {"skill": "python", "confidence": "inferred"},
            {"skill": "FastAPI", "confidence": "explicit"},
        ],
        "projects": [],
        "internships": [],
        "work_experiences": [],
        "achievements": [],
        "learning_signals": [],
        "transferable_strengths": [],
        "risk_points": [],
        "confidence": "low",
    })

    resume = "张三，本科计算机专业，2024年毕业。技能：Python、FastAPI、MySQL。项目：开发了求职分析平台。"

    agent = CandidateProfileAgent()
    with patch("services.candidate_profile_agent.call_llm", return_value=llm_response):
        result, mode = agent.analyze(resume)

    # python 和 Python 应该合并
    skill_names = [s["skill"].lower() for s in result.skill_stack]
    assert skill_names.count("python") == 1


# ── API 集成测试 ──

def test_candidate_profile_api_returns_analysis_mode():
    """/candidate_profiles/analyze 返回 analysis_mode"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    import time

    c = TestClient(app)
    resume = f"张三，本科计算机专业，2024年毕业于浙江大学。技能：Python、FastAPI。唯一标识：{time.time()}"

    r = c.post("/candidate_profiles/analyze", json={
        "user_id": 0,
        "resume_text": resume,
    })

    assert r.status_code == 200
    data = r.json()
    assert "analysis_mode" in data
    assert data["analysis_mode"] in ("agent", "rule_fallback")
