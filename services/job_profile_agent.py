"""岗位画像 Agent — v0.25

当前使用规则版 extract_job_profile，预留 LLM 多 JD 综合画像分析接口。
后续可替换为 LLM Agent 实现。
"""
from __future__ import annotations
from sqlalchemy import text as sql_text

from models.database import SessionLocal
from services.job_profile_service import extract_job_profile, save_job_profile
from services.profile_schemas import JobProfileResult
from tools.skill_guard import normalize_job_name
from core.logger import get_logger

logger = get_logger(__name__)


class JobProfileAgent:
    """LLM 多 JD 综合画像分析 Agent。

    当前使用规则版 fallback，后续替换为 LLM 实现。
    """

    def analyze(self, job_name: str, jd_texts: list[str]) -> JobProfileResult:
        """从多条 JD 文本综合分析岗位画像。

        Args:
            job_name: 岗位名称
            jd_texts: JD 原文列表

        Returns:
            JobProfileResult 岗位画像
        """
        # 当前使用规则版
        return extract_job_profile(job_name, raw_jd_texts=jd_texts)


def build_job_profile_from_jds(
    job_name: str,
    source_platform: str = "boss",
    top_n: int = 20,
) -> tuple[JobProfileResult, list[int]]:
    """从最近采集的有效 JD 重建岗位画像。

    从 jd_documents 查询该 job_name 的最近 JD，
    调用 extract_job_profile() 生成画像。

    Args:
        job_name: 岗位名称
        source_platform: 来源平台（当前仅 boss）
        top_n: 最多使用多少条 JD

    Returns:
        (profile_result, source_document_ids)
    """
    job_name = normalize_job_name(job_name)

    # 查询最近采集的 JD 文档 ID
    doc_ids = _fetch_source_doc_ids(job_name, limit=top_n)

    if not doc_ids:
        logger.warning(f"未找到 job_name={job_name} 的 JD 文档")
        # 返回空画像
        profile = extract_job_profile(job_name, raw_jd_texts=[])
        return profile, []

    # 使用 extract_job_profile 从数据库读取 JD 并生成画像
    profile = extract_job_profile(job_name, top_n=top_n)

    logger.info(
        f"岗位画像重建完成: job_name={job_name}, "
        f"doc_ids={len(doc_ids)}, sample_count={profile.sample_count}"
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
