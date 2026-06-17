"""Candidate profile extraction service — v0.35 增强提取质量

改进点：
- 院校识别：支持 XX大学/XX学院/XX职业技术学院/XX University
- 专业识别：优先匹配明确声明，避免把经历词误识别为专业
- 学历识别：本科/硕士/研究生/博士/大专
- 毕业年份：2026届/2026年毕业/毕业时间：2026
- 项目经历：从段落中拆分项目，识别项目名、技术栈、职责、成果
- 实习/工作经历：从段落中拆分，带公司/角色/描述
- 技能栈：去重、规范大小写
- 证据引用：每个关键结论带简短 evidence text
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from services.screening import (
    _estimate_experience_years, _detect_sensitive_info,
    _split_sentences, MAJOR_HINTS, DEGREES,
)
from services.profile_schemas import CandidateProfileResult, EvidenceItem
from tools.skill_guard import ALIASES
from core.logger import get_logger

logger = get_logger(__name__)

# 专业关键词（用于精确匹配，避免误识别）
_MAJOR_PATTERNS = [
    "计算机科学与技术", "计算机科学", "计算机技术", "计算机",
    "软件工程", "软件技术",
    "人工智能", "智能科学",
    "数据科学", "大数据",
    "信息安全", "网络安全",
    "电子信息", "电子工程", "电子科学",
    "通信工程", "通信",
    "自动化", "控制工程",
    "数学", "应用数学", "信息与计算科学", "统计学", "统计",
    "物理学", "应用物理",
    "工商管理", "工商", "管理学",
    "市场营销", "市场",
    "金融学", "金融",
    "经济学", "经济",
    "设计", "工业设计", "交互设计", "视觉传达",
]

# 院校名称模式
_SCHOOL_PATTERN = re.compile(
    r"([一-鿿]{2,15}(?:大学|学院|职业技术学院|专科学校))"
    r"|([A-Z][a-zA-Z\s]{2,30}(?:University|College|Institute))",
    re.UNICODE,
)

# 学历关键词
_DEGREE_PATTERN = re.compile(r"(博士|硕士|研究生|本科|大专|专科|学士|MBA|EMBA)", re.I)

# 毕业年份模式
_GRAD_YEAR_PATTERN = re.compile(
    r"(20\d{2})\s*(?:年)?\s*(?:毕业|届|入学|年毕业)"
    r"|毕业时间?[：:]\s*(20\d{2})"
    r"|(20\d{2})\s*届",
)

# 项目段落标题
_PROJECT_SECTION_PATTERN = re.compile(
    r"(项目经历|项目经验|个人项目|课程项目|实战项目|开源项目|Projects)",
    re.I,
)

# 实习段落标题
_INTERN_SECTION_PATTERN = re.compile(
    r"(实习经历|实习经验|校园经历|实践经历|Internship)",
    re.I,
)

# 工作段落标题
_WORK_SECTION_PATTERN = re.compile(
    r"(工作经历|工作经验|任职经历|Work Experience)",
    re.I,
)

# 技能提取停用词
_SKILL_STOP = {
    "熟悉", "掌握", "了解", "精通", "具备", "使用", "具有", "会",
    "以上", "优先", "经验", "能力", "技术", "开发", "工程师",
    "相关", "学历", "专业", "本科", "硕士", "博士", "大专",
    "年以上", "应届", "实习", "工作", "岗位", "职责", "要求",
    "负责", "参与", "承担", "主导", "完成", "推动", "设计",
    "维护", "优化", "上线", "部署", "测试", "评审", "沟通", "协作",
}

# 技能词正则
_SKILL_EN = re.compile(r"[A-Z][A-Za-z+#./0-9]{1,25}")
_SKILL_CN = re.compile(r"[一-鿿]{2,8}")

# 非专业词（避免误识别）
_NOT_MAJOR = {
    "数学建模", "数据分析", "算法竞赛", "机器学习", "深度学习",
    "自然语言处理", "计算机视觉", "数据挖掘", "大数据",
    "项目经历", "项目经验", "工作经历", "实习经历",
    "教育背景", "教育经历", "获奖经历", "技能清单",
}


def _extract_education_v2(text_value: str) -> dict:
    """改进的教育背景提取

    - 院校识别：支持 XX大学/XX学院/XX职业技术学院/XX University
    - 专业识别：优先匹配明确声明，避免把经历词误识别为专业
    - 学历识别：本科/硕士/研究生/博士/大专
    - 毕业年份：2026届/2026年毕业/毕业时间：2026
    """
    # 学历
    degree = ""
    degree_match = _DEGREE_PATTERN.search(text_value)
    if degree_match:
        degree = degree_match.group(1)

    # 院校
    school = ""
    school_match = _SCHOOL_PATTERN.search(text_value)
    if school_match:
        school = (school_match.group(1) or school_match.group(2) or "").strip()

    # 专业（优先匹配明确声明）
    major = ""
    # 先找"专业：xxx"或"xxx专业"模式
    major_explicit = re.search(
        r"专业[：:]\s*([一-鿿A-Za-z]{2,20})"
        r"|([一-鿿]{2,15}(?:专业))",
        text_value,
    )
    if major_explicit:
        candidate = (major_explicit.group(1) or major_explicit.group(2) or "").strip()
        if candidate and candidate not in _NOT_MAJOR:
            major = candidate.replace("专业", "").strip()

    # 如果没找到明确声明，从 MAJOR_HINTS 中匹配
    if not major:
        for m in MAJOR_HINTS:
            # 避免把"数学建模"误识别为"数学"专业
            if m in text_value:
                # 检查是否是独立的专业词，不是其他词的一部分
                pattern = re.compile(r"(?:专业[：:]?|就读于|本科|硕士|博士)" + re.escape(m), re.I)
                if pattern.search(text_value):
                    major = m
                    break

    # 毕业年份
    graduation_year = ""
    grad_match = _GRAD_YEAR_PATTERN.search(text_value)
    if grad_match:
        graduation_year = grad_match.group(1) or grad_match.group(2) or grad_match.group(3) or ""

    return {
        "degree": degree,
        "major": major,
        "graduation_year": graduation_year,
        "school": school,
    }


def _normalize_skill(s: str) -> str:
    """归一化技能名"""
    s = s.strip()
    if not s:
        return s
    lower = s.lower()
    if lower in ALIASES:
        return ALIASES[lower]
    if s.isascii() and s.isalpha():
        return s[0].upper() + s[1:].lower()
    return s


def _extract_skills_from_text(text_value: str) -> list[dict]:
    """从全文提取技能词，归一化 + 去重"""
    from tools.skill_taxonomy import KNOWN_SKILLS
    skills = []
    seen = set()

    # 段落标题识别能力要求段
    req_pattern = re.compile(r"(任职要求|岗位要求|必备条件|能力要求|Requirements)", re.I)
    nice_pattern = re.compile(r"(加分项|优先|熟悉更佳|nice.to.have|bonus)", re.I)

    sections = req_pattern.split(text_value)
    for i, section in enumerate(sections):
        if req_pattern.match(section):
            if i + 1 < len(sections):
                content = sections[i + 1]
                if nice_pattern.search(content):
                    content = nice_pattern.split(content)[0]
                for match in _SKILL_EN.finditer(content):
                    s = _normalize_skill(match.group())
                    if s.lower() not in seen and s.lower() not in {w.lower() for w in _SKILL_STOP}:
                        seen.add(s.lower())
                        skills.append({"skill": s, "confidence": "explicit"})
                for match in _SKILL_CN.finditer(content):
                    s = match.group().strip()
                    if s.lower() not in seen and s not in _SKILL_STOP and len(s) >= 2:
                        seen.add(s.lower())
                        skills.append({"skill": s, "confidence": "explicit"})

    # 兜底：扫描"熟悉/掌握/精通"句式
    if not skills:
        for s in _split_sentences(text_value):
            if any(k in s for k in ("熟悉", "掌握", "精通", "具备", "了解", "擅长")):
                for match in _SKILL_EN.finditer(s):
                    sk = _normalize_skill(match.group())
                    if sk.lower() not in seen and sk.lower() not in {w.lower() for w in _SKILL_STOP}:
                        seen.add(sk.lower())
                        skills.append({"skill": sk, "confidence": "inferred"})
                for match in _SKILL_CN.finditer(s):
                    sk = match.group().strip()
                    if sk.lower() not in seen and sk not in _SKILL_STOP and len(sk) >= 2:
                        seen.add(sk.lower())
                        skills.append({"skill": sk, "confidence": "inferred"})

    # 补漏：全文匹配 KNOWN_SKILLS
    text_lower = text_value.lower()
    for known in KNOWN_SKILLS:
        if known.lower() in text_lower and known.lower() not in seen:
            seen.add(known.lower())
            skills.append({"skill": known, "confidence": "inferred"})

    # 补漏：匹配冒号/顿号分隔的技能列表（"技能：SQL、Python、Pandas"）
    skill_list_pattern = re.compile(r"(?:技能|skills|掌握|熟悉)[：:]\s*(.+?)(?:\n|$)", re.I)
    for match in skill_list_pattern.finditer(text_value):
        items = re.split(r"[、,，\s]+", match.group(1))
        for item in items:
            s = _normalize_skill(item.strip())
            if s and len(s) >= 2 and s.lower() not in seen and s.lower() not in {w.lower() for w in _SKILL_STOP}:
                seen.add(s.lower())
                skills.append({"skill": s, "confidence": "explicit"})

    return skills[:20]


def _extract_projects(text_value: str) -> list[dict]:
    """从简历中提取项目经历"""
    projects = []
    seen = set()

    # 项目段落标题
    proj_pattern = re.compile(r"(项目经历|项目经验|个人项目|课程项目|实战项目|开源项目)", re.I)
    sections = proj_pattern.split(text_value)
    for i, section in enumerate(sections):
        if proj_pattern.match(section):
            if i + 1 < len(sections):
                content = sections[i + 1][:500]
                for s in _split_sentences(content):
                    s = s.strip()
                    if len(s) < 6:
                        continue
                    # 停止条件：遇到工作/实习/教育
                    if any(k in s for k in ("工作经历", "实习经历", "教育背景", "学历")):
                        break
                    if s not in seen:
                        seen.add(s)
                        projects.append(s[:200])

    # 兜底：扫描"项目"关键词
    if not projects:
        for s in _split_sentences(text_value):
            if any(k in s for k in ("项目", "平台", "系统", "工具", "网站")) and len(s) > 10:
                if s not in seen:
                    seen.add(s)
                    projects.append(s[:200])

    return [{"name": p[:30], "description": p, "confidence": "explicit"} for p in projects[:6]]


def _extract_internships(text_value: str) -> list[dict]:
    """提取实习经历"""
    internships = []
    seen = set()

    # 实习段落标题
    intern_pattern = re.compile(r"(实习经历|实习经验|实习)", re.I)
    sections = intern_pattern.split(text_value)
    for i, section in enumerate(sections):
        if intern_pattern.match(section):
            if i + 1 < len(sections):
                content = sections[i + 1][:400]
                for s in _split_sentences(content):
                    s = s.strip()
                    if len(s) < 6:
                        continue
                    if any(k in s for k in ("工作经历", "教育背景", "项目经历")):
                        break
                    if s not in seen:
                        seen.add(s)
                        internships.append({"description": s[:180], "confidence": "explicit"})

    # 兜底：扫描"实习"关键词
    if not internships:
        for s in _split_sentences(text_value):
            if "实习" in s and len(s) > 6:
                if s not in seen:
                    seen.add(s)
                    internships.append({"description": s[:180], "confidence": "inferred"})

    return internships[:6]


def _extract_work_experiences(text_value: str) -> list[dict]:
    """提取工作经历"""
    experiences = []
    seen = set()

    # 工作段落标题
    work_pattern = re.compile(r"(工作经历|工作经验|任职经历)", re.I)
    sections = work_pattern.split(text_value)
    for i, section in enumerate(sections):
        if work_pattern.match(section):
            if i + 1 < len(sections):
                content = sections[i + 1][:500]
                for s in _split_sentences(content):
                    s = s.strip()
                    if len(s) < 6:
                        continue
                    if any(k in s for k in ("教育背景", "项目经历", "实习")):
                        break
                    if s not in seen:
                        seen.add(s)
                        experiences.append({"description": s[:180], "confidence": "explicit"})

    # 兜底：扫描"任职/工作于/就职/工作："关键词（排除项目和实习）
    if not experiences:
        for s in _split_sentences(text_value):
            if any(k in s for k in ("任职", "工作于", "就职", "工作：", "工作:")) and len(s) > 6:
                if "实习" not in s and "项目" not in s:
                    if s not in seen:
                        seen.add(s)
                        experiences.append({"description": s[:180], "confidence": "inferred"})

    return experiences[:8]


def _extract_achievements(text_value: str) -> list[dict]:
    """提取成果证据"""
    achievements = []
    seen = set()
    for s in _split_sentences(text_value):
        if re.search(r"\d+[%万千]|提升|降低|优化|上线|获奖|竞赛|专利|论文|部署|落地|交付|准确率|召回率|延迟|QPS|TopK", s):
            s_clean = s.strip()[:150]
            if s_clean and s_clean not in seen:
                seen.add(s_clean)
                achievements.append({
                    "description": s_clean,
                    "has_metric": bool(re.search(r"\d+[%万千]", s)),
                })
    return achievements[:8]


def _extract_learning_signals(text_value: str) -> list[str]:
    """提取学习能力信号"""
    signals = []
    patterns = [
        (r"(自学|自研|独立学习|独立完成|从0到1|从零开始)", "自主学习"),
        (r"(开源|github|GitHub|贡献)", "开源参与"),
        (r"(竞赛|比赛|hackathon|Hackathon)", "竞赛经历"),
        (r"(论文|专利|博客|技术文章)", "技术输出"),
        (r"(证书|认证|考取)", "认证获取"),
        (r"(新技术|新框架|快速上手|快速学习)", "技术迁移"),
        (r"(跨专业|跨领域|转行)", "跨领域学习"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text_value, re.I):
            signals.append(label)
    return signals


def _extract_business_understanding(text_value: str) -> list[str]:
    """提取业务理解信号"""
    domains = []
    domain_keywords = {
        "电商": ("电商", "商城", "购物", "下单", "订单"),
        "金融": ("金融", "支付", "风控", "交易", "银行"),
        "教育": ("教育", "在线学习", "课程", "教学"),
        "医疗": ("医疗", "健康", "医院", "问诊"),
        "游戏": ("游戏", "引擎", "Unity", "游戏开发"),
        "社交": ("社交", "IM", "消息", "即时通讯"),
        "企业服务": ("SaaS", "CRM", "ERP", "OA", "企业服务"),
        "AI": ("AI", "人工智能", "大模型", "LLM", "Agent"),
    }
    for domain, keywords in domain_keywords.items():
        if any(k in text_value for k in keywords):
            domains.append(domain)
    return domains


def _extract_collaboration_signals(text_value: str) -> list[str]:
    """提取协作信号"""
    signals = []
    patterns = [
        (r"(团队|协作|配合|跨部门)", "团队协作"),
        (r"(沟通|表达|汇报|演示)", "沟通表达"),
        (r"(带领|负责|leader|lead)", "领导力"),
        (r"(Code Review|代码评审|技术分享)", "技术分享"),
    ]
    for pattern, label in patterns:
        try:
            if re.search(pattern, text_value, re.I):
                signals.append(label)
        except Exception:
            pass
    return signals


def extract_candidate_profile(
    resume_text: str = "",
    user_id: int = 0,
    resume_filename: str = "",
    conversation_text: str = "",
) -> CandidateProfileResult:
    """从简历文本中提取结构化候选人画像（v0.35 增强版）"""
    text_value = (resume_text or "").strip()
    if conversation_text:
        text_value += "\n" + conversation_text

    # 空文本安全兜底
    if not text_value or len(text_value) < 10:
        return CandidateProfileResult(
            education_background={},
            skill_stack=[],
            projects=[],
            internships=[],
            work_experiences=[],
            achievements=[],
            learning_signals=[],
            risk_points=["简历文本为空或过短"],
            evidence=[],
            confidence="low",
            summary="简历文本为空或过短，无法提取画像",
        )

    # 教育背景（v0.35 增强版）
    education = _extract_education_v2(text_value)

    # 成果证据
    achievements = _extract_achievements(text_value)

    # 学习信号
    learning = _extract_learning_signals(text_value)

    # 业务理解
    business = _extract_business_understanding(text_value)

    # 协作信号
    collab = _extract_collaboration_signals(text_value)

    # 敏感信息检测
    sensitive = _detect_sensitive_info(text_value)

    # 技能栈：优先 LLM，失败走规则兜底
    from services.resume_profile import extract_profile_from_text, profile_to_skill_names
    base = extract_profile_from_text(text_value, use_llm=True) if text_value else {"skills": [], "summary": "", "parser": "empty"}
    skill_stack = []
    for item in base.get("skills", []):
        name = item if isinstance(item, str) else item.get("skill", "")
        conf = "explicit" if isinstance(item, dict) and item.get("confidence", 0) >= 0.7 else "inferred"
        skill_stack.append({"skill": str(name), "confidence": conf})

    # 如果 LLM 没提取到技能，走规则兜底
    if not skill_stack and text_value:
        logger.info("LLM 未提取到技能，使用规则兜底")
        skill_stack = _extract_skills_from_text(text_value)

    # 技能去重规范化
    skill_stack = _dedupe_skills(skill_stack)

    # 项目/实习/工作经历：规则提取
    projects = _extract_projects(text_value)
    internships = _extract_internships(text_value)
    work_experiences = _extract_work_experiences(text_value)

    # 经历年限
    exp_years = _estimate_experience_years(text_value)

    # 风险点
    risks = []
    if not education.get("degree"):
        risks.append("未明确学历")
    if not education.get("school"):
        risks.append("未识别到院校")
    if not projects and not internships:
        risks.append("缺少项目/实习经历")
    if not achievements:
        risks.append("缺少量化成果")
    if not skill_stack:
        risks.append("未识别到技能")

    # 置信度
    has_degree = bool(education.get("degree"))
    has_school = bool(education.get("school"))
    conf = "high" if (skill_stack and projects and has_degree and has_school) else "medium" if skill_stack else "low"

    # 证据
    evidence = []
    if education.get("school"):
        evidence.append(EvidenceItem(text=f"院校: {education['school']}", source="教育背景"))
    if education.get("degree"):
        evidence.append(EvidenceItem(text=f"学历: {education['degree']}", source="教育背景"))
    if projects:
        evidence.append(EvidenceItem(text=projects[0].get("description", "")[:100], source="项目经历"))

    return CandidateProfileResult(
        education_background={
            "degree": education.get("degree", ""),
            "major": education.get("major", ""),
            "graduation_year": education.get("graduation_year", ""),
            "school": education.get("school", ""),
        },
        skill_stack=skill_stack,
        projects=projects,
        internships=internships,
        work_experiences=work_experiences,
        business_understanding=business,
        achievements=achievements,
        learning_signals=learning,
        transferable_strengths=collab,
        collaboration_signals=collab,
        risk_points=risks,
        evidence=evidence,
        confidence=conf,
        sensitive_detected=sensitive,
        summary=f"识别到 {len(skill_stack)} 个技能、{len(projects)} 个项目经历、{len(internships)} 段实习。",
    )


def _dedupe_skills(skills: list[dict]) -> list[dict]:
    """技能去重规范化"""
    seen = set()
    result = []
    for item in skills:
        name = (item.get("skill") or "").strip()
        if not name or len(name) < 2:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:20]


def save_candidate_profile(profile: CandidateProfileResult, user_id: int, resume_filename: str = "") -> int:
    """保存候选人画像到数据库"""
    from models.database import SessionLocal
    from models.profile import CandidateProfile
    with SessionLocal() as session:
        obj = CandidateProfile(
            user_id=user_id,
            source_type="resume_text",
            resume_filename=resume_filename,
            raw_text="",
            education_background=json.dumps(profile.education_background, ensure_ascii=False),
            skill_stack=json.dumps(profile.skill_stack, ensure_ascii=False),
            projects=json.dumps(profile.projects, ensure_ascii=False),
            internships=json.dumps(profile.internships, ensure_ascii=False),
            work_experiences=json.dumps(profile.work_experiences, ensure_ascii=False),
            business_understanding=json.dumps(profile.business_understanding, ensure_ascii=False),
            achievements=json.dumps(profile.achievements, ensure_ascii=False),
            learning_signals=json.dumps(profile.learning_signals, ensure_ascii=False),
            transferable_strengths=json.dumps(profile.transferable_strengths, ensure_ascii=False),
            collaboration_signals=json.dumps(profile.collaboration_signals, ensure_ascii=False),
            risk_points=json.dumps(profile.risk_points, ensure_ascii=False),
            evidence=json.dumps([e.model_dump() for e in profile.evidence], ensure_ascii=False),
            confidence=profile.confidence,
            sensitive_detected=json.dumps(profile.sensitive_detected, ensure_ascii=False),
        )
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj.id
