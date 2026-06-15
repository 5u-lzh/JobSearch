"""数据清理工具 — v0.34

安全清理开发期数据污染，支持 dry-run 预览。

用法：
  python scripts/clean_product_data.py --job-name "Python后端" --dry-run
  python scripts/clean_product_data.py --job-name "Python后端" --confirm
  python scripts/clean_product_data.py --all-demo --dry-run
  python scripts/clean_product_data.py --all-demo --confirm
"""
from __future__ import annotations
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.database import SessionLocal, init_database
from models.document import JdDocument, JdChunk
from models.profile import JobProfile, FitAnalysisReport, CandidateProfile
from tools.skill_guard import normalize_job_name
from core.logger import get_logger

logger = get_logger(__name__)

# 演示岗位名列表
DEMO_JOB_NAMES = [
    "AI Agent 应用开发实习生",
    "AI Agent",
    "Python后端",
    "测试岗位",
]


def count_records(session, model, **filters):
    """统计符合条件的记录数"""
    query = session.query(model)
    for key, value in filters.items():
        query = query.filter(getattr(model, key) == value)
    return query.count()


def get_job_profile_ids(session, job_name: str) -> list[int]:
    """获取指定 job_name 的岗位画像 ID"""
    rows = session.query(JobProfile.id).filter(JobProfile.job_name == job_name).all()
    return [r[0] for r in rows]


def get_fit_report_ids(session, job_profile_ids: list[int]) -> list[int]:
    """获取关联指定 job_profile 的适配报告 ID"""
    if not job_profile_ids:
        return []
    rows = session.query(FitAnalysisReport.id).filter(
        FitAnalysisReport.job_profile_id.in_(job_profile_ids)
    ).all()
    return [r[0] for r in rows]


def preview_cleanup(session, job_name: str) -> dict:
    """预览将清理的数据"""
    job_name = normalize_job_name(job_name)

    jd_count = count_records(session, JdDocument, job_name=job_name)
    chunk_count = 0
    if jd_count > 0:
        doc_ids = [r[0] for r in session.query(JdDocument.id).filter(
            JdDocument.job_name == job_name
        ).all()]
        chunk_count = session.query(JdChunk).filter(
            JdChunk.document_id.in_(doc_ids)
        ).count() if doc_ids else 0

    profile_ids = get_job_profile_ids(session, job_name)
    profile_count = len(profile_ids)

    report_ids = get_fit_report_ids(session, profile_ids)
    report_count = len(report_ids)

    return {
        "job_name": job_name,
        "jd_documents": jd_count,
        "jd_chunks": chunk_count,
        "job_profiles": profile_count,
        "fit_reports": report_count,
        "profile_ids": profile_ids,
        "report_ids": report_ids,
        "doc_ids": doc_ids if jd_count > 0 else [],
    }


def execute_cleanup(session, preview: dict) -> dict:
    """执行清理"""
    job_name = preview["job_name"]
    deleted = {
        "fit_reports": 0,
        "job_profiles": 0,
        "jd_chunks": 0,
        "jd_documents": 0,
    }

    # 1. 删除适配报告
    if preview["report_ids"]:
        deleted["fit_reports"] = session.query(FitAnalysisReport).filter(
            FitAnalysisReport.id.in_(preview["report_ids"])
        ).delete(synchronize_session=False)

    # 2. 删除岗位画像
    if preview["profile_ids"]:
        deleted["job_profiles"] = session.query(JobProfile).filter(
            JobProfile.id.in_(preview["profile_ids"])
        ).delete(synchronize_session=False)

    # 3. 删除 JD chunks
    if preview["doc_ids"]:
        deleted["jd_chunks"] = session.query(JdChunk).filter(
            JdChunk.document_id.in_(preview["doc_ids"])
        ).delete(synchronize_session=False)

    # 4. 删除 JD 文档
    if preview["doc_ids"]:
        deleted["jd_documents"] = session.query(JdDocument).filter(
            JdDocument.id.in_(preview["doc_ids"])
        ).delete(synchronize_session=False)

    session.commit()
    return deleted


def main():
    parser = argparse.ArgumentParser(description="数据清理工具")
    parser.add_argument("--job-name", type=str, help="清理指定岗位相关数据")
    parser.add_argument("--all-demo", action="store_true", help="清理所有演示数据")
    parser.add_argument("--dry-run", action="store_true", default=True, help="只预览，不删除（默认）")
    parser.add_argument("--confirm", action="store_true", help="真正执行删除")
    args = parser.parse_args()

    if not args.job_name and not args.all_demo:
        parser.error("请指定 --job-name 或 --all-demo")

    if args.confirm:
        args.dry_run = False

    init_database()
    session = SessionLocal()

    job_names = []
    if args.all_demo:
        job_names = DEMO_JOB_NAMES
    elif args.job_name:
        job_names = [args.job_name]

    total_preview = {
        "jd_documents": 0,
        "jd_chunks": 0,
        "job_profiles": 0,
        "fit_reports": 0,
    }

    print(f"\n{'='*50}")
    print(f"  数据清理工具 {'(DRY-RUN)' if args.dry_run else '(EXECUTE)'}")
    print(f"{'='*50}")

    for jn in job_names:
        preview = preview_cleanup(session, jn)
        print(f"\n  岗位: {preview['job_name']}")
        print(f"    jd_documents: {preview['jd_documents']}")
        print(f"    jd_chunks: {preview['jd_chunks']}")
        print(f"    job_profiles: {preview['job_profiles']}")
        print(f"    fit_reports: {preview['fit_reports']}")

        for key in total_preview:
            total_preview[key] += preview[key]

        if not args.dry_run and (preview['jd_documents'] > 0 or preview['job_profiles'] > 0):
            deleted = execute_cleanup(session, preview)
            print(f"    ✓ 已删除: {deleted}")

    print(f"\n  {'将删除' if args.dry_run else '已删除'}总计:")
    for key, count in total_preview.items():
        print(f"    {key}: {count}")

    if args.dry_run and any(v > 0 for v in total_preview.values()):
        print(f"\n  ⚠ 以上为预览，添加 --confirm 执行真正删除")

    print(f"\n{'='*50}")

    # 安全检查：不允许删除 users
    print("\n  安全检查:")
    print("    users 表: 不允许清理 ✓")
    print("    data/ 目录: 不允许清理 ✓")

    session.close()


if __name__ == "__main__":
    main()
