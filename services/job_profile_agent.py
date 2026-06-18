"""岗位画像 Agent — v0.36 LLM 优先 + 规则兜底

分析流程：
1. 质量过滤与去重 JD
2. 规则提取候选字段及证据
3. LLM 基于 JD 原文与规则证据综合归纳
4. Pydantic schema 校验
5. 事实与证据约束检查
6. 任一步失败则返回规则画像
"""
from __future__ import annotations
import json
from typing import Optional
from sqlalchemy import text as sql_text

from models.database import SessionLocal
from services.job_profile_service import extract_job_profile, save_job_profile
from services.profile_schemas import JobProfileResult, EvidenceItem
from services.agent_common import (
    call_llm, parse_json_from_llm, validate_pydantic,
    build_evidence_context, build_rule_evidence,
    is_fabricated_evidence,
)
from tools.skill_guard import normalize_job_name
from core.logger import get_logger

logger = get_logger(__name__)

# ── 岗位画像 System Prompt ──

JOB_PROFILE_PROMPT = """你是岗位画像分析 Agent。你的任务是基于多条 JD 原文和规则提取的证据，综合归纳出结构化的岗位画像。

## 核心规则
1. **只依据输入 JD 与证据**，不得编造信息。
2. 归并同义项，不要复制原始句子。
3. 没有证据时返回空数组、"不明确"或较低置信度，不得补充行业常识。
4. 必备能力与加分能力互斥，不得重复。
5. 输出纯 JSON，不要输出 Markdown 或任何其他格式。
6. 所有字段必须匹配 schema，不得有额外字段。

## 能力提取要求
### 必备能力 (must_have_capabilities)
- 多条 JD 共同要求，或与核心职责直接相关
- 必须能通过简历、项目或面试验证
- 合并语义重复内容（如 "Python" 和 "python" 合并）
- 删除招聘口号、人格标签（如 "踏实靠谱"、"积极主动"）
- 最多 6 项

### 加分能力 (nice_to_have_capabilities)
- 少数 JD 明确标记为"优先、加分、了解"
- 必须是可验证的技能、领域经验或业务能力
- 最多 5 项
- 不得与必备能力重复
- 删除招聘话术（如 "优先加分"、"无相关经验也可"）

### 职责 (responsibilities)
- 从"岗位职责/工作职责/你将负责"段落提取
- 归并相似职责为完整表述
- 3-6 条

## 输出 JSON 格式
{{
  "job_name": "岗位名",
  "employment_type": "全职/兼职/实习/未知",
  "target_audience": "面向人群",
  "responsibilities": ["职责1", "职责2"],
  "must_have_capabilities": ["能力1", "能力2"],
  "nice_to_have_capabilities": ["加分1", "加分2"],
  "education_preference": "学历要求",
  "major_preference": "专业要求",
  "experience_requirement": "经验要求",
  "business_context": ["业务场景1"],
  "growth_context": ["成长信号1"],
  "confidence": "high/medium/low",
  "quality_flags": ["标记1"],
  "sample_count": 0,
  "valid_sample_count": 0,
  "filtered_sample_count": 0
}}

## 输入

### JD 原文
{jd_context}

### 规则提取的参考信号
{rule_signals}

只输出 JSON。"""


class JobProfileAgent:
    """岗位画像 Agent — LLM 优先 + 规则兜底。"""

    def analyze(
        self,
        job_name: str,
        jd_texts: list[str],
        fallback_profile: Optional[JobProfileResult] = None,
    ) -> tuple[JobProfileResult, str]:
        """分析岗位画像。

        Args:
            job_name: 岗位名称
            jd_texts: 已过滤去重的 JD 原文列表
            fallback_profile: 规则提取的兜底画像

        Returns:
            (profile_result, analysis_mode) 其中 analysis_mode 为 "agent" 或 "rule_fallback"
        """
        # 1. 规则兜底
        if fallback_profile is None:
            fallback_profile = extract_job_profile(job_name, raw_jd_texts=jd_texts)

        # 2. 检查是否有足够文本
        if not jd_texts or all(len(t.strip()) < 50 for t in jd_texts):
            logger.info(f"JD 文本不足，使用规则画像: {job_name}")
            return fallback_profile, "rule_fallback"

        # 3. 构建 LLM prompt
        jd_context = build_evidence_context(jd_texts)
        rule_signals = build_rule_evidence(fallback_profile.model_dump())
        prompt = JOB_PROFILE_PROMPT.format(
            jd_context=jd_context,
            rule_signals=rule_signals,
        )

        # 4. 调用 LLM
        logger.info(f"调用 LLM 分析岗位画像: {job_name}")
        raw_response = call_llm(prompt, max_tokens=2000, timeout=60)
        if not raw_response:
            logger.warning(f"LLM 调用失败，使用规则画像: {job_name}")
            return fallback_profile, "rule_fallback"

        # 5. 解析 JSON
        data = parse_json_from_llm(raw_response)
        if not data:
            logger.warning(f"LLM 返回非法 JSON，使用规则画像: {job_name}")
            return fallback_profile, "rule_fallback"

        # 6. Pydantic 校验
        # 移除可能的额外字段
        allowed_fields = set(JobProfileResult.model_fields.keys())
        filtered_data = {k: v for k, v in data.items() if k in allowed_fields}
        # 确保必需字段存在
        filtered_data.setdefault("job_name", job_name)
        filtered_data.setdefault("sample_count", len(jd_texts))
        filtered_data.setdefault("valid_sample_count", len(jd_texts))

        agent_result = validate_pydantic(filtered_data, JobProfileResult)
        if not agent_result:
            logger.warning(f"Pydantic 校验失败，使用规则画像: {job_name}")
            return fallback_profile, "rule_fallback"

        # 7. 事实与证据约束检查
        if self._has_hallucination(agent_result, jd_texts):
            logger.warning(f"检测到幻觉，使用规则画像: {job_name}")
            return fallback_profile, "rule_fallback"

        # 8. 能力数量限制
        agent_result.must_have_capabilities = agent_result.must_have_capabilities[:6]
        agent_result.nice_to_have_capabilities = agent_result.nice_to_have_capabilities[:5]

        # 9. 必备/加分去重
        must_set = {s.lower() for s in agent_result.must_have_capabilities}
        agent_result.nice_to_have_capabilities = [
            s for s in agent_result.nice_to_have_capabilities
            if s.lower() not in must_set
        ]

        logger.info(f"LLM 岗位画像完成: {job_name}, analysis_mode=agent")
        return agent_result, "agent"

    def _has_hallucination(self, profile: JobProfileResult, jd_texts: list[str]) -> bool:
        """检查是否有编造内容。"""
        # 检查必备能力是否在 JD 中有证据
        for skill in profile.must_have_capabilities:
            if is_fabricated_evidence(skill, jd_texts):
                logger.warning(f"幻觉检测: 必备能力 '{skill}' 不在 JD 中")
                return True

        # 检查职责是否在 JD 中有证据
        for resp in profile.responsibilities:
            if is_fabricated_evidence(resp, jd_texts):
                logger.warning(f"幻觉检测: 职责 '{resp[:30]}' 不在 JD 中")
                return True

        return False


def build_job_profile_from_jds(
    job_name: str,
    source_platform: str = "boss",
    top_n: int = 20,
) -> tuple[JobProfileResult, list[int]]:
    """从最近采集的有效 JD 重建岗位画像。

    流程：规则提取 → LLM Agent 分析 → 保存

    Returns:
        (profile_result, source_document_ids)
    """
    job_name = normalize_job_name(job_name)

    # 查询最近采集的 JD 文档
    doc_ids = _fetch_source_doc_ids(job_name, limit=top_n)
    if not doc_ids:
        logger.warning(f"未找到 job_name={job_name} 的 JD 文档")
        profile = extract_job_profile(job_name, raw_jd_texts=[])
        return profile, []

    # 获取 JD 原文
    jd_texts = _fetch_jd_texts_by_ids(doc_ids)

    # 1. 规则提取
    rule_profile = extract_job_profile(job_name, raw_jd_texts=jd_texts)

    # 2. LLM Agent 分析
    agent = JobProfileAgent()
    profile, analysis_mode = agent.analyze(
        job_name=job_name,
        jd_texts=jd_texts,
        fallback_profile=rule_profile,
    )

    logger.info(
        f"岗位画像重建完成: job_name={job_name}, "
        f"doc_ids={len(doc_ids)}, sample_count={profile.sample_count}, "
        f"analysis_mode={analysis_mode}"
    )
    return profile, doc_ids


def _fetch_source_doc_ids(job_name: str, limit: int = 20) -> list[int]:
    """查询 jd_documents 中该 job_name 的最近文档 ID"""
    with SessionLocal() as session:
        rows = session.execute(
            sql_text(
                "SELECT id FROM jd_documents WHERE job_name = :job "
                "ORDER BY fetched_at DESC LIMIT :limit"
            ),
            {"job": job_name, "limit": limit},
        ).fetchall()
    return [r[0] for r in rows]


def _fetch_jd_texts_by_ids(doc_ids: list[int]) -> list[str]:
    """根据文档 ID 列表获取 JD 原文"""
    if not doc_ids:
        return []
    with SessionLocal() as session:
        placeholders = ",".join([str(i) for i in doc_ids])
        rows = session.execute(
            sql_text(f"SELECT raw_text FROM jd_documents WHERE id IN ({placeholders})")
        ).fetchall()
    return [r[0] for r in rows if r[0]]
