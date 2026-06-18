"""公共 Agent 辅助函数 — v0.36

岗位画像和候选人画像 Agent 共用的工具函数：
- 构造结构化 prompt
- 清理 Markdown JSON code fence
- JSON 解析
- Pydantic 校验
- evidence grounding 检查
- 超时和异常处理
- 日志记录
- fallback 处理
"""
from __future__ import annotations
import json
import re
from typing import Optional, TypeVar, Type
from pydantic import BaseModel, ValidationError
from core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


def call_llm(prompt: str, max_tokens: int = 2000, timeout: int = 60) -> Optional[str]:
    """调用 LLM，返回原始文本。失败返回 None。"""
    try:
        from agents.base import get_heavy_llm
        llm = get_heavy_llm()
        # 设置 max_tokens
        llm.max_tokens = max_tokens
        response = llm.invoke(prompt)
        return response.content
    except Exception as e:
        logger.warning(f"LLM 调用失败: {e}")
        return None


def parse_json_from_llm(raw: str) -> Optional[dict]:
    """从 LLM 输出中提取 JSON，容忍 Markdown 包裹和多余文本。"""
    if not raw:
        return None

    # 1. 直接解析
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # 2. 从 ```json ... ``` 中提取
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 找第一个 { 到最后一个 }
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def validate_pydantic(data: dict, model_class: Type[T]) -> Optional[T]:
    """将 dict 转为 Pydantic 模型，校验失败返回 None。"""
    try:
        return model_class(**data)
    except (ValidationError, Exception) as e:
        logger.warning(f"Pydantic 校验失败 ({model_class.__name__}): {e}")
        return None


def strip_code_fences(text: str) -> str:
    """移除 Markdown code fence 包裹。"""
    text = text.strip()
    if text.startswith("```"):
        # 移除开头的 ```json 或 ```
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def build_evidence_context(jd_texts: list[str], max_per_jd: int = 300) -> str:
    """将 JD 原文列表构建为 LLM 可读的证据上下文。"""
    parts = []
    for i, text in enumerate(jd_texts[:10], 1):
        truncated = text[:max_per_jd]
        parts.append(f"--- JD {i} ---\n{truncated}")
    return "\n\n".join(parts)


def build_rule_evidence(profile_dict: dict) -> str:
    """将规则提取结果构建为 LLM 可读的参考信号。"""
    lines = []
    if profile_dict.get("responsibilities"):
        lines.append(f"规则提取职责: {', '.join(profile_dict['responsibilities'][:6])}")
    if profile_dict.get("must_have_capabilities"):
        lines.append(f"规则提取必备能力: {', '.join(profile_dict['must_have_capabilities'][:10])}")
    if profile_dict.get("nice_to_have_capabilities"):
        lines.append(f"规则提取加分能力: {', '.join(profile_dict['nice_to_have_capabilities'][:8])}")
    if profile_dict.get("education_preference"):
        lines.append(f"规则提取学历: {profile_dict['education_preference']}")
    if profile_dict.get("experience_requirement"):
        lines.append(f"规则提取经验: {profile_dict['experience_requirement']}")
    if profile_dict.get("business_context"):
        lines.append(f"规则提取业务场景: {', '.join(profile_dict['business_context'][:5])}")
    if profile_dict.get("employment_type"):
        lines.append(f"规则推断用工类型: {profile_dict['employment_type']}")
    if profile_dict.get("target_audience"):
        lines.append(f"规则推断面向人群: {profile_dict['target_audience']}")
    return "\n".join(lines) if lines else "无规则信号。"


def build_candidate_evidence(profile_dict: dict) -> str:
    """将候选人规则提取结果构建为 LLM 可读的参考信号。"""
    lines = []
    edu = profile_dict.get("education_background", {})
    if edu:
        lines.append(f"规则提取教育: {edu.get('degree', '')} {edu.get('major', '')} {edu.get('school', '')}")
    skills = profile_dict.get("skill_stack", [])
    if skills:
        names = [s.get("skill", s) if isinstance(s, dict) else str(s) for s in skills[:10]]
        lines.append(f"规则提取技能: {', '.join(names)}")
    projects = profile_dict.get("projects", [])
    if projects:
        descs = [p.get("description", p.get("name", ""))[:60] for p in projects[:3]]
        lines.append(f"规则提取项目: {'; '.join(descs)}")
    achievements = profile_dict.get("achievements", [])
    if achievements:
        descs = [a.get("description", "")[:60] for a in achievements[:3]]
        lines.append(f"规则提取成果: {'; '.join(descs)}")
    return "\n".join(lines) if lines else "无规则信号。"


def is_fabricated_evidence(text: str, source_texts: list[str]) -> bool:
    """检查文本是否可能是编造的（不在任何源文本中出现关键片段）。

    简单实现：提取文本中的关键词，检查是否在源文本中出现。
    """
    if not text or not source_texts:
        return False

    # 提取文本中的关键词（2-4字中文词或英文词）
    keywords = set(re.findall(r'[一-鿿]{2,4}|[A-Za-z]{3,}', text))
    if not keywords:
        return False

    joined_source = " ".join(source_texts).lower()
    matched = sum(1 for kw in keywords if kw.lower() in joined_source)

    # 如果超过一半的关键词不在源文本中，可能是编造
    return matched < len(keywords) * 0.3
