"""Job profile extraction service — v0.27 增强提取逻辑

提升岗位画像从"技能列表"到"招聘需求画像"的质量：
- 分段解析 JD（职责/要求/加分项）
- responsibilities 聚合高频职责
- business_context 业务场景推断
- employment_type / target_audience 改进
- must_have vs nice_to_have 区分
- 质量控制（confidence / quality_flags）
"""
from __future__ import annotations
import json
import re
from collections import Counter
from datetime import datetime
from services.screening import (
    _fetch_jd_texts, _filter_jd_quality,
    _count_hits, BUSINESS_DOMAINS, SOFT_REQUIREMENTS, DEGREES, MAJOR_HINTS,
    EXPERIENCE_PATTERNS, EMPLOYMENT_TYPE_KEYWORDS,
)
from services.jd_quality_service import filter_jd_items
from services.profile_schemas import JobProfileResult, EvidenceItem
from tools.skill_guard import normalize_job_name, ALIASES
from tools.skill_taxonomy import filter_skill_names, KNOWN_SKILLS
from core.logger import get_logger

logger = get_logger(__name__)

# ── 段落标题正则 ──

_RESPONSIBILITY_HEADERS = re.compile(
    r"(岗位职责|工作职责|你将负责|工作内容|职责描述|你将做|主要工作|"
    r"Responsibilities|What you.ll do|What you.ll be doing)",
    re.I,
)

_REQUIREMENT_HEADERS = re.compile(
    r"(任职要求|岗位要求|必备条件|任职资格|我们希望你|职位要求|"
    r"Requirements|What we need|What we.re looking for)",
    re.I,
)

_NICE_TO_HAVE_HEADERS = re.compile(
    r"(加分项|优先|熟悉更佳|nice.to.have|bonus|加分条件|优先考虑|加分)",
    re.I,
)

# ── 技能提取 ──

_SKILL_EXTRACT_PATTERNS = [
    re.compile(r"[A-Z][A-Za-z+#./0-9]{1,25}"),
    re.compile(r"[一-鿿]{2,8}"),
]

_SKILL_STOP_WORDS = {
    "熟悉", "掌握", "了解", "精通", "具备", "使用", "具有", "会",
    "以上", "优先", "经验", "能力", "技术", "开发", "工程师",
    "相关", "学历", "专业", "本科", "硕士", "博士", "大专",
    "年以上", "应届", "实习", "工作", "岗位", "职责", "要求",
    "沟通", "协作", "团队", "学习", "能力", "抗压", "责任心",
    "逻辑思维", "表达", "推动", "执行", "跨部门",
    "五险一金", "年终奖", "带薪年假", "节日福利", "团建",
}

# ── 业务场景关键词 ──
_BUSINESS_CONTEXT_KEYWORDS = {
    "AI 应用": ("ai应用", "ai 落地", "ai产品", "ai平台", "智能应用"),
    "大模型应用": ("大模型", "llm", "大语言模型", "chatgpt", "gpt", "大模型应用"),
    "RAG/知识库": ("rag", "知识库", "知识图谱", "检索增强", "向量"),
    "Agent/工作流": ("agent", "智能体", "工作流", "workflow", "自动化"),
    "数据平台": ("数据平台", "数据中台", "数据仓库", "大数据", "数据分析"),
    "后台系统": ("后台", "管理系统", "admin", "crm", "erp", "中台"),
    "电商": ("电商", "商城", "交易", "订单", "支付"),
    "社交": ("社交", "即时通讯", "im", "社区", "内容"),
    "金融": ("金融", "风控", "信贷", "银行", "保险"),
    "教育": ("教育", "在线教育", "知识付费", "培训"),
    "医疗": ("医疗", "健康", "医院", "医药"),
    "增长/营销": ("增长", "营销", "广告", "推荐", "搜索引擎"),
    "嵌入式/硬件": ("嵌入式", "硬件", "物联网", "iot", "芯片"),
    "游戏": ("游戏", "unity", "unreal", "游戏引擎"),
    "工具/SaaS": ("saas", "工具", "效率", "协同", "办公"),
}

# ── 成长信号关键词 ──
_GROWTH_KEYWORDS = {
    "实习/校招培养": ("实习", "校招", "应届", "培养", "导师"),
    "核心项目": ("核心", "从0到1", "从零到一", "核心业务", "重点项目"),
    "技术成长": ("技术成长", "技术栈", "前沿", "探索", "创新"),
    "跨团队协作": ("跨团队", "跨部门", "协作", "沟通"),
    "晋升空间": ("晋升", "发展", "成长空间", "职业发展"),
}


def _extract_skills_from_section(text: str) -> list[str]:
    """从文本中提取技能词，返回去重归一化列表"""
    skills = []
    seen = set()
    for sentence in re.split(r"[。；\n,，]", text):
        sentence = sentence.strip()
        if len(sentence) < 2:
            continue
        for pattern in _SKILL_EXTRACT_PATTERNS:
            for match in pattern.finditer(sentence):
                skill = match.group().strip()
                if len(skill) < 2 or skill.lower() in _SKILL_STOP_WORDS:
                    continue
                normalized = _normalize_skill(skill)
                if normalized.lower() not in seen:
                    seen.add(normalized.lower())
                    skills.append(normalized)
    return skills


def _normalize_skill(skill: str) -> str:
    """归一化技能名：别名映射 + 大小写统一"""
    s = skill.strip()
    if not s:
        return s
    lower = s.lower()
    if lower in ALIASES:
        return ALIASES[lower]
    if s.isascii() and s.isalpha():
        return s[0].upper() + s[1:].lower()
    return s


def _split_jd_sections(text: str) -> dict[str, str]:
    """将 JD 文本分段解析为结构化段落。

    Returns:
        {"responsibilities": "...", "requirements": "...", "nice_to_have": "...", "other": "..."}
    """
    result = {"responsibilities": "", "requirements": "", "nice_to_have": "", "other": ""}

    # 只匹配作为段落标题的关键词（前面是换行或句号，后面是冒号或句号）
    _section_patterns = [
        (r"(?:^|[\n。])\s*(岗位职责|工作职责|你将负责|工作内容|职责描述|你将做|主要工作)\s*[：:。]", "responsibilities"),
        (r"(?:^|[\n。])\s*(任职要求|岗位要求|必备条件|任职资格|职位要求|我们希望你)\s*[：:。]", "requirements"),
        (r"(?:^|[\n。])\s*(加分项|加分条件)\s*[：:。]", "nice_to_have"),
    ]

    found_sections = []
    for pattern, section_type in _section_patterns:
        for match in re.finditer(pattern, text, re.I):
            found_sections.append((match.start(), match.end(), section_type))

    if not found_sections:
        # 没有明确段落标题，整段作为 other
        result["other"] = text
        return result

    # 按位置排序
    found_sections.sort(key=lambda x: x[0])

    # 提取各段落内容
    for i, (start, end, section_type) in enumerate(found_sections):
        next_start = found_sections[i + 1][0] if i + 1 < len(found_sections) else len(text)
        content = text[end:next_start].strip()
        result[section_type] += content + "\n"

    # 标题前的内容作为 other
    if found_sections[0][0] > 0:
        result["other"] = text[:found_sections[0][0]]

    return result


def _extract_responsibilities(texts: list[str]) -> list[str]:
    """从 JD 中提取岗位职责。

    优先从职责段落提取，聚合高频职责，3-6 条。
    """
    raw_responsibilities = []
    seen = set()

    for text in texts:
        sections = _split_jd_sections(text)
        resp_text = sections["responsibilities"]

        if resp_text:
            # 从职责段提取
            for s in re.split(r"[。；\n]", resp_text):
                s = s.strip()
                if len(s) < 6:
                    continue
                # 停止条件：遇到要求段
                if re.match(r"(任职要求|岗位要求|必备条件)", s, re.I):
                    break
                # 保留包含动作动词的句子
                if any(k in s for k in ("负责", "参与", "承担", "主导", "完成", "推动", "设计", "开发", "维护", "优化", "搭建", "建设", "落地", "探索", "研究")):
                    # 归一化：去掉序号、前缀
                    clean = re.sub(r"^[\d.、\-\s]+", "", s).strip()
                    if clean and clean not in seen and len(clean) > 6:
                        seen.add(clean)
                        raw_responsibilities.append(clean[:120])
        else:
            # 兜底：扫描全文
            for s in re.split(r"[。；\n]", text):
                s = s.strip()
                if any(k in s for k in ("负责", "参与", "承担", "主导")) and len(s) > 8:
                    clean = re.sub(r"^[\d.、\-\s]+", "", s).strip()
                    if clean and clean not in seen:
                        seen.add(clean)
                        raw_responsibilities.append(clean[:120])

    # 去重并限制数量
    return raw_responsibilities[:6]


def _extract_must_have_skills(texts: list[str]) -> list[str]:
    """从 JD 任职要求段提取必备技能。

    只从要求段提取，排除加分/优先句子。
    """
    all_skills = []
    seen = set()

    # 弱要求关键词（句中出现则排除出 must_have）
    _weak_markers = ("优先", "加分", "熟悉更佳", "了解即可", "了解优先",
                     "有经验者优先", "有以下经验优先", "熟悉优先",
                     "了解", "熟悉更佳",
                     "preferred", "plus", "bonus", "nice to have")

    for text in texts:
        sections = _split_jd_sections(text)
        req_text = sections["requirements"]

        if req_text:
            # 逐句处理，排除包含弱要求关键词的句子
            for s in re.split(r"[。；\n]", req_text):
                s = s.strip()
                if not s:
                    continue
                # 排除加分/优先句子
                if any(k in s.lower() for k in _weak_markers):
                    continue
                skills = _extract_skills_from_section(s)
                for sk in skills:
                    if sk.lower() not in seen:
                        seen.add(sk.lower())
                        all_skills.append(sk)
        else:
            # 兜底：扫描"熟悉/掌握/精通"关键词
            for s in re.split(r"[。；\n]", text):
                if any(k in s for k in ("熟悉", "掌握", "精通", "具备")):
                    # 排除加分项句子
                    if any(k in s.lower() for k in _weak_markers):
                        continue
                    skills = _extract_skills_from_section(s)
                    for sk in skills:
                        if sk.lower() not in seen:
                            seen.add(sk.lower())
                            all_skills.append(sk)

    # 补漏：CAPABILITY_WHITELIST
    from tools.skill_guard import CAPABILITY_WHITELIST
    for text in texts:
        text_lower = text.lower()
        for cap in CAPABILITY_WHITELIST:
            if cap.lower() in text_lower and cap.lower() not in seen:
                seen.add(cap.lower())
                all_skills.append(cap)

    # 过滤掉明显不是技能的词
    _extra_stop = {"任职要求", "岗位要求", "必备条件", "任职资格", "职位要求", "加分项", "优先条件"}
    all_skills = [s for s in all_skills if s not in _extra_stop]

    return filter_skill_names(all_skills, job_name="")[:10]


def _extract_nice_to_have_skills(texts: list[str]) -> list[str]:
    """从 JD 加分/优先段落提取加分技能。

    扫描"加分项/优先/了解/熟悉更佳/有...经验优先"等弱要求句子。
    排除已在 must_have 中的能力。
    """
    all_skills = []
    seen = set()

    # 弱要求关键词（句中出现则视为 nice_to_have 来源）
    _weak_markers = ("优先", "加分", "熟悉更佳", "了解即可", "了解优先",
                     "有经验者优先", "有以下经验优先", "熟悉优先",
                     "了解", "熟悉更佳",
                     "preferred", "plus", "bonus", "nice to have")

    for text in texts:
        sections = _split_jd_sections(text)
        nth_text = sections["nice_to_have"]

        if nth_text:
            # 从加分段提取
            skills = _extract_skills_from_section(nth_text[:300])
            for s in skills:
                if s.lower() not in seen:
                    seen.add(s.lower())
                    all_skills.append(s)
        else:
            # 兜底：扫描包含弱要求关键词的句子
            for s in re.split(r"[。；\n]", text):
                s_lower = s.lower()
                if any(k in s_lower for k in _weak_markers):
                    # 只提取技术能力词，排除软性要求
                    skills = _extract_skills_from_section(s)
                    for sk in skills:
                        if sk.lower() not in seen and not _is_soft_skill(sk):
                            seen.add(sk.lower())
                            all_skills.append(sk)

        # 额外：扫描"了解XXX"模式（了解大模型、了解RAG等）
        for match in re.finditer(r"了解([A-Za-z一-鿿][A-Za-z一-鿿+#./0-9、,，]{1,50})", text):
            phrase = match.group(1).strip()
            # 按中文逗号/顿号分割多个技能
            for part in re.split(r"[、,，]", phrase):
                skill = part.strip()
                # 去掉常见后缀
                skill = re.sub(r"(等概念|等技术|等框架|等工具|等|概念|技术|框架|工具)$", "", skill).strip()
                if len(skill) >= 2 and not _is_soft_skill(skill):
                    normalized = _normalize_skill(skill)
                    if normalized.lower() not in seen:
                        seen.add(normalized.lower())
                        all_skills.append(normalized)

    return filter_skill_names(all_skills, job_name="")[:8]


def _is_soft_skill(skill: str) -> bool:
    """判断是否为软性能力（不应进入 nice_to_have）"""
    soft = {
        "沟通", "协作", "团队", "学习能力", "抗压", "责任心", "执行力",
        "逻辑思维", "表达", "团队合作", "跨部门", "沟通能力", "沟通协作",
        "良好的沟通", "良好的团队", "良好的逻辑", "良好的表达",
        "具备", "使用", "具有", "能力", "经验", "技术", "开发",
    }
    s = skill.strip().lower()
    return s in soft or len(s) < 2


def _extract_business_context(texts: list[str]) -> list[str]:
    """从 JD 文本推断业务场景。"""
    joined = "\n".join(texts).lower()
    contexts = []
    for context_name, keywords in _BUSINESS_CONTEXT_KEYWORDS.items():
        for kw in keywords:
            if kw in joined:
                contexts.append(context_name)
                break
    return contexts[:5]


def _extract_growth_context(texts: list[str]) -> list[str]:
    """提取成长/学习信号。"""
    growth = []
    seen = set()
    for text in texts:
        for s in re.split(r"[。；\n]", text):
            s_clean = s.strip()
            if len(s_clean) < 5:
                continue
            for signal, keywords in _GROWTH_KEYWORDS.items():
                if any(k in s_clean for k in keywords):
                    if signal not in seen:
                        seen.add(signal)
                        growth.append(s_clean[:100])
                    break
    return growth[:5]


def _infer_employment_type_v2(job_name: str, texts: list[str]) -> dict:
    """改进的用工类型推断。"""
    joined = f"{job_name}\n" + "\n".join(texts[:5])

    # 实习：标题或正文包含实习/实习生/在校
    if re.search(r"(实习|intern|internship|在校)", joined, re.I):
        return {"employment_type": "实习", "target_audience": "在校生/应届生", "confidence": "high"}

    # 校招：校园/应届/毕业生/校招
    if re.search(r"(校招|校园招聘|应届|毕业生|管培)", joined, re.I):
        return {"employment_type": "校招", "target_audience": "应届生", "confidence": "high"}

    # 全职：社招/正式/全职/经验要求/标准岗位
    if re.search(r"(社招|正式|全职|经验|[1-9]\s*年|任职要求|岗位职责|岗位要求)", joined, re.I):
        # 进一步判断 target_audience
        has_exp = re.search(r"[2-9]\s*年|[3-9]\s*年|多年", joined)
        has_entry = re.search(r"(应届|毕业生|不限经验|经验不限|在校)", joined, re.I)
        if has_entry and not has_exp:
            return {"employment_type": "全职", "target_audience": "应届生/有经验候选人", "confidence": "medium"}
        if has_exp:
            return {"employment_type": "全职", "target_audience": "有经验候选人", "confidence": "medium"}
        return {"employment_type": "全职", "target_audience": "有经验候选人", "confidence": "medium"}

    # 兼职
    if re.search(r"(兼职|part-time)", joined, re.I):
        return {"employment_type": "兼职", "target_audience": "不明确", "confidence": "medium"}

    return {"employment_type": "未知", "target_audience": "未明确", "confidence": "low"}


def _extract_education_major_experience(texts: list[str]) -> dict:
    """从 JD 原文中提取学历/专业/经验要求。"""
    joined = "\n".join(texts)

    # 学历
    edu = "未明确"
    for degree in ("博士", "硕士", "研究生", "本科", "大专", "专科"):
        if degree in joined:
            edu = degree
            break
    if "学历不限" in joined:
        edu = "学历不限"

    # 专业
    majors = []
    for major in MAJOR_HINTS:
        if major in joined:
            majors.append(major)
    major_str = "、".join(majors[:3]) if majors else "未明确"

    # 经验
    exp = "未明确"
    for pattern in EXPERIENCE_PATTERNS:
        match = re.search(pattern, joined)
        if match:
            exp = match.group(0)
            break
    if "经验不限" in joined or "不限经验" in joined:
        exp = "经验不限"

    return {"education": edu, "major": major_str, "experience": exp}


def extract_job_profile(job_name: str, top_n: int = 20, raw_jd_texts: list[str] = None) -> JobProfileResult:
    """从 JD 样本中提取结构化岗位画像。"""
    job_name = normalize_job_name(job_name)

    if raw_jd_texts:
        jd_items = [{"text": t, "title": "", "company": "", "source_url": "", "fetched_at": ""} for t in raw_jd_texts]
    else:
        jd_items = _fetch_jd_texts(job_name, limit=20)

    raw_count = len(jd_items)

    # JD 质量过滤 + 去重
    valid_jds, filtered_jds, quality_summary = filter_jd_items(jd_items, job_name=job_name)
    jd_items = valid_jds
    texts = [item["text"] for item in jd_items if item.get("text")]
    valid_count = len(jd_items)

    # ── 岗位定位 ──
    emp = _infer_employment_type_v2(job_name, texts)

    # ── 核心职责 ──
    responsibilities = _extract_responsibilities(texts)

    # ── 必备/加分技能 ──
    must_have = _extract_must_have_skills(texts)
    nice_to_have = _extract_nice_to_have_skills(texts)
    must_set = {s.lower() for s in must_have}
    nice_to_have = [s for s in nice_to_have if s.lower() not in must_set]

    # ── 学历/专业/经验 ──
    req_info = _extract_education_major_experience(texts)

    # ── 业务场景 ──
    business_ctx = _extract_business_context(texts)

    # ── 成长信号 ──
    growth_ctx = _extract_growth_context(texts)

    # ── 软性要求 ──
    soft = _count_hits(texts, SOFT_REQUIREMENTS)

    # ── 证据片段 ──
    evidence = []
    for item in jd_items[:3]:
        evidence.append(EvidenceItem(
            text=item["text"][:200],
            source=f"{item.get('company', '')} - {item.get('title', '')}",
        ))

    # ── 置信度 ──
    if valid_count >= 8 and responsibilities and must_have and business_ctx:
        conf = "high"
    elif valid_count >= 3 and (responsibilities or must_have):
        conf = "medium"
    else:
        conf = "low"

    # ── 质量标记 ──
    flags = []
    if valid_count < 3:
        flags.append("low_sample_count")
    if raw_count > 0 and valid_count < raw_count * 0.3:
        flags.append("low_valid_ratio")
    if not responsibilities:
        flags.append("missing_responsibilities")
    if not must_have:
        flags.append("missing_requirements")
    if quality_summary.get("avg_quality_score", 0) < 50:
        flags.append("low_quality_jds")
    # 检查经验/学历冲突
    exp_values = set()
    for text in texts:
        for pattern in EXPERIENCE_PATTERNS:
            matches = re.findall(pattern, text)
            for m in matches:
                exp_values.add(m if isinstance(m, str) else m[0])
    if len(exp_values) > 2:
        flags.append("mixed_experience_requirement")

    return JobProfileResult(
        job_name=job_name,
        job_type=emp.get("employment_type", "未知"),
        employment_type=emp.get("employment_type", "未知"),
        target_audience=emp.get("target_audience", "未明确"),
        responsibilities=responsibilities,
        must_have_capabilities=must_have,
        nice_to_have_capabilities=nice_to_have,
        experience_requirement=req_info["experience"],
        education_preference=req_info["education"],
        major_preference=req_info["major"],
        business_context=business_ctx,
        growth_context=growth_ctx,
        evidence=evidence,
        confidence=conf,
        quality_flags=flags,
        sample_count=valid_count,
        valid_sample_count=valid_count,
        filtered_sample_count=raw_count - valid_count,
    )


def save_job_profile(profile: JobProfileResult, source_doc_ids: list[int] = None) -> int:
    """保存岗位画像到数据库，返回 ID"""
    from models.database import SessionLocal
    from models.profile import JobProfile
    with SessionLocal() as session:
        obj = JobProfile(
            job_name=profile.job_name,
            profile_version="1.0",
            source_document_ids=json.dumps(source_doc_ids or []),
            sample_count=profile.sample_count,
            job_type=profile.job_type,
            employment_type=profile.employment_type,
            target_audience=profile.target_audience,
            responsibilities=json.dumps(profile.responsibilities, ensure_ascii=False),
            must_have_capabilities=json.dumps(profile.must_have_capabilities, ensure_ascii=False),
            nice_to_have_capabilities=json.dumps(profile.nice_to_have_capabilities, ensure_ascii=False),
            experience_requirement=profile.experience_requirement,
            education_preference=profile.education_preference,
            major_preference=profile.major_preference,
            business_context=json.dumps(profile.business_context, ensure_ascii=False),
            growth_context=json.dumps(profile.growth_context, ensure_ascii=False),
            evidence=json.dumps([e.model_dump() for e in profile.evidence], ensure_ascii=False),
            confidence=profile.confidence,
            quality_flags=json.dumps(profile.quality_flags, ensure_ascii=False),
        )
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj.id
