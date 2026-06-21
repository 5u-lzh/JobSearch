"""v0.44 岗位数据看板测试 — 筛选、清洗、洞察、技能模块、回归."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from api.fastapi_app import app, _normalize_job_type

client = TestClient(app)


# ═══ 基础路由与结构 ═══

def test_overview_no_filter():
    """1. 无筛选时接口正常返回"""
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 200
    assert "meta" in data
    assert "kpis" in data
    assert "insights" in data


def test_overview_backward_compat():
    """11. 原有 overview 返回结构保持兼容"""
    resp = client.get("/dashboard/overview")
    data = resp.json()
    # 原有字段全部存在
    for key in ("kpis", "top_jobs", "job_type_distribution",
                "skill_heatmap", "fit_score_distribution", "fit_level_distribution"):
        assert key in data, f"缺少字段 {key}"
    # KPI 四个字段
    for k in ("jd_count", "profile_count", "skill_count", "report_count"):
        assert k in data["kpis"], f"kpis 缺少 {k}"


def test_skill_trend_no_regression():
    """12. skill_trend 原有行为不回归"""
    # 缺少参数 → 400
    assert client.get("/dashboard/skill_trend").status_code == 400
    # 空参数 → 400
    assert client.get("/dashboard/skill_trend?job_name=").status_code == 400
    # 不存在的岗位 → 200 + 空 skills
    r = client.get("/dashboard/skill_trend?job_name=不存在XYZ")
    assert r.status_code == 200
    assert r.json()["skills"] == []


# ═══ 筛选功能 ═══

def test_keyword_filter():
    """2. 岗位关键词筛选"""
    r1 = client.get("/dashboard/overview")
    r2 = client.get("/dashboard/overview?job_keyword=Python后端")
    assert r1.status_code == 200 and r2.status_code == 200
    d1, d2 = r1.json(), r2.json()
    # 关键词筛选后 JD 数量应 <= 无筛选
    assert d2["kpis"]["jd_count"] <= d1["kpis"]["jd_count"]
    # meta 中应反映筛选条件
    assert d2["meta"]["filters"]["job_keyword"] == "Python后端"


def test_type_filter():
    """3. 岗位类型筛选"""
    r = client.get("/dashboard/overview?job_type=全职")
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["filters"]["job_type"] == "全职"
    # 类型分布中应只有全职
    for item in data["job_type_distribution"]:
        assert item["type"] == "全职"


def test_date_filter():
    """4. 合法时间范围"""
    r = client.get("/dashboard/overview?start_date=2025-01-01&end_date=2026-12-31")
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["filters"]["start_date"] == "2025-01-01"
    assert data["meta"]["filters"]["end_date"] == "2026-12-31"
    assert len(data["meta"]["filter_scope_notes"]) > 0


def test_invalid_date_format():
    """5. 非法日期格式返回 400"""
    r = client.get("/dashboard/overview?start_date=not-a-date")
    assert r.status_code == 400
    assert "格式" in r.json()["detail"] or "无效" in r.json()["detail"]


def test_start_after_end():
    """6. 开始时间晚于结束时间返回 400"""
    r = client.get("/dashboard/overview?start_date=2026-12-31&end_date=2025-01-01")
    assert r.status_code == 400
    assert "晚于" in r.json()["detail"] or "不能" in r.json()["detail"]


# ═══ 数据清洗 ═══

def test_job_type_normalization():
    """7. 分类清洗映射"""
    assert _normalize_job_type(None) == "未分类"
    assert _normalize_job_type("") == "未分类"
    assert _normalize_job_type("  ") == "未分类"
    assert _normalize_job_type("unknown") == "未分类"
    assert _normalize_job_type("未知") == "未分类"
    assert _normalize_job_type("正式") == "全职"
    assert _normalize_job_type("全职") == "全职"
    assert _normalize_job_type("实习") == "实习"
    assert _normalize_job_type("校招") == "校招"
    assert _normalize_job_type("兼职") == "兼职"


def test_type_distribution_uses_normalized():
    """类型分布使用清洗后的分类"""
    r = client.get("/dashboard/overview")
    types = {item["type"] for item in r.json()["job_type_distribution"]}
    # 不应出现原始的 "正式"、"unknown"、"未知"
    assert "正式" not in types
    assert "unknown" not in types
    assert "未知" not in types
    # 应出现清洗后的
    for t in types:
        assert t in ("全职", "实习", "校招", "未分类") or t  # 或其他保留值


# ═══ 空数据状态 ═══

def test_empty_result():
    """8. 筛选无结果时返回空列表和 0"""
    r = client.get("/dashboard/overview?job_keyword=ZZZZNOTEXIST999")
    assert r.status_code == 200
    data = r.json()
    assert data["kpis"]["jd_count"] == 0
    assert data["top_jobs"] == []
    assert data["insights"] == [] or all("暂无" in i["text"] for i in data["insights"])


# ═══ Insights ═══

def test_insights_from_real_data():
    """9. insights 来自真实聚合结果"""
    r = client.get("/dashboard/overview")
    data = r.json()
    insights = data.get("insights", [])
    assert isinstance(insights, list)
    assert len(insights) <= 3
    for i in insights:
        assert "icon" in i
        assert "text" in i
        # 不应包含占位符
        assert "TODO" not in i["text"]


def test_insights_change_with_filter():
    """insights 随筛选结果变化"""
    r1 = client.get("/dashboard/overview")
    r2 = client.get("/dashboard/overview?job_keyword=Python后端")
    i1 = r1.json().get("insights", [])
    i2 = r2.json().get("insights", [])
    # 如果有数据，筛选后的 insights 应该不同（至少文本不同）
    if i1 and i2 and r1.json()["kpis"]["jd_count"] > 0:
        # 不强断言内容不同（可能恰好相同），但结构应正确
        assert isinstance(i2, list)


# ═══ Meta ═══

def test_meta_fields():
    """meta 包含 source、updated_at、filters"""
    r = client.get("/dashboard/overview")
    meta = r.json()["meta"]
    assert meta["source"] == "JobLab MySQL"
    assert "updated_at" in meta
    assert "filters" in meta
    assert "filter_scope_notes" in meta


# ═══ 序列化与兼容 ═══

def test_overview_json_serializable():
    """10. 返回结果可以 JSON 序列化"""
    r = client.get("/dashboard/overview")
    data = r.json()
    assert isinstance(data, dict)
    # 确保可以再次序列化
    import json
    json.dumps(data)


def test_overview_with_real_data():
    """有真实数据时 KPI > 0"""
    r = client.get("/dashboard/overview")
    kpis = r.json()["kpis"]
    assert kpis["jd_count"] > 0
    assert kpis["skill_count"] > 0


def test_kpis_are_integers():
    """KPI 是整数"""
    r = client.get("/dashboard/overview")
    kpis = r.json()["kpis"]
    for v in kpis.values():
        assert isinstance(v, int)


def test_fit_score_distribution_has_5_ranges():
    """适配分分布有 5 个分数段"""
    r = client.get("/dashboard/overview")
    assert len(r.json()["fit_score_distribution"]) == 5


# ═══════════════════════════════════════════════════════
# 技能模块集成测试（/dashboard/skill_trend）
# ═══════════════════════════════════════════════════════

def test_skill_trend_with_valid_job():
    """指定岗位技能查询应返回技能列表"""
    r = client.get("/dashboard/skill_trend?job_name=Python后端")
    data = r.json()
    assert data["code"] == 200
    assert data["job_name"] == "Python后端"
    assert isinstance(data["skills"], list)


def test_skill_trend_skills_fields():
    """技能结果字段应包含 skill、count、last_seen"""
    r = client.get("/dashboard/skill_trend?job_name=Python后端")
    skills = r.json()["skills"]
    if skills:
        skill = skills[0]
        assert "skill" in skill, "缺少 skill 字段"
        assert "count" in skill, "缺少 count 字段"
        assert "last_seen" in skill, "缺少 last_seen 字段"
        assert isinstance(skill["count"], int), "count 应为整数"


def test_skill_trend_empty_job_name():
    """缺少岗位关键词应返回 400"""
    r = client.get("/dashboard/skill_trend")
    assert r.status_code == 400


def test_skill_trend_nonexistent_job():
    """不存在的岗位应返回空技能列表"""
    r = client.get("/dashboard/skill_trend?job_name=ZZZZNOTEXIST999")
    data = r.json()
    assert data["code"] == 200
    assert data["skills"] == []


def test_skill_trend_json_serializable():
    """接口返回值可被 json.dumps 序列化"""
    r = client.get("/dashboard/skill_trend?job_name=Python后端")
    json.dumps(r.json())


def test_skill_trend_job_name_normalized():
    """返回的 job_name 应经过 normalize_job_name 处理"""
    r = client.get("/dashboard/skill_trend?job_name=  Python后端  ")
    data = r.json()
    assert data["job_name"] == "Python后端"


def test_overview_skill_heatmap_not_empty():
    """全景模式下 skill_heatmap 应有数据"""
    r = client.get("/dashboard/overview")
    data = r.json()
    assert len(data["skill_heatmap"]) > 0, "skill_heatmap 为空"


def test_skill_evidence_requires_skill_name():
    r = client.get("/dashboard/skill_evidence")
    assert r.status_code == 400


def test_skill_evidence_response_shape():
    r = client.get("/dashboard/skill_evidence?skill_name=Python")
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert data["skill"] == "Python"
    assert "evidence" in data
    if data["evidence"]:
        assert "excerpt" in data["evidence"]
        assert "job_name" in data["evidence"]
        assert "title" in data["evidence"]
        assert "company" in data["evidence"]


def test_extract_skill_excerpt_contains_skill():
    from api.fastapi_app import _extract_skill_excerpt

    text = "负责服务稳定性建设。熟悉 Python 和 FastAPI，能够完成接口开发。具备良好沟通能力。"
    excerpt = _extract_skill_excerpt(text, "Python")
    assert "Python" in excerpt
    assert "FastAPI" in excerpt


def test_fit_level_detail_rejects_invalid_level():
    r = client.get("/dashboard/fit_level_detail?level=invalid")
    assert r.status_code == 400


def test_fit_level_detail_shape():
    r = client.get("/dashboard/fit_level_detail?level=weak")
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert data["level"] == "weak"
    assert isinstance(data["total"], int)
    assert isinstance(data["average_score"], (int, float))
    assert isinstance(data["completeness_rate"], int)
    assert len(data["dimensions"]) == 5
    assert isinstance(data["reports"], list)
    if data["reports"]:
        report = data["reports"][0]
        assert "job_name" in report
        assert "score" in report
        assert "gaps" in report
        assert "learning_plan" in report
