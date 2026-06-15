"""演示数据生成工具 — v0.34

一键生成可演示的最小闭环数据，不依赖外部网络。

用法：
  python scripts/seed_demo_data.py
  python scripts/seed_demo_data.py --clean  # 先清理再生成
"""
from __future__ import annotations
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.database import SessionLocal, init_database
from models.document import JdDocument
from models.profile import JobProfile, CandidateProfile, FitAnalysisReport
from models.user import User
from core.logger import get_logger

logger = get_logger(__name__)

DEMO_JOB_NAME = "AI Agent 应用开发实习生"
DEMO_USER_NAME = "demo_user"

# 演示 JD 文本
DEMO_JD_TEXTS = [
    """岗位职责：
1. 负责 AI Agent 应用的后端开发与维护
2. 参与 Prompt/RAG/Agent 工作流建设
3. 对接业务系统并完成接口开发
4. 参与模型能力落地和工程优化

任职要求：
1. 计算机相关专业本科及以上学历
2. 熟悉 Python、FastAPI、MySQL
3. 了解大模型、RAG、Agent 等概念
4. 有 LangChain/LlamaIndex 使用经验优先
5. 具备良好的沟通协作能力""",
    """职位描述：
参与 AI 大模型应用开发，负责 Agent 工作流搭建和后端服务开发。

任职要求：
1. 本科及以上学历，计算机/软件工程相关专业
2. 熟练掌握 Python，熟悉 FastAPI 或 Django
3. 熟悉 MySQL、Redis、Docker
4. 了解 LLM、Prompt Engineering、RAG
5. 有开源项目或技术博客优先""",
]

# 演示简历文本
DEMO_RESUME_TEXT = """
姓名：张小明
学历：本科 计算机科学与技术专业 2024年毕业

技能：Python、FastAPI、MySQL、Redis、Docker、LangChain、OpenAI API

项目经历：
1. 基于 LangChain 的智能问答系统
   - 使用 LangChain + OpenAI API 构建 RAG 问答系统
   - 实现文档加载、向量检索、Prompt 模板、答案生成全流程
   - 支持 PDF/TXT 文档上传和多轮对话
   - 项目上线后日均查询 200+ 次

2. 求职分析平台后端开发
   - 使用 FastAPI 开发 RESTful API
   - MySQL 存储数据，Redis 缓存热门查询
   - Docker 容器化部署，日活用户 500+

实习经历：
在某科技公司担任后端开发实习生，参与内部 AI 助手项目的开发。

获奖/证书：
- ACM 校赛银奖
- 英语 CET-6
"""


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seed_demo_data(clean_first: bool = False):
    """生成演示数据"""
    init_database()
    session = SessionLocal()

    print(f"\n{'='*50}")
    print(f"  演示数据生成工具")
    print(f"{'='*50}")

    # 检查是否已存在
    existing_user = session.query(User).filter(User.username == DEMO_USER_NAME).first()
    if existing_user and not clean_first:
        print(f"\n  ⚠ 演示用户已存在 (id={existing_user.id})")
        print(f"    使用 --clean 先清理再生成")
        session.close()
        return {
            "user_id": existing_user.id,
            "job_profile_id": None,
            "candidate_profile_id": None,
            "report_id": None,
        }

    # 清理旧演示数据
    if clean_first and existing_user:
        print(f"\n  清理旧演示数据...")
        _cleanup_demo(session, existing_user.id)

    # 1. 创建演示用户
    user = User(username=DEMO_USER_NAME)
    session.add(user)
    session.flush()
    user_id = user.id
    print(f"\n  ✓ 创建演示用户: id={user_id}")

    # 2. 创建 JD 文档
    doc_ids = []
    for i, jd_text in enumerate(DEMO_JD_TEXTS):
        h = _text_hash(jd_text)
        existing = session.query(JdDocument).filter(JdDocument.text_hash == h).first()
        if existing:
            doc_ids.append(existing.id)
            continue
        doc = JdDocument(
            job_name=DEMO_JOB_NAME,
            source_url=f"https://example.com/demo/jd/{i+1}",
            title=f"{DEMO_JOB_NAME} - JD{i+1}",
            company="Demo科技有限公司",
            raw_text=jd_text,
            text_hash=h,
            search_query=DEMO_JOB_NAME,
            fetched_at=datetime.now(),
        )
        session.add(doc)
        session.flush()
        doc_ids.append(doc.id)
    print(f"  ✓ 创建 JD 文档: {len(doc_ids)} 条")

    # 3. 创建岗位画像
    job_profile = JobProfile(
        job_name=DEMO_JOB_NAME,
        profile_version="1.0",
        source_document_ids=json.dumps(doc_ids),
        sample_count=len(doc_ids),
        job_type="实习",
        employment_type="实习",
        target_audience="在校生/应届生",
        responsibilities=json.dumps([
            "负责 AI Agent 应用的后端开发与维护",
            "参与 Prompt/RAG/Agent 工作流建设",
            "对接业务系统并完成接口开发",
        ], ensure_ascii=False),
        must_have_capabilities=json.dumps(["Python", "FastAPI", "MySQL", "LangChain"], ensure_ascii=False),
        nice_to_have_capabilities=json.dumps(["RAG", "Agent", "Docker", "Redis"], ensure_ascii=False),
        experience_requirement="经验不限",
        education_preference="本科",
        major_preference="计算机、软件工程",
        business_context=json.dumps(["AI 应用", "大模型应用"], ensure_ascii=False),
        growth_context=json.dumps(["实习/校招培养", "核心项目"], ensure_ascii=False),
        evidence=json.dumps([{"text": jd_text[:200], "source": f"JD{i+1}"} for i, jd_text in enumerate(DEMO_JD_TEXTS)], ensure_ascii=False),
        confidence="medium",
        quality_flags=json.dumps(["low_sample_count"], ensure_ascii=False),
    )
    session.add(job_profile)
    session.flush()
    job_profile_id = job_profile.id
    print(f"  ✓ 创建岗位画像: id={job_profile_id}")

    # 4. 创建候选人画像
    candidate_profile = CandidateProfile(
        user_id=user_id,
        source_type="resume_text",
        resume_filename="demo_resume.txt",
        raw_text=DEMO_RESUME_TEXT,
        education_background=json.dumps({
            "degree": "本科",
            "major": "计算机科学与技术",
            "graduation_year": "2024",
            "school": "",
        }, ensure_ascii=False),
        skill_stack=json.dumps([
            {"skill": "Python", "confidence": "explicit"},
            {"skill": "FastAPI", "confidence": "explicit"},
            {"skill": "MySQL", "confidence": "explicit"},
            {"skill": "Redis", "confidence": "explicit"},
            {"skill": "Docker", "confidence": "explicit"},
            {"skill": "LangChain", "confidence": "explicit"},
            {"skill": "OpenAI API", "confidence": "explicit"},
        ], ensure_ascii=False),
        projects=json.dumps([
            {"name": "智能问答系统", "description": "基于 LangChain 的 RAG 问答系统", "confidence": "explicit"},
            {"name": "求职分析平台", "description": "FastAPI 后端开发，MySQL+Redis+Docker", "confidence": "explicit"},
        ], ensure_ascii=False),
        internships=json.dumps([
            {"description": "某科技公司后端开发实习生，参与 AI 助手项目", "confidence": "explicit"},
        ], ensure_ascii=False),
        work_experiences=json.dumps([], ensure_ascii=False),
        business_understanding=json.dumps(["AI"], ensure_ascii=False),
        achievements=json.dumps([
            {"description": "日均查询 200+ 次", "has_metric": True},
            {"description": "日活用户 500+", "has_metric": True},
            {"description": "ACM 校赛银奖", "has_metric": False},
        ], ensure_ascii=False),
        learning_signals=json.dumps(["开源参与", "竞赛经历"], ensure_ascii=False),
        transferable_strengths=json.dumps(["团队协作"], ensure_ascii=False),
        collaboration_signals=json.dumps(["团队协作"], ensure_ascii=False),
        risk_points=json.dumps([], ensure_ascii=False),
        evidence=json.dumps([{"text": DEMO_RESUME_TEXT[:200], "source": "简历原文"}], ensure_ascii=False),
        confidence="medium",
        sensitive_detected=json.dumps([], ensure_ascii=False),
    )
    session.add(candidate_profile)
    session.flush()
    candidate_profile_id = candidate_profile.id
    print(f"  ✓ 创建候选人画像: id={candidate_profile_id}")

    # 5. 创建适配分析报告
    report = FitAnalysisReport(
        user_id=user_id,
        job_profile_id=job_profile_id,
        candidate_profile_id=candidate_profile_id,
        overall_fit_level="strong",
        overall_score=85.0,
        fit_summary="综合适配 strong（85分）：能力匹配 strong、经历相关 strong、成长潜力 strong、证据 strong、风险 strong",
        capability_fit=json.dumps({"level": "strong", "score": 80.0, "summary": "核心技能匹配", "evidence_refs": ["Python", "FastAPI", "MySQL", "LangChain"]}, ensure_ascii=False),
        experience_relevance=json.dumps({"level": "strong", "score": 85.0, "summary": "项目经历丰富", "evidence_refs": ["智能问答系统", "求职分析平台"]}, ensure_ascii=False),
        growth_potential=json.dumps({"level": "strong", "score": 90.0, "summary": "学习信号强", "evidence_refs": ["开源参与", "竞赛经历"]}, ensure_ascii=False),
        evidence_strength=json.dumps({"level": "strong", "score": 85.0, "summary": "成果证据充分", "evidence_refs": ["日均查询 200+", "日活 500+"]}, ensure_ascii=False),
        risks_and_gaps=json.dumps({"level": "strong", "score": 90.0, "summary": "无关键风险", "evidence_refs": []}, ensure_ascii=False),
        strengths=json.dumps(["技能覆盖全面，核心技能匹配度高", "项目经验丰富，与岗位需求相关", "有实习经历，具备实践基础", "成果证据充分，有量化数据支撑"], ensure_ascii=False),
        gaps=json.dumps([], ensure_ascii=False),
        transferable_strengths=json.dumps(["团队协作"], ensure_ascii=False),
        learning_plan=json.dumps(["补充「RAG」相关技能和项目经验", "补充「Agent」相关技能和项目经验"], ensure_ascii=False),
        interview_strategy=json.dumps(["重点准备技术深度问题，展示技能掌握程度", "准备项目经历STAR描述，突出成果和贡献", "用成果数据支撑项目价值"], ensure_ascii=False),
        evidence_refs=json.dumps(["Python", "FastAPI", "MySQL", "LangChain", "智能问答系统", "求职分析平台"], ensure_ascii=False),
        confidence="medium",
    )
    session.add(report)
    session.flush()
    report_id = report.id
    print(f"  ✓ 创建适配分析报告: id={report_id}")

    session.commit()
    session.close()

    print(f"\n  {'='*50}")
    print(f"  演示数据生成完成!")
    print(f"  user_id: {user_id}")
    print(f"  job_profile_id: {job_profile_id}")
    print(f"  candidate_profile_id: {candidate_profile_id}")
    print(f"  report_id: {report_id}")
    print(f"  {'='*50}")

    return {
        "user_id": user_id,
        "job_profile_id": job_profile_id,
        "candidate_profile_id": candidate_profile_id,
        "report_id": report_id,
    }


def _cleanup_demo(session, user_id: int):
    """清理旧演示数据"""
    # 删除适配报告
    reports = session.query(FitAnalysisReport).filter(
        FitAnalysisReport.user_id == user_id
    ).all()
    for r in reports:
        session.delete(r)

    # 删除候选人画像
    cands = session.query(CandidateProfile).filter(
        CandidateProfile.user_id == user_id
    ).all()
    for c in cands:
        session.delete(c)

    # 删除岗位画像（通过 job_name）
    profiles = session.query(JobProfile).filter(
        JobProfile.job_name == DEMO_JOB_NAME
    ).all()
    for p in profiles:
        session.delete(p)

    # 删除 JD 文档
    docs = session.query(JdDocument).filter(
        JdDocument.job_name == DEMO_JOB_NAME
    ).all()
    for d in docs:
        session.delete(d)

    # 删除用户
    user = session.query(User).filter(User.id == user_id).first()
    if user:
        session.delete(user)

    session.commit()
    print(f"    ✓ 已清理旧演示数据")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="演示数据生成工具")
    parser.add_argument("--clean", action="store_true", help="先清理再生成")
    args = parser.parse_args()
    seed_demo_data(clean_first=args.clean)
