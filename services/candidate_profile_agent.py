"""候选人画像 Agent — v0.36 LLM 优先 + 规则兜底

分析流程：
1. 规则提取教育、技能、项目、实习、工作、成果及证据
2. LLM 基于简历原文与规则证据综合归纳
3. Pydantic schema 校验
4. 事实与证据约束检查
5. 任一步失败则返回规则画像
"""
from __future__ import annotations
import json
import re
from typing import Optional

from services.candidate_profile_service import extract_candidate_profile
from services.profile_schemas import CandidateProfileResult, EvidenceItem
from services.agent_common import (
    call_llm, parse_json_from_llm, validate_pydantic,
    build_candidate_evidence, is_fabricated_evidence,
)
from core.logger import get_logger

logger = get_logger(__name__)

# ── 候选人画像 System Prompt ──

CANDIDATE_PROFILE_PROMPT = """你是候选人画像分析 Agent。你的任务是基于简历原文和规则提取的证据，综合归纳出结构化的候选人画像。

## 核心规则
1. **只依据简历原文**，不得编造信息。
2. 不得推测未写出的学校、专业、工作年限或成果。
3. 不得使用年龄、性别、民族、婚育等敏感信息进行评价。
4. 明确区分教育、技能、项目、实习和工作经历。
5. 不得把项目经历伪装成工作经历。
6. 输出纯 JSON，不要输出 Markdown 或任何其他格式。
7. 所有字段必须匹配 schema，不得有额外字段。

## 提取要求

### 教育背景 (education_background)
- 识别院校（XX大学/XX学院/XX University）
- 识别专业（优先匹配"专业：xxx"明确声明）
- 识别学历（本科/硕士/博士/大专）
- 识别毕业年份
- 不要把"数学建模"误识别为"数学专业"

### 技能栈 (skill_stack)
- 每项包含 skill 和 confidence（explicit/inferred）
- 去重、规范大小写
- 最多 20 项

### 项目经历 (projects)
- 每项包含 name、description、tech_stack、achievements、confidence
- 从"项目经历/项目经验"段落提取

### 实习/工作经历
- 每项包含 company、role、period、description、achievements、confidence
- 不要把成果句错误拆成单独经历

### 成果证据 (achievements)
- 识别量化表达（提升 X%、处理 X 条数据等）
- 每项包含 description 和 has_metric

### 风险点 (risk_points)
- 基于证据生成，不编造
- 如：缺少实习、项目证据不足

## 输出 JSON 格式
{{
  "education_background": {{"degree": "", "major": "", "school": "", "graduation_year": ""}},
  "skill_stack": [{{"skill": "Python", "confidence": "explicit"}}],
  "projects": [{{"name": "", "description": "", "tech_stack": [], "achievements": [], "confidence": "explicit"}}],
  "internships": [{{"company": "", "role": "", "period": "", "description": "", "achievements": [], "confidence": "explicit"}}],
  "work_experiences": [{{"company": "", "role": "", "period": "", "description": "", "achievements": [], "confidence": "explicit"}}],
  "achievements": [{{"description": "", "has_metric": false}}],
  "learning_signals": [],
  "transferable_strengths": [],
  "risk_points": [],
  "confidence": "high/medium/low"
}}

## 输入

### 简历原文
{resume_text}

### 规则提取的参考信号
{rule_signals}

只输出 JSON。"""


class CandidateProfileAgent:
    """候选人画像 Agent — LLM 优先 + 规则兜底。"""

    def analyze(
        self,
        resume_text: str,
        fallback_profile: Optional[CandidateProfileResult] = None,
    ) -> tuple[CandidateProfileResult, str]:
        """分析候选人画像。

        Args:
            resume_text: 简历原文
            fallback_profile: 规则提取的兜底画像

        Returns:
            (profile_result, analysis_mode)
        """
        # 1. 规则兜底
        if fallback_profile is None:
            fallback_profile = extract_candidate_profile(resume_text=resume_text)

        # 2. 检查文本
        if not resume_text or len(resume_text.strip()) < 50:
            logger.info("简历文本过短，使用规则画像")
            return fallback_profile, "rule_fallback"

        # 3. 构建 LLM prompt
        rule_signals = build_candidate_evidence(fallback_profile.model_dump())
        prompt = CANDIDATE_PROFILE_PROMPT.format(
            resume_text=resume_text[:3000],
            rule_signals=rule_signals,
        )

        # 4. 调用 LLM
        logger.info("调用 LLM 分析候选人画像")
        raw_response = call_llm(prompt, max_tokens=2000, timeout=60)
        if not raw_response:
            logger.warning("LLM 调用失败，使用规则画像")
            return fallback_profile, "rule_fallback"

        # 5. 解析 JSON
        data = parse_json_from_llm(raw_response)
        if not data:
            logger.warning("LLM 返回非法 JSON，使用规则画像")
            return fallback_profile, "rule_fallback"

        # 6. Pydantic 校验
        allowed_fields = set(CandidateProfileResult.model_fields.keys())
        filtered_data = {k: v for k, v in data.items() if k in allowed_fields}
        filtered_data.setdefault("confidence", "low")

        agent_result = validate_pydantic(filtered_data, CandidateProfileResult)
        if not agent_result:
            logger.warning("Pydantic 校验失败，使用规则画像")
            return fallback_profile, "rule_fallback"

        # 7. 事实约束检查
        if self._has_hallucination(agent_result, resume_text):
            logger.warning("检测到幻觉，使用规则画像")
            return fallback_profile, "rule_fallback"

        # 8. 技能去重
        seen = set()
        deduped = []
        for item in agent_result.skill_stack:
            name = (item.get("skill") or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                deduped.append(item)
        agent_result.skill_stack = deduped[:20]

        logger.info("LLM 候选人画像完成, analysis_mode=agent")
        return agent_result, "agent"

    def _has_hallucination(self, profile: CandidateProfileResult, resume_text: str) -> bool:
        """检查是否有编造内容。"""
        resume_lower = resume_text.lower()

        # 检查院校是否在简历中出现
        school = profile.education_background.get("school", "")
        if school and school.lower() not in resume_lower:
            # 院校名是严格要求，必须完全匹配
            logger.warning(f"幻觉检测: 院校 '{school}' 不在简历中")
            return True

        # 检查项目名是否在简历中有相关证据
        # 项目名可能是简称（如"求职平台" vs "求职分析平台"）
        # 检查项目名是否是简历中某个词的子串，或简历中有项目名的关键词
        for proj in profile.projects[:3]:
            name = proj.get("name", "")
            if not name or len(name) <= 2:
                continue
            name_lower = name.lower()
            # 1. 检查项目名是否直接出现在简历中
            if name_lower in resume_lower:
                continue
            # 2. 检查简历中是否有包含项目名关键词的词
            #    例如 "求职平台" 的关键词是 "求职" 和 "平台"
            #    简历中有 "求职分析平台"，包含 "求职" 和 "平台"
            keywords = set()
            # 提取 2 字中文关键词
            for match in re.finditer(r'[一-鿿]{2}', name):
                keywords.add(match.group().lower())
            # 检查是否有至少一个关键词在简历中
            found = any(kw in resume_lower for kw in keywords)
            if not found:
                logger.warning(f"幻觉检测: 项目 '{name}' 不在简历中")
                return True

        return False


def extract_candidate_profile_with_agent(
    resume_text: str = "",
    user_id: int = 0,
    resume_filename: str = "",
    conversation_text: str = "",
) -> tuple[CandidateProfileResult, str]:
    """提取候选人画像（Agent 优先 + 规则兜底）。

    Returns:
        (profile_result, analysis_mode)
    """
    text_value = (resume_text or "").strip()
    if conversation_text:
        text_value += "\n" + conversation_text

    # 1. 规则提取
    rule_profile = extract_candidate_profile(
        resume_text=resume_text,
        user_id=user_id,
        resume_filename=resume_filename,
        conversation_text=conversation_text,
    )

    # 2. LLM Agent
    agent = CandidateProfileAgent()
    profile, analysis_mode = agent.analyze(
        resume_text=text_value,
        fallback_profile=rule_profile,
    )

    return profile, analysis_mode
