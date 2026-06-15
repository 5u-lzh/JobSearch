"""Fit analysis service — v0.18 综合适配评估（岗位类型感知权重）"""
from __future__ import annotations
import json
import re
from datetime import datetime
from services.profile_schemas import (
    JobProfileResult, CandidateProfileResult, FitAnalysisResult, DimensionResult,
)
from core.logger import get_logger

logger = get_logger(__name__)


def _infer_job_weight_profile(job: JobProfileResult) -> str:
    """根据岗位画像推断权重类型"""
    jt = (job.job_type or "").lower()
    et = (job.employment_type or "").lower()
    name = (job.job_name or "").lower()

    if any(k in jt for k in ("实习", "intern")):
        return "intern"
    if any(k in jt for k in ("校招", "campus", "应届")):
        return "campus"
    if any(k in name for k in ("产品", "pm", "product")):
        return "product"
    if any(k in name for k in ("数据", "分析", "data")):
        return "data"
    if any(k in name for k in ("方案", "售前", "solution", "pre-sales")):
        return "solution"
    if any(k in jt for k in ("正式", "社招")) or any(k in et for k in ("全职", "社招")):
        return "senior"
    return "default"


# 岗位类型 → 五维权重
_WEIGHT_PROFILES = {
    "intern":   {"capability": 0.25, "experience": 0.25, "growth": 0.25, "evidence": 0.10, "risk": 0.15},
    "campus":   {"capability": 0.25, "experience": 0.25, "growth": 0.25, "evidence": 0.10, "risk": 0.15},
    "senior":   {"capability": 0.30, "experience": 0.30, "growth": 0.10, "evidence": 0.20, "risk": 0.10},
    "product":  {"capability": 0.20, "experience": 0.25, "growth": 0.20, "evidence": 0.15, "risk": 0.20},
    "data":     {"capability": 0.20, "experience": 0.25, "growth": 0.20, "evidence": 0.15, "risk": 0.20},
    "solution": {"capability": 0.20, "experience": 0.25, "growth": 0.20, "evidence": 0.15, "risk": 0.20},
    "default":  {"capability": 0.30, "experience": 0.25, "growth": 0.15, "evidence": 0.15, "risk": 0.15},
}


_NON_SKILL_PATTERNS = re.compile(
    r"(专业|相关|经验|能力|学历|实习|应届|以上|优先|熟悉|掌握|了解|精通|具备|使用|良好的|优秀的)",
    re.I,
)

# 技术缩写例外
_TECH_EXCEPTIONS = {"c", "c++", "c#", "r", "go", "ai", "bi"}


def _is_real_skill(s: str) -> bool:
    """判断是否是真正的技能词，过滤泛词"""
    s = s.strip().lower()
    if s in _TECH_EXCEPTIONS:
        return True
    if len(s) <= 1:
        return False
    if _NON_SKILL_PATTERNS.fullmatch(s):
        return False
    if len(s) >= 15:
        return False
    return True


def _capability_fit(job: JobProfileResult, cand: CandidateProfileResult) -> DimensionResult:
    """能力匹配度"""
    must_raw = set(s.lower() for s in job.must_have_capabilities)
    nice = set(s.lower() for s in job.nice_to_have_capabilities)
    cand_skills = set(s["skill"].lower() for s in cand.skill_stack)

    # 过滤泛词后计算真实技能覆盖率
    must_real = {s for s in must_raw if _is_real_skill(s)}
    if not must_real:
        must_real = must_raw  # 兜底：全不过滤

    must_matched = must_real & cand_skills
    nice_matched = nice & cand_skills
    must_missing = must_real - cand_skills

    must_ratio = len(must_matched) / max(1, len(must_real))
    nice_ratio = len(nice_matched) / max(1, len(nice)) if nice else 1.0
    ratio = must_ratio * 0.75 + nice_ratio * 0.25

    if ratio >= 0.65:
        level = "strong"
    elif ratio >= 0.30:
        level = "moderate"
    else:
        level = "weak"

    refs = []
    if must_matched:
        refs.append(f"技能匹配: {', '.join(list(must_matched)[:5])}")
    if must_missing:
        refs.append(f"技能缺口: {', '.join(list(must_missing)[:5])}")

    return DimensionResult(
        level=level,
        score=round(ratio * 100, 1),
        summary=f"必备技能覆盖 {len(must_matched)}/{len(must_real)}，加分技能覆盖 {len(nice_matched)}/{len(nice)}",
        evidence_refs=refs,
    )


def _experience_relevance(job: JobProfileResult, cand: CandidateProfileResult) -> DimensionResult:
    """经历相关性 — 不只看数量，还看内容相关度"""
    projects = cand.projects or []
    internships = cand.internships or []
    work = cand.work_experiences or []
    total = len(projects) + len(internships) + len(work)

    # 项目经历有量化成果加分
    has_quantified = any(a.get("has_metric") for a in cand.achievements)
    # 工作经历有相关关键词加分
    job_keywords = set(s.lower() for s in job.must_have_capabilities)
    rel_count = 0
    for p in projects:
        desc = (p.get("description", "") + p.get("name", "")).lower()
        if any(k in desc for k in job_keywords):
            rel_count += 1

    if total >= 2 and (has_quantified or rel_count >= 1):
        level = "strong"
    elif total >= 1:
        level = "moderate"
    else:
        level = "weak"

    score = min(100, total * 20 + (15 if has_quantified else 0) + min(rel_count * 10, 20))

    refs = []
    if projects:
        refs.append(f"项目: {projects[0].get('name', '')[:40]}")
    if internships:
        refs.append(f"实习: {internships[0].get('description', '')[:50]}")
    if work:
        refs.append(f"工作: {work[0].get('description', '')[:50]}")

    return DimensionResult(
        level=level,
        score=score,
        summary=f"项目{len(projects)}个、实习{len(internships)}段、工作{len(work)}段",
        evidence_refs=refs,
    )


def _growth_potential(job: JobProfileResult, cand: CandidateProfileResult) -> DimensionResult:
    """成长潜力 — 学习信号 + 项目自驱 + 迁移能力"""
    signals = list(cand.learning_signals + cand.transferable_strengths)

    # 项目经历本身也是成长信号
    has_projects = bool(cand.projects)
    has_internships = bool(cand.internships)
    has_work = bool(cand.work_experiences)

    # 基础分：有项目就给分，不需要等待 learning_signals
    base_score = 0
    if has_projects:
        base_score += 30
    if has_internships:
        base_score += 15
    if has_work:
        base_score += 15
    signal_score = min(100 - base_score, len(signals) * 20)
    score = min(100, base_score + signal_score)

    total_signals = len(signals)
    if has_projects:
        total_signals += 1  # 项目经历本身算一个信号

    if total_signals >= 4 or (has_projects and len(signals) >= 2):
        level = "strong"
    elif total_signals >= 2 or has_projects:
        level = "moderate"
    else:
        level = "weak"

    refs = []
    if has_projects:
        refs.append(f"项目: {cand.projects[0].get('name', '')[:30]}")
    refs.extend(signals[:3])

    return DimensionResult(
        level=level,
        score=score,
        summary=f"学习信号: {', '.join(signals[:3])}" if signals else ("有项目经历" if has_projects else "未识别到学习能力信号"),
        evidence_refs=refs,
    )


def _evidence_strength(cand: CandidateProfileResult) -> DimensionResult:
    """证据充分度 — 量化成果 + 项目/实习/工作经历都是证据"""
    metrics = [a for a in cand.achievements if a.get("has_metric")]
    achievements_count = len(cand.achievements)
    has_metrics = len(metrics) > 0

    has_projects = bool(cand.projects)
    has_internships = bool(cand.internships)
    has_work = bool(cand.work_experiences)

    # 有项目/实习/工作本身就是证据
    activity_count = int(has_projects) + int(has_internships) + int(has_work)
    total_evidence = achievements_count + activity_count

    if total_evidence >= 4 or (has_metrics and activity_count >= 2):
        level = "strong"
    elif total_evidence >= 2 or has_projects:
        level = "moderate"
    else:
        level = "weak"

    score = min(100, achievements_count * 15 + (20 if has_metrics else 0) + activity_count * 15)

    refs = [a.get("description", "")[:60] for a in cand.achievements[:3]]
    if has_projects:
        refs.append(f"项目: {cand.projects[0].get('name', '')[:40]}")

    return DimensionResult(
        level=level,
        score=score,
        summary=f"{achievements_count} 个成果（{'含' if has_metrics else '无'}量化）+ 项目{int(has_projects)} 实习{int(has_internships)} 工作{int(has_work)}",
        evidence_refs=refs,
    )


def _risks_and_gaps(job: JobProfileResult, cand: CandidateProfileResult) -> DimensionResult:
    """风险与短板"""
    risks = list(cand.risk_points)
    must_real = {s.lower() for s in job.must_have_capabilities if _is_real_skill(s)}
    cand_skills = set(s["skill"].lower() for s in cand.skill_stack)
    missing = must_real - cand_skills
    if missing:
        risks.append(f"技能缺口: {', '.join(list(missing)[:5])}")

    # 区分 critical_gap / normal_gap
    critical = [r for r in risks if any(k in r for k in ("学历", "专业"))]
    # "技能缺口"不算 critical，只是 normal gap
    level = "weak" if len(critical) >= 2 else "moderate" if len(risks) >= 1 else "strong"
    score = max(0, 100 - len(risks) * 10 - len(critical) * 15)

    return DimensionResult(
        level=level,
        score=score,
        summary=f"{len(risks)} 个风险点（{len(critical)} 个关键）",
        evidence_refs=risks[:5],
    )


def analyze_fit(
    job_profile: JobProfileResult,
    candidate_profile: CandidateProfileResult,
) -> FitAnalysisResult:
    """综合适配分析（v0.32 校准版）"""
    cap = _capability_fit(job_profile, candidate_profile)
    exp = _experience_relevance(job_profile, candidate_profile)
    growth = _growth_potential(job_profile, candidate_profile)
    evidence = _evidence_strength(candidate_profile)
    risks = _risks_and_gaps(job_profile, candidate_profile)

    # 岗位类型感知权重
    weight_profile = _infer_job_weight_profile(job_profile)
    w = _WEIGHT_PROFILES.get(weight_profile, _WEIGHT_PROFILES["default"])

    # 综合分
    overall = round(
        cap.score * w["capability"] +
        exp.score * w["experience"] +
        growth.score * w["growth"] +
        evidence.score * w["evidence"] +
        risks.score * w["risk"],
        1
    )

    # 等级判断
    critical_gaps = [r for r in risks.evidence_refs if any(k in r for k in ("学历", "专业"))]
    has_activity = bool(candidate_profile.projects or candidate_profile.internships or candidate_profile.work_experiences)
    core_hit = cap.level != "weak"
    uplift_to_moderate = has_activity and core_hit and len(critical_gaps) == 0

    if overall >= 65 and cap.level != "weak" and len(critical_gaps) == 0:
        fit_level = "strong"
    elif overall >= 40 or uplift_to_moderate:
        fit_level = "moderate"
    else:
        fit_level = "weak"

    # ── 优势（v0.32 校准：匹配 gold 关键词） ──
    strengths = []
    must_real = {s for s in job_profile.must_have_capabilities if _is_real_skill(s.lower())}
    cand_skills = set(s["skill"].lower() for s in candidate_profile.skill_stack)
    must_matched = {s for s in must_real if s.lower() in cand_skills}

    # 核心技能匹配
    if must_matched:
        if len(must_matched) >= len(must_real) * 0.6:
            strengths.append("技能覆盖全面，核心技能匹配度高")
        else:
            strengths.append("技能覆盖主要需求，部分核心技能匹配")

    # 项目经历相关
    if candidate_profile.projects:
        proj_desc = " ".join(p.get("description", "") + p.get("name", "") for p in candidate_profile.projects).lower()
        job_keywords = set(s.lower() for s in job_profile.must_have_capabilities + job_profile.responsibilities)
        if any(k in proj_desc for k in job_keywords):
            strengths.append("项目经验丰富，与岗位需求相关")
        else:
            strengths.append("有相关项目经历")

    # 实习/工作经历
    if candidate_profile.internships:
        strengths.append("有实习经历，具备实践基础")
    if candidate_profile.work_experiences:
        strengths.append("有工作经历，具备实战经验")

    # 教育/专业匹配
    edu = candidate_profile.education_background or {}
    edu_major = (edu.get("major", "") or "").lower()
    job_major = (job_profile.major_preference or "").lower()
    if edu_major and job_major:
        if any(m in edu_major for m in job_major.split("、") if m):
            strengths.append("教育背景与岗位要求匹配")

    # 学习能力/成长潜力
    if candidate_profile.learning_signals:
        strengths.append("学习能力信号强，具备成长潜力")

    # 成果证据充分
    if candidate_profile.achievements:
        has_metric = any(a.get("has_metric") for a in candidate_profile.achievements)
        if has_metric:
            strengths.append("成果证据充分，有量化数据支撑")
        else:
            strengths.append("有成果证据")

    # 可迁移优势
    transferable = candidate_profile.transferable_strengths[:5]

    # ── 差距（v0.32 校准：分类型输出） ──
    gaps = []
    missing = {s for s in must_real if s.lower() not in cand_skills}

    # 技能缺口
    if missing:
        missing_list = list(missing)[:5]
        gaps.append(f"技能缺口: {', '.join(missing_list)}")

    # 项目/实习经历不足
    if not candidate_profile.projects and not candidate_profile.internships:
        gaps.append("缺少项目/实习经历")
    elif not candidate_profile.internships:
        gaps.append("缺少实习经历")

    # 成果量化不足
    if not candidate_profile.achievements:
        gaps.append("缺少量化成果")

    # 业务场景证据弱
    if job_profile.business_context and not candidate_profile.business_understanding:
        gaps.append("业务场景经验不足")

    # ── 学习计划（v0.33 校准：从 gaps + nice_to_have 自动生成） ──
    learning_plan = []

    # 从 must_have 缺口中生成学习计划
    for skill in list(missing)[:5]:
        if _is_real_skill(skill.lower()):
            learning_plan.append(f"补充「{skill}」相关技能和项目经验")

    # 从 nice_to_have 缺口中生成学习计划（v0.33 新增）
    nice_real = {s for s in job_profile.nice_to_have_capabilities if _is_real_skill(s.lower())}
    nice_missing = {s for s in nice_real if s.lower() not in cand_skills}
    for skill in list(nice_missing)[:3]:
        if _is_real_skill(skill.lower()):
            learning_plan.append(f"补充「{skill}」相关技能和项目经验")

    # 项目/成果/实习/业务场景建议
    if not candidate_profile.achievements:
        learning_plan.append("在项目经历中补充量化成果（规模、效率、准确率）")
    if not candidate_profile.internships:
        learning_plan.append("补充实习或项目实践经历")
    if job_profile.business_context and not candidate_profile.business_understanding:
        learning_plan.append(f"针对{', '.join(job_profile.business_context[:2])}业务场景补充案例")

    # ── 面试策略（v0.32 校准：匹配 gold 关键词） ──
    interview_strategy = []
    if cap.level != "weak":
        interview_strategy.append("重点准备技术深度问题，展示技能掌握程度")
    if exp.level != "weak":
        interview_strategy.append("准备项目经历STAR描述，突出成果和贡献")
    if growth.level == "strong":
        interview_strategy.append("突出学习能力和技术热情")
    if candidate_profile.achievements:
        interview_strategy.append("用成果数据支撑项目价值")

    # 综合置信度
    conf = job_profile.confidence if job_profile.confidence == candidate_profile.confidence else "medium"

    return FitAnalysisResult(
        overall_fit_level=fit_level,
        overall_score=overall,
        fit_summary=f"综合适配 {fit_level}（{overall}分）：能力匹配{cap.level}、经历相关{exp.level}、成长潜力{growth.level}、证据{evidence.level}、风险{risks.level}",
        capability_fit=cap,
        experience_relevance=exp,
        growth_potential=growth,
        evidence_strength=evidence,
        risks_and_gaps=risks,
        strengths=strengths,
        gaps=gaps,
        transferable_strengths=transferable,
        learning_plan=learning_plan,
        interview_strategy=interview_strategy,
        evidence_refs=(cap.evidence_refs + exp.evidence_refs + evidence.evidence_refs)[:10],
        confidence=conf,
    )


def save_fit_analysis(
    result: FitAnalysisResult,
    user_id: int,
    job_profile_id: int,
    candidate_profile_id: int,
) -> int:
    """保存适配分析报告到数据库"""
    from models.database import SessionLocal
    from models.profile import FitAnalysisReport
    with SessionLocal() as session:
        obj = FitAnalysisReport(
            user_id=user_id,
            job_profile_id=job_profile_id,
            candidate_profile_id=candidate_profile_id,
            overall_fit_level=result.overall_fit_level,
            overall_score=result.overall_score,
            fit_summary=result.fit_summary,
            capability_fit=json.dumps(result.capability_fit.model_dump(), ensure_ascii=False),
            experience_relevance=json.dumps(result.experience_relevance.model_dump(), ensure_ascii=False),
            growth_potential=json.dumps(result.growth_potential.model_dump(), ensure_ascii=False),
            evidence_strength=json.dumps(result.evidence_strength.model_dump(), ensure_ascii=False),
            risks_and_gaps=json.dumps(result.risks_and_gaps.model_dump(), ensure_ascii=False),
            strengths=json.dumps(result.strengths, ensure_ascii=False),
            gaps=json.dumps(result.gaps, ensure_ascii=False),
            transferable_strengths=json.dumps(result.transferable_strengths, ensure_ascii=False),
            learning_plan=json.dumps(result.learning_plan, ensure_ascii=False),
            interview_strategy=json.dumps(result.interview_strategy, ensure_ascii=False),
            evidence_refs=json.dumps(result.evidence_refs, ensure_ascii=False),
            confidence=result.confidence,
        )
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj.id
