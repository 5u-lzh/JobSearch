"""Boss 直聘岗位 JD 采集服务 — v0.25.1

优先使用 Playwright 浏览器采集（真实浏览器上下文）。
浏览器未启动时返回提示，不再使用 httpx 模拟请求。
支持手动粘贴 JD 文本导入作为备用路径。

不做：投递、保存账号密码 Cookie、绕过验证码/风控。
"""
from __future__ import annotations
import hashlib
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import SessionLocal
from models.document import JdDocument
from services.jd_quality_service import filter_jd_items
from tools.skill_guard import normalize_job_name
from core.logger import get_logger

logger = get_logger(__name__)


class BossCaptureRequest(BaseModel):
    """Boss 采集请求参数"""
    job_name: str
    city: str = ""
    max_jobs: int = Field(default=10, ge=1, le=30)
    filters: dict = Field(default_factory=dict)  # salary, experience, education, job_type


class BossCaptureResult(BaseModel):
    """Boss 采集结果"""
    captured_count: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    detail_missing_count: int = 0
    blocked_reason: str = ""
    documents: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    failed_examples: list[dict] = Field(default_factory=list)
    profile_generated: bool = False
    job_profile_id: int = 0
    job_profile: dict = Field(default_factory=dict)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def capture_boss_jds(req: BossCaptureRequest) -> BossCaptureResult:
    """Boss 直聘 JD 采集主流程。

    优先使用 Playwright 浏览器采集：
    1. 检查浏览器状态
    2. 使用浏览器 XHR/DOM 采集岗位列表
    3. 质量过滤后入库

    浏览器未启动时返回 blocked_reason 提示。
    """
    from services.boss_browser_capture import get_browser_capture

    job_name = normalize_job_name(req.job_name)
    result = BossCaptureResult()

    if not job_name:
        result.blocked_reason = "岗位关键词不能为空"
        return result

    logger.info(f"Boss 采集开始: job_name={job_name}, city={req.city}")

    # 检查浏览器状态
    browser = get_browser_capture()
    bs = browser.status()

    if not bs.running:
        result.blocked_reason = "浏览器未启动，请先点击「启动浏览器」"
        result.warnings.append("启动浏览器后，在浏览器中完成 Boss 直聘登录，再点击采集")
        return result

    if not bs.logged_in:
        result.blocked_reason = "需要登录 Boss 直聘，请在浏览器中完成登录后重试"
        result.warnings.append("登录完成后点击「我已完成登录，重试采集」")
        return result

    try:
        # 使用浏览器采集
        jobs = browser.search_and_capture(
            job_name=job_name,
            city=req.city,
            max_jobs=req.max_jobs,
            filters=req.filters,
        )

        result.captured_count = len(jobs)

        if not jobs:
            result.warnings.append("未采集到岗位，可能搜索无结果或页面结构变化")
            result.warnings.append("请尝试手动粘贴 JD 文本")
            return result

        # 构建 JD items（兼容现有质量过滤和入库逻辑）
        jd_items = []
        failed_examples = []
        for job in jobs:
            raw_text = (job.get("raw_text") or "").strip()
            is_detail_missing = job.get("detail_missing", False)
            is_fallback_only = job.get("fallback_only", False)

            if not raw_text:
                result.failed_count += 1
                if len(failed_examples) < 3:
                    failed_examples.append({
                        "title": job.get("title", ""),
                        "reason": "未提取到 JD 详情文本",
                    })
                continue

            if is_fallback_only:
                # fallback 数据标记为低质量
                result.detail_missing_count += 1
                jd_items.append({
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "url": job.get("source_url", ""),
                    "content": raw_text,
                    "quality_flags": ["detail_missing"],
                })
            else:
                jd_items.append({
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "url": job.get("source_url", ""),
                    "content": raw_text,
                })

        result.failed_examples = failed_examples

        # 质量过滤
        if jd_items:
            valid_jds, filtered_jds, summary = filter_jd_items(jd_items, job_name=job_name)
            result.skipped_count = len(filtered_jds)

            # 入库（text_hash 去重）
            imported = _store_jds(job_name, req.city, valid_jds)
            result.imported_count = imported
            result.documents = [
                {"title": jd.get("title", ""), "company": jd.get("company", "")}
                for jd in valid_jds
            ]

        # 生成更可解释的警告
        if result.imported_count == 0 and result.captured_count > 0:
            result.warnings.append(
                f"已抓到 {result.captured_count} 个岗位列表，但未获取到 JD 详情。"
                "可能是 Boss 详情接口字段变化或风控，请尝试打开岗位详情后重试，或手动粘贴 JD。"
            )
        if result.detail_missing_count > 0:
            result.warnings.append(
                f"{result.detail_missing_count} 个岗位只采集到卡片信息，未采集到完整 JD，已标记为低质量"
            )

        # 采集成功后自动生成岗位画像
        if result.imported_count > 0:
            try:
                from services.job_profile_agent import build_job_profile_from_jds
                from services.job_profile_service import save_job_profile
                profile, doc_ids = build_job_profile_from_jds(job_name, "boss", 20)
                profile_id = save_job_profile(profile, source_doc_ids=doc_ids)
                result.profile_generated = True
                result.job_profile_id = profile_id
                result.job_profile = profile.model_dump()
                logger.info(f"岗位画像已自动生成: profile_id={profile_id}, sample_count={profile.sample_count}")
            except Exception as e:
                logger.warning(f"自动生成岗位画像失败: {e}")
                result.warnings.append("画像自动生成失败，请手动点击重建")

    except RuntimeError as e:
        err_msg = str(e)
        if "登录" in err_msg:
            result.blocked_reason = err_msg
            result.warnings.append("请在浏览器中完成登录后重试")
        else:
            result.blocked_reason = f"采集异常: {err_msg}"
            result.warnings.append("请尝试手动粘贴 JD 文本")
    except Exception as e:
        logger.error(f"Boss 采集异常: {e}")
        result.blocked_reason = f"采集异常: {str(e)}"
        result.warnings.append("请尝试手动粘贴 JD 文本")

    if result.imported_count == 0 and not result.blocked_reason:
        result.warnings.append("未成功导入任何 JD，建议手动粘贴 JD 文本")

    logger.info(
        f"Boss 采集完成: captured={result.captured_count}, "
        f"imported={result.imported_count}, skipped={result.skipped_count}, "
        f"failed={result.failed_count}"
    )
    return result


def import_manual_jd(job_name: str, jd_text: str, title: str = "", company: str = "") -> BossCaptureResult:
    """手动导入单条 JD 文本。

    备用路径：用户直接粘贴 JD 原文入库。
    """
    job_name = normalize_job_name(job_name)
    result = BossCaptureResult()

    if not jd_text or not jd_text.strip():
        result.blocked_reason = "JD 文本不能为空"
        return result

    # 检测明显乱码（连续非中文非英文非标点字符）
    import re
    cleaned = jd_text.strip()
    # 检查是否包含中文字符（正常 JD 应该有中文）
    has_chinese = bool(re.search(r'[一-鿿]', cleaned))
    # 检查乱码特征：连续特殊字符
    garbled_pattern = re.compile(r'[\x00-\x08\x0e-\x1f]{3,}')
    if garbled_pattern.search(cleaned):
        result.blocked_reason = "检测到文本编码异常（乱码），请确保使用 UTF-8 编码"
        result.warnings.append("建议直接从浏览器复制 JD 文本，避免从 PowerShell 等非 UTF-8 终端粘贴")
        return result

    # 如果文本很短且没有中文，可能是编码问题
    if len(cleaned) < 50 and not has_chinese:
        result.blocked_reason = "JD 文本过短或可能包含编码问题，请检查后重试"
        return result

    jd_items = [{
        "title": title or f"{job_name} - 手动导入",
        "company": company or "",
        "url": "",
        "content": cleaned,
    }]

    # 质量过滤
    valid_jds, filtered_jds, summary = filter_jd_items(jd_items, job_name=job_name)
    result.captured_count = 1
    result.skipped_count = len(filtered_jds)

    if not valid_jds:
        result.warnings.append("JD 质量不达标，未导入")
        return result

    # 入库
    imported = _store_jds(job_name, "", valid_jds)
    result.imported_count = imported
    result.documents = [
        {"title": jd.get("title", ""), "company": jd.get("company", "")}
        for jd in valid_jds
    ]

    # 自动生成岗位画像
    if imported > 0:
        try:
            from services.job_profile_agent import build_job_profile_from_jds
            from services.job_profile_service import save_job_profile
            profile, doc_ids = build_job_profile_from_jds(job_name, "boss", 20)
            profile_id = save_job_profile(profile, source_doc_ids=doc_ids)
            result.profile_generated = True
            result.job_profile_id = profile_id
            result.job_profile = profile.model_dump()
        except Exception as e:
            logger.warning(f"自动生成岗位画像失败: {e}")

    return result


def _store_jds(job_name: str, city: str, jd_items: list[dict]) -> int:
    """将 JD 写入 jd_documents 表，text_hash 去重。返回新增条数。"""
    imported = 0
    with SessionLocal() as session:
        for item in jd_items:
            raw_text = (item.get("content") or "").strip()
            if not raw_text:
                continue

            h = _text_hash(raw_text)
            existing = session.query(JdDocument).filter(JdDocument.text_hash == h).first()
            if existing:
                logger.debug(f"JD 去重跳过: {item.get('title', '')[:40]}")
                continue

            search_query = f"{job_name} {city}".strip() if city else job_name
            doc = JdDocument(
                job_name=job_name,
                source_url=item.get("url", ""),
                title=item.get("title", ""),
                company=item.get("company", ""),
                raw_text=raw_text,
                text_hash=h,
                search_query=search_query,
                fetched_at=datetime.now(),
            )
            session.add(doc)
            imported += 1

        session.commit()

    return imported
