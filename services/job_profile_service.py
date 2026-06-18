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


def _extract_nice_to_have_skills(texts: list[str], must_have: list[str] = None) -> list[str]:
    """从 JD 加分/优先段落提取加分技能（v0.36 质量治理）。

    质量治理规则：
    1. 必须是可验证的技能、领域经验或业务能力
    2. 删除招聘话术（优先加分、无相关经验也可）
    3. 删除人格标签（踏实靠谱、重点看个人价值观）
    4. 合并语义重复项（B2B + 商务销售 → B2B商务拓展）
    5. 与 must_have 语义重复时只保留在 must_have
    6. 最多保留 5 项
    """
    all_skills = []
    seen = set()
    must_set = {s.lower() for s in (must_have or [])}

    # 弱要求关键词
    _weak_markers = ("优先", "加分", "熟悉更佳", "了解即可", "了解优先",
                     "有经验者优先", "有以下经验优先", "熟悉优先",
                     "了解", "熟悉更佳",
                     "preferred", "plus", "bonus", "nice to have")

    for text in texts:
        sections = _split_jd_sections(text)
        nth_text = sections["nice_to_have"]

        if nth_text:
            skills = _extract_skills_from_section(nth_text[:300])
            for s in skills:
                if s.lower() not in seen and s.lower() not in must_set:
                    seen.add(s.lower())
                    all_skills.append(s)
        else:
            for s in re.split(r"[。；\n]", text):
                s_lower = s.lower()
                if any(k in s_lower for k in _weak_markers):
                    skills = _extract_skills_from_section(s)
                    for sk in skills:
                        if sk.lower() not in seen and sk.lower() not in must_set and not _is_soft_skill(sk):
                            seen.add(sk.lower())
                            all_skills.append(sk)

        # 扫描"了解XXX"模式
        for match in re.finditer(r"了解([A-Za-z一-鿿][A-Za-z一-鿿+#./0-9、,，]{1,50})", text):
            phrase = match.group(1).strip()
            for part in re.split(r"[、,，]", phrase):
                skill = part.strip()
                skill = re.sub(r"(等概念|等技术|等框架|等工具|等|概念|技术|框架|工具)$", "", skill).strip()
                if len(skill) >= 2 and not _is_soft_skill(skill):
                    normalized = _normalize_skill(skill)
                    if normalized.lower() not in seen and normalized.lower() not in must_set:
                        seen.add(normalized.lower())
                        all_skills.append(normalized)

    # 质量治理：清理、合并、截断
    return _clean_nice_to_have(all_skills, must_set)


# ── 加分能力质量治理 ──

# 招聘话术 / 人格标签 / 宽松条件 —— 直接删除
_NTH_NOISE = {
    "优先加分", "加分", "优先", "无相关经验也可", "无经验也可", "经验不限",
    "重点看个人价值观", "踏实靠谱", "踏实", "靠谱", "认真负责",
    "积极主动", "主动性强", "抗压能力强", "自驱力", "自驱",
    "有意向即可", "愿意学习", "学习意愿", "态度端正",
    "有责任心", "有上进心", "有热情", "热爱",
}

# 语义合并规则：(包含关键词列表, 合并后名称)
_NTH_MERGE_RULES = [
    ({"b2b", "商务销售", "商务拓展"}, "B2B商务拓展"),
    ({"b2b", "商务"}, "B2B商务拓展"),
    ({"企业服务", "企业级", "to b"}, "企业服务经验"),
    ({"ai产品", "ai 运营", "大模型运营"}, "AI产品运营"),
    ({"客户增长", "用户增长", "转化"}, "客户增长与转化"),
    ({"数据驱动", "数据运营", "数据分析运营"}, "数据驱动运营"),
]


def _clean_nice_to_have(raw_skills: list[str], must_set: set[str]) -> list[str]:
    """清理加分能力：去噪、合并、去重、截断。"""
    cleaned = []
    seen = set()

    for skill in raw_skills:
        s = skill.strip()
        if not s or len(s) < 2:
            continue
        s_lower = s.lower()

        # 1. 删除噪声
        if s_lower in _NTH_NOISE or any(noise in s_lower for noise in _NTH_NOISE):
            continue

        # 2. 删除与 must_have 重复的
        if s_lower in must_set:
            continue

        # 3. 规范化后缀
        s = re.sub(r"类经验者?$", "经验", s)
        s = re.sub(r"优先$", "", s).strip()
        if not s:
            continue

        # 4. 去重
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        cleaned.append(s)

    # 5. 语义合并
    merged = _merge_nice_to_have(cleaned)

    # 6. 截断
    return merged[:5]


def _merge_nice_to_have(skills: list[str]) -> list[str]:
    """合并语义重复的加分项。"""
    if not skills:
        return skills

    result = []
    used = set()

    for skill in skills:
        if skill.lower() in used:
            continue

        merged = False
        for keywords, merged_name in _NTH_MERGE_RULES:
            # 检查当前 skill 是否匹配规则中的关键词
            if any(kw in skill.lower() for kw in keywords):
                # 检查是否有其他 skill 也匹配同一规则
                related = [s for s in skills if s.lower() not in used and any(kw in s.lower() for kw in keywords)]
                if len(related) >= 2 or skill.lower() != merged_name.lower():
                    result.append(merged_name)
                    for r in related:
                        used.add(r.lower())
                    used.add(skill.lower())
                    merged = True
                    break

        if not merged and skill.lower() not in used:
            result.append(skill)
            used.add(skill.lower())

    return result


def _is_soft_skill(skill: str) -> bool:
    """判断是否为软性能力（不应进入 nice_to_have）"""
    soft = {
        "沟通", "协作", "团队", "学习能力", "抗压", "责任心", "执行力",
        "逻辑思维", "表达", "团队合作", "跨部门", "沟通能力", "沟通协作",
        "良好的沟通", "良好的团队", "良好的逻辑", "良好的表达",
        "具备", "使用", "具有", "能力", "经验", "技术", "开发",
        "踏实靠谱", "认真负责", "积极主动", "自驱力", "有责任心",
        "有上进心", "有热情", "态度端正", "愿意学习",
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
    nice_to_have = _extract_nice_to_have_skills(texts, must_have=must_have)

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
