"""Golden Set 自动评测脚本 — v0.14

用法：
  python eval/run_golden_eval.py              # 使用规则 fallback
  python eval/run_golden_eval.py --use-agent  # 允许调用真实 Agent
  python eval/run_golden_eval.py --limit 2    # 只跑前 2 个 case
  python eval/run_golden_eval.py --output eval/reports/custom.json
"""
from __future__ import annotations
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_golden_set(path: str = None) -> list[dict]:
    p = Path(path) if path else Path(__file__).parent / "golden_set_v1.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


_SYNONYM_MAP = {
    # 工程技术
    "mysql": "mysql", "react": "react", "react.js": "react", "reactjs": "react",
    "vue": "vue", "vue.js": "vue", "vuejs": "vue",
    "node": "node.js", "nodejs": "node.js",
    "k8s": "kubernetes", "kubernetes": "kubernetes",
    "rest api": "restful api", "restful api": "restful api",
    "postgresql": "postgresql", "postgres": "postgresql",
    "go": "go", "golang": "go",
    "js": "javascript", "javascript": "javascript",
    "ts": "typescript", "typescript": "typescript",
    # AI/LLM
    "llm": "llm", "大模型": "llm", "大语言模型": "llm",
    "rag": "rag", "检索增强生成": "rag", "检索增强": "rag",
    "agent": "agent", "智能体": "agent", "ai agent": "agent",
    "ai": "ai", "人工智能": "ai",
    # 产品
    "prd": "prd", "产品需求文档": "prd", "需求文档": "prd",
    "用户调研": "用户研究", "用户访谈": "用户研究",
    "竞品调研": "竞品分析",
    "axure rp": "axure", "axure": "axure",
    "figma": "figma",
    "ab测试": "a/b测试", "ab test": "a/b测试",
    "原型": "原型设计",
    # 数据分析
    "power bi": "powerbi", "powerbi": "powerbi",
    "business intelligence": "bi",
}


def _normalize_for_match(text: str) -> str:
    """归一化关键词用于匹配"""
    t = text.lower().strip()
    return _SYNONYM_MAP.get(t, t)


def _keyword_hit_rate(gold_keywords: list[str], actual_items: list, key: str = "") -> tuple[float, list[str]]:
    """计算关键词命中率，支持包含式匹配 + 同义词归一化"""
    if not gold_keywords:
        return 1.0, []
    actual_set = set()
    for item in actual_items:
        if isinstance(item, str):
            actual_set.add(_normalize_for_match(item))
        elif isinstance(item, dict):
            val = item.get(key or "skill", item.get("name", item.get("description", "")))
            if isinstance(val, str):
                actual_set.add(_normalize_for_match(val))
    hit = 0
    missed = []
    for kw in gold_keywords:
        kw_norm = _normalize_for_match(kw)
        if any(kw_norm in a or a in kw_norm for a in actual_set):
            hit += 1
        else:
            missed.append(kw)
    return round(hit / len(gold_keywords), 3), missed


def _evaluate_case(case: dict, job_profile: dict, candidate_profile: dict, fit_report: dict) -> dict:
    """对单个 case 做关键词匹配评估"""
    gold_job = case.get("gold_job_profile", {})
    gold_cand = case.get("gold_candidate_profile", {})
    gold_fit = case.get("gold_fit", {})

    # ── 岗位画像评估（原有） ──
    jp_must_hit, jp_must_miss = _keyword_hit_rate(
        gold_job.get("must_have_capabilities", []),
        job_profile.get("must_have_capabilities", []),
    )
    jp_resp_hit, jp_resp_miss = _keyword_hit_rate(
        gold_job.get("responsibilities_keywords", []),
        job_profile.get("responsibilities", []),
    )
    job_profile_score = round((jp_must_hit * 0.7 + jp_resp_hit * 0.3) * 100, 1)

    # ── 岗位画像细分指标（v0.28 新增） ──
    jp_nice_hit, jp_nice_miss = _keyword_hit_rate(
        gold_job.get("nice_to_have_capabilities", []),
        job_profile.get("nice_to_have_capabilities", []),
    )
    jp_biz_hit, jp_biz_miss = _keyword_hit_rate(
        gold_job.get("business_context_keywords", []),
        job_profile.get("business_context", []),
    )
    jp_growth_hit, jp_growth_miss = _keyword_hit_rate(
        gold_job.get("growth_context_keywords", []),
        job_profile.get("growth_context", []),
    )

    # employment_type 匹配
    gold_emp = gold_job.get("employment_type", "")
    actual_emp = job_profile.get("employment_type", "")
    emp_match = (gold_emp == actual_emp) if gold_emp else True

    # target_audience 匹配
    gold_aud = gold_job.get("target_audience", "")
    actual_aud = job_profile.get("target_audience", "")
    aud_match = (gold_aud == actual_aud) if gold_aud else True

    # requirement 综合命中率（education / major / experience）
    req_hits = 0
    req_total = 0
    gold_edu = gold_job.get("education_preference", "")
    if gold_edu:
        req_total += 1
        if gold_edu in (job_profile.get("education_preference", "") or ""):
            req_hits += 1
    gold_major = gold_job.get("major_preference", "")
    if gold_major:
        req_total += 1
        actual_major = job_profile.get("major_preference", "") or ""
        if any(m in actual_major for m in gold_major.split("、") if m):
            req_hits += 1
    gold_exp = gold_job.get("experience_requirement", "")
    if gold_exp:
        req_total += 1
        if gold_exp in (job_profile.get("experience_requirement", "") or ""):
            req_hits += 1
    req_match_rate = round(req_hits / max(1, req_total), 3)

    # evidence_coverage
    evidence_count = len(job_profile.get("evidence", []))
    has_resp = len(job_profile.get("responsibilities", [])) > 0
    has_must = len(job_profile.get("must_have_capabilities", [])) > 0
    has_biz = len(job_profile.get("business_context", [])) > 0
    evidence_coverage = round(sum([has_resp, has_must, has_biz]) / 3, 3)

    # ── 候选人画像评估 ──
    cand_skill_hit, cand_skill_miss = _keyword_hit_rate(
        gold_cand.get("skill_keywords", []),
        candidate_profile.get("skill_stack", []),
        key="skill",
    )
    cand_proj_hit, cand_proj_miss = _keyword_hit_rate(
        gold_cand.get("project_keywords", []),
        candidate_profile.get("projects", []),
        key="description",
    )
    cand_achieve_hit, cand_achieve_miss = _keyword_hit_rate(
        gold_cand.get("achievement_keywords", []),
        candidate_profile.get("achievements", []),
        key="description",
    )
    candidate_score = round((cand_skill_hit * 0.4 + cand_proj_hit * 0.4 + cand_achieve_hit * 0.2) * 100, 1)

    # v0.30 候选人画像字段级指标
    cand_edu_hit, cand_edu_miss = _keyword_hit_rate(
        gold_cand.get("education_keywords", []),
        [candidate_profile.get("education_background", {}).get("degree", ""),
         candidate_profile.get("education_background", {}).get("major", ""),
         candidate_profile.get("education_background", {}).get("graduation_year", "")],
    )
    cand_major_hit, cand_major_miss = _keyword_hit_rate(
        gold_cand.get("major_keywords", []),
        [candidate_profile.get("education_background", {}).get("major", "")],
    )
    cand_intern_hit, cand_intern_miss = _keyword_hit_rate(
        gold_cand.get("internship_keywords", []),
        candidate_profile.get("internships", []),
        key="description",
    )
    cand_work_hit, cand_work_miss = _keyword_hit_rate(
        gold_cand.get("work_experience_keywords", []),
        candidate_profile.get("work_experiences", []),
        key="description",
    )
    cand_learn_hit, cand_learn_miss = _keyword_hit_rate(
        gold_cand.get("learning_signal_keywords", []),
        candidate_profile.get("learning_signals", []),
    )
    # risk_point 匹配：检查 gold 风险关键词是否在 actual risk_points 中出现
    gold_risks = gold_cand.get("risk_keywords", [])
    actual_risks = candidate_profile.get("risk_points", [])
    risk_match = 0
    risk_total = len(gold_risks)
    if risk_total > 0:
        for gr in gold_risks:
            if any(gr in ar for ar in actual_risks):
                risk_match += 1
        risk_match_rate = round(risk_match / risk_total, 3)
    else:
        risk_match_rate = 1.0  # 没有期望风险时默认通过

    # evidence_coverage_candidate：项目/实习/工作/成果中有证据的比例
    cand_evidence_items = []
    if candidate_profile.get("projects"):
        cand_evidence_items.append(True)
    if candidate_profile.get("internships"):
        cand_evidence_items.append(True)
    if candidate_profile.get("work_experiences"):
        cand_evidence_items.append(True)
    if candidate_profile.get("achievements"):
        cand_evidence_items.append(True)
    cand_evidence_coverage = round(len(cand_evidence_items) / 4, 3) if cand_evidence_items else 0

    # ── 适配分析评估 ──
    actual_level = fit_report.get("overall_fit_level", "moderate")
    gold_level = gold_fit.get("overall_fit_level", "moderate")
    fit_level_match = actual_level == gold_level
    _near_set = {("strong", "moderate"), ("moderate", "strong")}
    fit_level_near_match = (actual_level, gold_level) in _near_set
    mismatch_reason = ""
    if not fit_level_match:
        mismatch_reason = f"gold={gold_level}, actual={actual_level}, score={fit_report.get('overall_score', '?')}"

    strengths_hit, strengths_miss = _keyword_hit_rate(
        gold_fit.get("expected_strengths", []),
        fit_report.get("strengths", []),
    )
    gaps_hit, gaps_miss = _keyword_hit_rate(
        gold_fit.get("expected_gaps", []),
        fit_report.get("gaps", []),
    )
    learning_hit, learning_miss = _keyword_hit_rate(
        gold_fit.get("expected_learning_keywords", []),
        fit_report.get("learning_plan", []),
    )

    # v0.31 适配分析字段级指标
    interview_hit, interview_miss = _keyword_hit_rate(
        gold_fit.get("interview_strategy_keywords", []),
        fit_report.get("interview_strategy", []),
    )
    risk_hit, risk_miss = _keyword_hit_rate(
        gold_fit.get("risk_keywords", []),
        fit_report.get("gaps", []) + fit_report.get("risks_and_gaps", {}).get("evidence_refs", []),
    )

    # score_in_expected_range
    score_range = gold_fit.get("expected_score_range", [])
    actual_score = fit_report.get("overall_score", 0)
    if score_range:
        score_in_range = score_range[0] <= actual_score <= score_range[1]
    else:
        score_in_range = True

    # evidence_ref_coverage_fit：strengths/gaps/learning_plan/interview_strategy 中有 evidence_refs 的比例
    fit_evidence_items = []
    if fit_report.get("strengths"):
        fit_evidence_items.append(True)
    if fit_report.get("gaps"):
        fit_evidence_items.append(True)
    if fit_report.get("learning_plan"):
        fit_evidence_items.append(True)
    if fit_report.get("interview_strategy"):
        fit_evidence_items.append(True)
    fit_evidence_coverage = round(len(fit_evidence_items) / 4, 3) if fit_evidence_items else 0

    # 幻觉标记
    hallucination_flags = []
    sys_must = set(s.lower() for s in job_profile.get("must_have_capabilities", []))
    gold_must = set(s.lower() for s in gold_job.get("must_have_capabilities", []))
    for s in sys_must - gold_must:
        hallucination_flags.append(f"job_profile.must_have 多出: {s}")

    total_score = round(job_profile_score * 0.3 + candidate_score * 0.3 + (
        (fit_level_match * 20) + (strengths_hit * 10) + (gaps_hit * 10) + (learning_hit * 10)
    ), 1)

    return {
        "passed": total_score >= 50 and (fit_level_match or fit_level_near_match),
        "score": total_score,
        "job_profile_score": job_profile_score,
        "candidate_profile_score": candidate_score,
        # v0.28/v0.30 字段级指标
        "field_scores": {
            # 岗位画像
            "responsibilities_hit_rate": round(jp_resp_hit * 100, 1),
            "must_have_hit_rate": round(jp_must_hit * 100, 1),
            "nice_to_have_hit_rate": round(jp_nice_hit * 100, 1),
            "business_context_hit_rate": round(jp_biz_hit * 100, 1),
            "growth_context_hit_rate": round(jp_growth_hit * 100, 1),
            "employment_type_match": emp_match,
            "target_audience_match": aud_match,
            "requirement_match_rate": round(req_match_rate * 100, 1),
            "evidence_coverage": round(evidence_coverage * 100, 1),
            # 候选人画像
            "cand_education_hit_rate": round(cand_edu_hit * 100, 1),
            "cand_major_hit_rate": round(cand_major_hit * 100, 1),
            "cand_skill_hit_rate": round(cand_skill_hit * 100, 1),
            "cand_project_hit_rate": round(cand_proj_hit * 100, 1),
            "cand_internship_hit_rate": round(cand_intern_hit * 100, 1),
            "cand_work_experience_hit_rate": round(cand_work_hit * 100, 1),
            "cand_achievement_hit_rate": round(cand_achieve_hit * 100, 1),
            "cand_learning_signal_hit_rate": round(cand_learn_hit * 100, 1),
            "cand_risk_point_match_rate": round(risk_match_rate * 100, 1),
            "cand_evidence_coverage": round(cand_evidence_coverage * 100, 1),
            # 适配分析
            "fit_level_match": fit_level_match,
            "fit_level_near_match": fit_level_near_match,
            "fit_score_in_range": score_in_range,
            "fit_strengths_hit_rate": round(strengths_hit * 100, 1),
            "fit_gaps_hit_rate": round(gaps_hit * 100, 1),
            "fit_learning_hit_rate": round(learning_hit * 100, 1),
            "fit_interview_hit_rate": round(interview_hit * 100, 1),
            "fit_risk_hit_rate": round(risk_hit * 100, 1),
            "fit_evidence_coverage": round(fit_evidence_coverage * 100, 1),
        },
        "fit_level_match": fit_level_match,
        "fit_level_near_match": fit_level_near_match,
        "actual_fit_level": actual_level,
        "mismatch_reason": mismatch_reason,
        "strengths_hit_rate": round(strengths_hit * 100, 1),
        "gaps_hit_rate": round(gaps_hit * 100, 1),
        "learning_plan_hit_rate": round(learning_hit * 100, 1),
        "missed_keywords": {
            "job_must_have": jp_must_miss,
            "job_responsibilities": jp_resp_miss,
            "job_nice_to_have": jp_nice_miss,
            "job_business_context": jp_biz_miss,
            "candidate_skills": cand_skill_miss,
            "candidate_projects": cand_proj_miss,
            "fit_strengths": strengths_miss,
            "fit_gaps": gaps_miss,
            "fit_learning": learning_miss,
        },
        "hallucination_flags": hallucination_flags,
        "notes": case.get("notes", ""),
    }


def run_golden_eval(use_agent: bool = False, limit: int = 0, output: str = None) -> dict:
    """运行 Golden Set 评测"""
    cases = _load_golden_set()
    if limit > 0:
        cases = cases[:limit]

    from services.job_profile_service import extract_job_profile
    from services.candidate_profile_service import extract_candidate_profile
    from services.fit_analysis_service import analyze_fit
    from services.profile_schemas import JobProfileResult, CandidateProfileResult

    results = []
    for i, case in enumerate(cases):
        print(f"[{i+1}/{len(cases)}] {case['case_id']}: {case['job_name']}...")

        # 生成岗位画像（使用 case 自带的 jd_texts，不依赖数据库）
        job_profile = extract_job_profile(case["job_name"], top_n=15, raw_jd_texts=case.get("jd_texts", []))
        job_dict = job_profile.model_dump()

        # 生成候选人画像
        cand_profile = extract_candidate_profile(resume_text=case.get("resume_text", ""))
        cand_dict = cand_profile.model_dump()

        # 适配分析（默认规则，可选 Agent）
        fit_report = analyze_fit(job_profile, cand_profile)
        if use_agent:
            try:
                from services.fit_analysis_agent import analyze_fit_with_agent
                fit_report, mode = analyze_fit_with_agent(job_profile, cand_profile, fit_report)
                print(f"  Agent mode: {mode}")
            except Exception as e:
                print(f"  Agent failed, using rule fallback: {e}")

        fit_dict = fit_report.model_dump()

        # 评估
        eval_result = _evaluate_case(case, job_dict, cand_dict, fit_dict)
        eval_result["case_id"] = case["case_id"]
        eval_result["job_name"] = case["job_name"]
        results.append(eval_result)

        status = "PASS" if eval_result["passed"] else "FAIL"
        print(f"  {status} score={eval_result['score']} "
              f"job={eval_result['job_profile_score']} "
              f"cand={eval_result['candidate_profile_score']} "
              f"fit_match={eval_result['fit_level_match']}")

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    avg_score = round(sum(r["score"] for r in results) / max(1, total), 1)
    avg_job = round(sum(r["job_profile_score"] for r in results) / max(1, total), 1)
    avg_cand = round(sum(r["candidate_profile_score"] for r in results) / max(1, total), 1)
    fit_match_rate = round(sum(1 for r in results if r["fit_level_match"]) / max(1, total) * 100, 1)

    # v0.28 字段级平均指标
    def _avg_field(key):
        vals = [r["field_scores"][key] for r in results if "field_scores" in r and key in r["field_scores"]]
        return round(sum(vals) / max(1, len(vals)), 1) if vals else 0

    def _avg_field_bool(key):
        vals = [r["field_scores"][key] for r in results if "field_scores" in r and key in r["field_scores"]]
        return round(sum(1 for v in vals if v) / max(1, len(vals)) * 100, 1) if vals else 0

    summary = {
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / max(1, total) * 100, 1),
        "avg_score": avg_score,
        "avg_job_profile_score": avg_job,
        "avg_candidate_profile_score": avg_cand,
        "fit_level_match_rate": fit_match_rate,
        # v0.28 岗位画像字段级指标
        "avg_responsibilities_hit_rate": _avg_field("responsibilities_hit_rate"),
        "avg_must_have_hit_rate": _avg_field("must_have_hit_rate"),
        "avg_nice_to_have_hit_rate": _avg_field("nice_to_have_hit_rate"),
        "avg_business_context_hit_rate": _avg_field("business_context_hit_rate"),
        "avg_growth_context_hit_rate": _avg_field("growth_context_hit_rate"),
        "employment_type_match_rate": _avg_field_bool("employment_type_match"),
        "target_audience_match_rate": _avg_field_bool("target_audience_match"),
        "avg_requirement_match_rate": _avg_field("requirement_match_rate"),
        "avg_evidence_coverage": _avg_field("evidence_coverage"),
        # v0.30 候选人画像字段级指标
        "avg_cand_education_hit_rate": _avg_field("cand_education_hit_rate"),
        "avg_cand_major_hit_rate": _avg_field("cand_major_hit_rate"),
        "avg_cand_skill_hit_rate": _avg_field("cand_skill_hit_rate"),
        "avg_cand_project_hit_rate": _avg_field("cand_project_hit_rate"),
        "avg_cand_internship_hit_rate": _avg_field("cand_internship_hit_rate"),
        "avg_cand_work_experience_hit_rate": _avg_field("cand_work_experience_hit_rate"),
        "avg_cand_achievement_hit_rate": _avg_field("cand_achievement_hit_rate"),
        "avg_cand_learning_signal_hit_rate": _avg_field("cand_learning_signal_hit_rate"),
        "avg_cand_risk_point_match_rate": _avg_field("cand_risk_point_match_rate"),
        "avg_cand_evidence_coverage": _avg_field("cand_evidence_coverage"),
        # v0.31 适配分析字段级指标
        "fit_level_exact_match_rate": _avg_field_bool("fit_level_match"),
        "fit_level_near_match_rate": _avg_field_bool("fit_level_near_match"),
        "fit_score_in_range_rate": _avg_field_bool("fit_score_in_range"),
        "avg_fit_strengths_hit_rate": _avg_field("fit_strengths_hit_rate"),
        "avg_fit_gaps_hit_rate": _avg_field("fit_gaps_hit_rate"),
        "avg_fit_learning_hit_rate": _avg_field("fit_learning_hit_rate"),
        "avg_fit_interview_hit_rate": _avg_field("fit_interview_hit_rate"),
        "avg_fit_risk_hit_rate": _avg_field("fit_risk_hit_rate"),
        "avg_fit_evidence_coverage": _avg_field("fit_evidence_coverage"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "use_agent": use_agent,
    }

    report = {"summary": summary, "cases": results}

    # 写入报告
    out_path = output or str(Path(__file__).parent / "reports" / "golden_eval_latest.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 控制台摘要
    print(f"\n{'='*50}")
    print(f"  Golden Set 评测完成")
    print(f"  总数: {total}  通过: {passed}  失败: {total - passed}")
    print(f"  通过率: {summary['pass_rate']}%")
    print(f"  平均分: {avg_score}")
    print(f"  岗位画像平均分: {avg_job}")
    print(f"  候选人画像平均分: {avg_cand}")
    print(f"  适配等级匹配率: {fit_match_rate}%")
    print(f"  --- 字段级指标 ---")
    print(f"  职责命中率: {summary['avg_responsibilities_hit_rate']}%")
    print(f"  必备能力命中率: {summary['avg_must_have_hit_rate']}%")
    print(f"  加分能力命中率: {summary['avg_nice_to_have_hit_rate']}%")
    print(f"  业务场景命中率: {summary['avg_business_context_hit_rate']}%")
    print(f"  成长信号命中率: {summary['avg_growth_context_hit_rate']}%")
    print(f"  用工类型匹配率: {summary['employment_type_match_rate']}%")
    print(f"  面向人群匹配率: {summary['target_audience_match_rate']}%")
    print(f"  要求匹配率: {summary['avg_requirement_match_rate']}%")
    print(f"  证据覆盖率: {summary['avg_evidence_coverage']}%")
    print(f"  --- 候选人画像字段级指标 ---")
    print(f"  学历命中率: {summary['avg_cand_education_hit_rate']}%")
    print(f"  专业命中率: {summary['avg_cand_major_hit_rate']}%")
    print(f"  技能栈命中率: {summary['avg_cand_skill_hit_rate']}%")
    print(f"  项目命中率: {summary['avg_cand_project_hit_rate']}%")
    print(f"  实习命中率: {summary['avg_cand_internship_hit_rate']}%")
    print(f"  工作经历命中率: {summary['avg_cand_work_experience_hit_rate']}%")
    print(f"  成果命中率: {summary['avg_cand_achievement_hit_rate']}%")
    print(f"  学习信号命中率: {summary['avg_cand_learning_signal_hit_rate']}%")
    print(f"  风险点匹配率: {summary['avg_cand_risk_point_match_rate']}%")
    print(f"  候选人证据覆盖率: {summary['avg_cand_evidence_coverage']}%")
    print(f"  --- 适配分析字段级指标 ---")
    print(f"  适配等级精确匹配率: {summary['fit_level_exact_match_rate']}%")
    print(f"  适配等级近似匹配率: {summary['fit_level_near_match_rate']}%")
    print(f"  分数在期望范围内: {summary['fit_score_in_range_rate']}%")
    print(f"  优势命中率: {summary['avg_fit_strengths_hit_rate']}%")
    print(f"  差距命中率: {summary['avg_fit_gaps_hit_rate']}%")
    print(f"  学习计划命中率: {summary['avg_fit_learning_hit_rate']}%")
    print(f"  面试策略命中率: {summary['avg_fit_interview_hit_rate']}%")
    print(f"  风险命中率: {summary['avg_fit_risk_hit_rate']}%")
    print(f"  适配证据覆盖率: {summary['avg_fit_evidence_coverage']}%")
    print(f"  报告: {out_path}")
    print(f"{'='*50}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Golden Set 评测脚本")
    parser.add_argument("--use-agent", action="store_true", help="调用真实 FitAnalysisAgent")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个 case")
    parser.add_argument("--output", type=str, default=None, help="输出路径")
    args = parser.parse_args()

    run_golden_eval(use_agent=args.use_agent, limit=args.limit, output=args.output)
