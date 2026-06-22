"""FastAPI 服务 —— REST API + 静态文件"""
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from models.database import init_database
from agents.registry import registry
from graphs.analyze import agent_graph as analyze_graph
from memory.long_term import (
    list_analyzed_jobs, list_user_conversations, get_or_create_user,
    delete_user_data, delete_conversation_data,
)
from tools.skill_guard import normalize_job_name
from core.task_manager import task_manager
from core.logger import get_logger

logger = get_logger(__name__)
db = registry.db_tool

STATIC_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"
LANDING_PAGE = Path(__file__).resolve().parent.parent / "joblab-landing-page-v2.html"

app = FastAPI(title="求职技能分析助手")


@app.on_event("startup")
def on_startup():
    logger.info("服务启动，初始化数据库...")
    init_database()
    logger.info("数据库初始化完成")


# ── 静态文件 ──
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(LANDING_PAGE))


@app.get("/app")
def workbench():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ── 用户 ──
@app.get("/users")
def list_users():
    """列出所有用户"""
    from models.database import SessionLocal
    from models.user import User
    with SessionLocal() as session:
        rows = session.query(User).order_by(User.id).all()
    return {"code": 200, "users": [{"id": r.id, "username": r.username} for r in rows]}


@app.get("/user")
def create_or_get_user(username: str = Query("")):
    name = username or f"用户_{str(uuid.uuid4())[:8]}"
    uid = get_or_create_user(name)
    return {"code": 200, "user_id": uid, "username": name}


@app.delete("/user/{user_id}")
def delete_user(user_id: int):
    result = delete_user_data(user_id)
    return {"code": 200, **result}


# ── 对话 ──
class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    user_id: int | None = None


@app.post("/chat")
def chat(request: ChatRequest):
    from graphs.chat import chat_agent_graph, new_thread_id

    tid = request.thread_id or new_thread_id()
    uid = request.user_id or 0

    if uid and not request.thread_id:
        get_or_create_user(f"用户_{tid[:8]}")

    task = task_manager.create("chat")

    def _run(task, _tid, _uid, _msg):
        task.progress = "分析意图..."
        result = chat_agent_graph.invoke(
            {"thread_id": _tid, "user_id": _uid, "user_input": _msg, "task_id": task.task_id},
            config={"configurable": {"thread_id": _tid}},
        )
        return {
            "code": 200,
            "response": result.get("response", ""),
            "thread_id": _tid,
            "knowledge": result.get("knowledge", []),
        }

    task_manager.run(task, _run, tid, uid, request.message)
    return {"code": 200, "task_id": task.task_id, "thread_id": tid, "async": True}


# ── 任务状态查询 ──
@app.get("/task/{task_id}")
def get_task(task_id: str):
    task = task_manager.get(task_id)
    if not task:
        return {"code": 404, "message": "任务不存在"}
    return {"code": 200, "task": task.to_dict()}


@app.post("/task/{task_id}/cancel")
def cancel_task(task_id: str):
    ok = task_manager.cancel(task_id)
    return {"code": 200, "cancelled": ok}


# ── 技能差距分析 ──
class SkillGapRequest(BaseModel):
    job_name: str
    user_skills: list[str] = Field(default_factory=list)
    user_profile: list[dict] = Field(default_factory=list)
    top_n: int = Field(default=15, ge=1, le=50)


@app.post("/skill_gap")
def skill_gap(request: SkillGapRequest):
    if not request.job_name or not request.job_name.strip():
        raise HTTPException(status_code=400, detail="job_name 不能为空")
    from services.skill_gap import analyze_skill_gap
    from services.resume_profile import profile_to_skill_names
    profile_skills = profile_to_skill_names(request.user_profile)
    user_skills = list(dict.fromkeys([*profile_skills, *request.user_skills]))
    result = analyze_skill_gap(
        job_name=request.job_name,
        user_skills=user_skills,
        top_n=request.top_n,
    )
    result["profile_skills"] = profile_skills
    return {"code": 200, **result}


# ── 简历画像分析 ──
class ResumeTextRequest(BaseModel):
    resume_text: str = Field(default="")


@app.post("/profile/resume_text")
def profile_resume_text(request: ResumeTextRequest):
    from services.resume_profile import extract_profile_from_text
    profile = extract_profile_from_text(request.resume_text)
    return {"code": 200, "profile": profile}


@app.post("/profile/resume")
async def profile_resume(
    resume_text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
):
    from services.resume_profile import extract_profile_from_text, extract_text_from_file
    text = resume_text or ""
    filename = ""
    if file is not None:
        filename = file.filename or ""
        text = extract_text_from_file(file.file, filename=filename)
    if not text.strip():
        raise HTTPException(status_code=400, detail="未能从简历中提取文本，请粘贴文本版简历或上传可复制文本的 PDF/DOCX/TXT。")
    profile = extract_profile_from_text(text)
    return {"code": 200, "filename": filename, "text_length": len(text), "profile": profile}


# ── v0.7 岗位画像 / 候选人画像 / 初筛模拟 ──
class CandidateProfileRequest(BaseModel):
    resume_text: str = Field(default="")
    user_profile: list[dict] = Field(default_factory=list)


class ScreeningReportRequest(BaseModel):
    job_name: str
    resume_text: str = Field(default="")
    user_profile: list[dict] = Field(default_factory=list)
    top_n: int = Field(default=20, ge=1, le=50)


@app.get("/job_profile/{job_name}")
def job_profile(job_name: str, top_n: int = Query(20, ge=1, le=50)):
    if not job_name or not job_name.strip():
        raise HTTPException(status_code=400, detail="job_name 不能为空")
    from services.screening import build_job_profile
    return {"code": 200, "profile": build_job_profile(job_name, top_n=top_n)}


@app.post("/candidate_profile")
def candidate_profile(request: CandidateProfileRequest):
    from services.screening import extract_candidate_profile
    profile = extract_candidate_profile(
        resume_text=request.resume_text,
        user_profile=request.user_profile,
    )
    return {"code": 200, "profile": profile}


@app.post("/screening_report")
def screening_report(request: ScreeningReportRequest):
    if not request.job_name or not request.job_name.strip():
        raise HTTPException(status_code=400, detail="job_name 不能为空")
    from services.screening import build_screening_report
    report = build_screening_report(
        job_name=request.job_name,
        resume_text=request.resume_text,
        user_profile=request.user_profile,
        top_n=request.top_n,
    )
    return {"code": 200, "report": report}


# ── 深度研究（直接走 research_graph，不经过 ChatAgent） ──
class ResearchRequest(BaseModel):
    topic: str


@app.post("/research")
def research(request: ResearchRequest):
    """直接从已有数据库搜索多维度信息，LLM 生成研究报告。
    Legacy capability: no longer exposed as a standalone UI.
    Reserved for future job/profile/report contextual research.
    """
    from graphs.research import research_graph
    import uuid

    tid = str(uuid.uuid4())
    logger.info(f"POST /research topic={request.topic[:50]}...")

    t0 = time.time()
    result = research_graph.invoke(
        {"user_input": request.topic, "messages": []},
        config={"configurable": {"thread_id": f"research_{tid}"}},
    )
    elapsed = time.time() - t0
    logger.info(f"POST /research 完成 -> 耗时 {elapsed:.1f}s, {len(result.get('knowledge',[]))} 卡片")

    return {
        "code": 200,
        "knowledge": result.get("knowledge", []),
        "response": result.get("response", ""),
        "elapsed": f"{elapsed:.1f}s",
    }


# ── 对话历史消息 ──
@app.get("/conversation/{thread_id}")
def get_conversation_messages(thread_id: str):
    """从 SqliteSaver 恢复对话消息"""
    from graphs.chat import chat_agent_graph
    try:
        state = chat_agent_graph.get_state(
            config={"configurable": {"thread_id": thread_id}}
        )
        if state and state.values:
            msgs = state.values.get("messages", [])
            if msgs:
                return {"code": 200, "messages": msgs}
    except Exception as e:
        logger.warning(f"graph state 恢复会话失败: {thread_id[:8]}... {e}")
    try:
        from memory.short_term import load_messages_from_writes
        msgs = load_messages_from_writes(thread_id)
        if msgs:
            return {"code": 200, "messages": msgs, "source": "writes"}
    except Exception as e:
        logger.warning(f"writes fallback 恢复会话失败: {thread_id[:8]}... {e}")
    return {"code": 200, "messages": []}


@app.delete("/conversation/{thread_id}")
def delete_conversation(thread_id: str, user_id: int = Query(0)):
    result = delete_conversation_data(thread_id, user_id or None)
    return {"code": 200, **result}


# ── 历史对话 ──
@app.get("/conversations")
def get_conversations(user_id: int = Query(0), include_orphans: bool = Query(True)):
    if not user_id:
        return {"code": 200, "conversations": []}
    convs = list_user_conversations(user_id)
    if include_orphans:
        try:
            from memory.short_term import list_checkpoint_threads
            known = {c["thread_id"] for c in convs}
            for item in list_checkpoint_threads():
                tid = item["thread_id"]
                if tid in known:
                    continue
                convs.append({
                    "thread_id": tid,
                    "title": f"恢复会话 {tid[:8]}",
                    "created_at": "",
                    "updated_at": "",
                    "recovered": True,
                })
        except Exception as e:
            logger.warning(f"恢复 checkpoint 会话列表失败: {e}")
    return {"code": 200, "conversations": convs}


# ── 技能反馈 ──
class SkillFeedbackRequest(BaseModel):
    user_id: int
    job_name: str
    skill_name: str
    action: str = Field(pattern="^(reject|important)$")


@app.post("/skill_feedback")
def post_skill_feedback(request: SkillFeedbackRequest):
    from models.user import SkillFeedback
    from models.database import SessionLocal as _SessionLocal
    job_name = normalize_job_name(request.job_name)
    with _SessionLocal() as session:
        existing = session.query(SkillFeedback).filter(
            SkillFeedback.user_id == request.user_id,
            SkillFeedback.job_name == job_name,
            SkillFeedback.skill_name == request.skill_name,
            SkillFeedback.action == request.action,
        ).first()
        if not existing:
            session.add(SkillFeedback(
                user_id=request.user_id,
                job_name=job_name,
                skill_name=request.skill_name,
                action=request.action,
            ))
            session.commit()
    return {"code": 200, "message": "ok"}


@app.get("/skill_feedback/summary")
def get_skill_feedback_summary(job_name: str, user_id: int = Query(0)):
    from models.user import SkillFeedback
    from models.database import SessionLocal as _SessionLocal
    from sqlalchemy import func as sqlfunc
    job_name = normalize_job_name(job_name)
    with _SessionLocal() as session:
        rows = session.query(
            SkillFeedback.skill_name,
            SkillFeedback.action,
            sqlfunc.count().label("cnt"),
        ).filter(
            SkillFeedback.job_name == job_name,
        ).group_by(SkillFeedback.skill_name, SkillFeedback.action).all()

        # 当前用户的反馈
        user_rows = set()
        if user_id:
            urows = session.query(SkillFeedback.skill_name, SkillFeedback.action).filter(
                SkillFeedback.job_name == job_name,
                SkillFeedback.user_id == user_id,
            ).all()
            user_rows = {(r[0], r[1]) for r in urows}

    summary = {}
    for skill, action, cnt in rows:
        if skill not in summary:
            summary[skill] = {"reject_count": 0, "important_count": 0, "user_rejected": False, "user_marked_important": False}
        summary[skill][f"{action}_count"] = cnt
        if (skill, "reject") in user_rows:
            summary[skill]["user_rejected"] = True
        if (skill, "important") in user_rows:
            summary[skill]["user_marked_important"] = True

    return {"code": 200, "job_name": job_name, "summary": summary}


# ── 已分析岗位列表 ──
@app.get("/skill_rank/_jobs")
def get_analyzed_jobs():
    jobs = list_analyzed_jobs()
    return {"code": 200, "jobs": jobs}


# ── 技能排名 ──
@app.get("/skill_rank/{job_name}")
def get_skill_rank(job_name: str, top_n: int = 10, user_id: int = Query(0)):
    from services.skill_gap import filter_market_skills, estimate_market_confidence
    from models.user import SkillFeedback
    from sqlalchemy import func as sqlfunc
    job_name = normalize_job_name(job_name)
    logger.info(f"GET /skill_rank/{job_name} top_n={top_n}")

    # 多取一些原始数据，过滤后再截取
    raw_rank = db.get_skill_rank(job_name, min(top_n * 2, 50))

    # 过滤低质量泛词，每项带 confidence + quality_reasons
    rank = filter_market_skills(raw_rank, job_name=job_name, top_n=top_n)

    # JD数量 + 更新时间
    from models.database import SessionLocal
    from sqlalchemy import text
    with SessionLocal() as session:
        jd_total = session.execute(
            text("SELECT COUNT(*) FROM jd_documents WHERE job_name = :job"),
            {"job": job_name},
        ).scalar() or 0
        last_update = session.execute(
            text("SELECT MAX(fetched_at) FROM jd_documents WHERE job_name = :job"),
            {"job": job_name},
        ).scalar()
        if not jd_total:
            jd_total = session.execute(
                text("SELECT COALESCE(MAX(total_jds), 0) FROM job_skills WHERE job_name = :job"),
                {"job": job_name},
            ).scalar() or 0
        if not last_update:
            last_update = session.execute(
                text("SELECT MAX(last_seen_at) FROM job_skills WHERE job_name = :job"),
                {"job": job_name},
            ).scalar()

        # 反馈汇总
        fb_rows = session.query(
            SkillFeedback.skill_name,
            SkillFeedback.action,
            sqlfunc.count().label("cnt"),
        ).filter(
            SkillFeedback.job_name == job_name,
        ).group_by(SkillFeedback.skill_name, SkillFeedback.action).all()

        user_fb = set()
        if user_id:
            urows = session.query(SkillFeedback.skill_name, SkillFeedback.action).filter(
                SkillFeedback.job_name == job_name,
                SkillFeedback.user_id == user_id,
            ).all()
            user_fb = {(r[0], r[1]) for r in urows}

    fb_map = {}
    for skill, action, cnt in fb_rows:
        if skill not in fb_map:
            fb_map[skill] = {"reject_count": 0, "important_count": 0}
        fb_map[skill][f"{action}_count"] = cnt

    # 给每个技能附加反馈标记
    for item in rank:
        s = item["skill"]
        fb = fb_map.get(s, {})
        item["reject_count"] = fb.get("reject_count", 0)
        item["important_count"] = fb.get("important_count", 0)
        item["user_rejected"] = (s, "reject") in user_fb
        item["user_marked_important"] = (s, "important") in user_fb
        item["community_rejected"] = item["reject_count"] >= 3
        item["community_important"] = item["important_count"] >= 3

    last_update_str = last_update if last_update else ""
    conf = estimate_market_confidence(rank, raw_count=len(raw_rank), total_jds=jd_total or 0)

    return {
        "code": 200,
        "data": rank,
        "total_jds": jd_total,
        "last_update": last_update_str,
        "confidence": conf["confidence"],
        "filtered_count": conf["filtered_count"],
    }


# ── 简历文件解析 ──
@app.post("/resume/parse")
async def parse_resume(file: UploadFile = File(default=None)):
    if not file:
        raise HTTPException(status_code=400, detail="未上传文件")
    content = await file.read()
    try:
        from services.resume_parser import parse_resume_file
        result = parse_resume_file(
            filename=file.filename or "",
            content=content,
            content_type=file.content_type or "",
        )
        return {"code": 200, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"简历解析异常: {e}")
        raise HTTPException(status_code=500, detail="简历解析失败，请手动粘贴简历文本")


# ═══ v0.9 画像 + 适配分析 API ═══

# ── 岗位画像 ──
class JobProfileRequest(BaseModel):
    job_name: str
    top_n: int = Field(default=20, ge=1, le=50)


@app.post("/job_profiles/analyze")
def analyze_job_profile(request: JobProfileRequest):
    from services.job_profile_service import extract_job_profile, save_job_profile
    profile = extract_job_profile(request.job_name, top_n=request.top_n)
    profile_id = save_job_profile(profile)
    return {"code": 200, "job_profile_id": profile_id, "profile": profile.model_dump()}


@app.get("/job_profiles/{profile_id}")
def get_job_profile(profile_id: int):
    from models.profile import JobProfile
    from models.database import SessionLocal as _SL
    with _SL() as session:
        obj = session.get(JobProfile, profile_id)
        if not obj:
            return {"code": 404, "message": "岗位画像不存在"}
        return {"code": 200, "profile": {k: getattr(obj, k) for k in [
            "id", "job_name", "job_type", "employment_type", "target_audience",
            "responsibilities", "must_have_capabilities", "nice_to_have_capabilities",
            "experience_requirement", "education_preference", "major_preference",
            "business_context", "growth_context", "evidence", "confidence",
            "quality_flags", "sample_count", "created_at",
        ]}}


# ── 候选人画像 ──
class CandidateProfileRequest(BaseModel):
    user_id: int = 0
    resume_text: str = ""
    resume_filename: str = ""
    conversation_text: str = ""


@app.post("/candidate_profiles/analyze")
def analyze_candidate_profile(request: CandidateProfileRequest):
    from services.candidate_profile_agent import extract_candidate_profile_with_agent
    from services.candidate_profile_service import save_candidate_profile
    from memory.long_term import get_or_create_user

    # 自动处理 user_id=0 的情况
    uid = request.user_id
    if not uid:
        uid = get_or_create_user("default_user")
        logger.info(f"user_id=0, auto-created default user: {uid}")

    # Agent 优先 + 规则兜底
    profile, analysis_mode = extract_candidate_profile_with_agent(
        resume_text=request.resume_text,
        user_id=uid,
        resume_filename=request.resume_filename,
        conversation_text=request.conversation_text,
    )
    profile_id = save_candidate_profile(profile, user_id=uid, resume_filename=request.resume_filename)
    return {
        "code": 200,
        "candidate_profile_id": profile_id,
        "profile": profile.model_dump(),
        "analysis_mode": analysis_mode,
    }


@app.get("/candidate_profiles/{profile_id}")
def get_candidate_profile(profile_id: int):
    from models.profile import CandidateProfile
    from models.database import SessionLocal as _SL
    with _SL() as session:
        obj = session.get(CandidateProfile, profile_id)
        if not obj:
            return {"code": 404, "message": "候选人画像不存在"}
        return {"code": 200, "profile": {k: getattr(obj, k) for k in [
            "id", "user_id", "education_background", "skill_stack",
            "projects", "internships", "work_experiences",
            "business_understanding", "achievements", "learning_signals",
            "transferable_strengths", "collaboration_signals", "risk_points",
            "evidence", "confidence", "sensitive_detected", "created_at",
        ]}}


# ── 综合适配分析 ──
class FitAnalysisRequest(BaseModel):
    user_id: int
    job_profile_id: int
    candidate_profile_id: int


@app.post("/fit_analysis_reports")
def create_fit_analysis(request: FitAnalysisRequest):
    from models.profile import JobProfile, CandidateProfile
    from models.database import SessionLocal as _SL
    from services.fit_analysis_service import analyze_fit, save_fit_analysis
    from services.fit_analysis_agent import analyze_fit_with_agent
    from services.profile_schemas import JobProfileResult, CandidateProfileResult

    with _SL() as session:
        jp = session.get(JobProfile, request.job_profile_id)
        cp = session.get(CandidateProfile, request.candidate_profile_id)
        if not jp or not cp:
            return {"code": 404, "message": "画像不存在"}

        import json
        job_result = JobProfileResult(
            job_name=jp.job_name,
            job_type=jp.job_type,
            employment_type=jp.employment_type,
            target_audience=jp.target_audience,
            responsibilities=json.loads(jp.responsibilities) if jp.responsibilities else [],
            must_have_capabilities=json.loads(jp.must_have_capabilities) if jp.must_have_capabilities else [],
            nice_to_have_capabilities=json.loads(jp.nice_to_have_capabilities) if jp.nice_to_have_capabilities else [],
            experience_requirement=jp.experience_requirement or "",
            education_preference=jp.education_preference or "",
            major_preference=jp.major_preference or "",
            business_context=json.loads(jp.business_context) if jp.business_context else [],
            growth_context=json.loads(jp.growth_context) if jp.growth_context else [],
            confidence=jp.confidence or "low",
            sample_count=jp.sample_count,
        )
        cand_result = CandidateProfileResult(
            education_background=json.loads(cp.education_background) if cp.education_background else {},
            skill_stack=json.loads(cp.skill_stack) if cp.skill_stack else [],
            projects=json.loads(cp.projects) if cp.projects else [],
            internships=json.loads(cp.internships) if cp.internships else [],
            work_experiences=json.loads(cp.work_experiences) if cp.work_experiences else [],
            business_understanding=json.loads(cp.business_understanding) if cp.business_understanding else [],
            achievements=json.loads(cp.achievements) if cp.achievements else [],
            learning_signals=json.loads(cp.learning_signals) if cp.learning_signals else [],
            transferable_strengths=json.loads(cp.transferable_strengths) if cp.transferable_strengths else [],
            collaboration_signals=json.loads(cp.collaboration_signals) if cp.collaboration_signals else [],
            risk_points=json.loads(cp.risk_points) if cp.risk_points else [],
            confidence=cp.confidence or "low",
            sensitive_detected=json.loads(cp.sensitive_detected) if cp.sensitive_detected else [],
        )

    # 先规则分析，再尝试 Agent
    rule_report = analyze_fit(job_result, cand_result)
    report, analysis_mode = analyze_fit_with_agent(job_result, cand_result, rule_report)

    report_id = save_fit_analysis(report, user_id=request.user_id,
                                  job_profile_id=request.job_profile_id,
                                  candidate_profile_id=request.candidate_profile_id)
    return {
        "code": 200,
        "fit_analysis_id": report_id,
        "report": report.model_dump(),
        "analysis_mode": analysis_mode,
        "rule_score": rule_report.overall_score,
    }


@app.get("/fit_analysis_reports/{report_id}")
def get_fit_analysis(report_id: int, user_id: int = Query(0)):
    from services.fit_report_history_service import get_fit_report_detail
    detail = get_fit_report_detail(report_id, user_id=user_id)
    if not detail:
        return {"code": 404, "message": "适配分析报告不存在"}
    return {"code": 200, **detail}


# ── v0.37 适配顾问 ──
class AdvisorRequest(BaseModel):
    user_id: int = 0
    question: str = ""


@app.post("/fit_analysis_reports/{report_id}/advisor")
def ask_fit_advisor(report_id: int, request: AdvisorRequest):
    """基于适配报告的上下文顾问"""
    from services.fit_report_history_service import get_fit_report_detail
    from models.profile import FitAnalysisReport
    from models.database import SessionLocal as _SL
    from services.agent_common import call_llm, parse_json_from_llm

    # 验证报告存在且属于当前用户
    detail = get_fit_report_detail(report_id, user_id=request.user_id)
    if not detail:
        return {"code": 404, "message": "报告不存在或无权访问"}

    report = detail.get("report", {})
    job_profile = detail.get("job_profile", {})
    candidate_profile = detail.get("candidate_profile", {})

    question = (request.question or "").strip()
    if not question:
        return {"code": 400, "message": "请输入问题"}

    # 构建上下文
    context = _build_advisor_context(report, job_profile, candidate_profile)

    # 优先 LLM
    prompt = f"""你是求职适配顾问。基于以下岗位画像、候选人画像和适配分析报告，回答用户问题。

## 规则
1. 只基于输入的画像和报告回答，不得编造信息。
2. 没有证据时明确说明"当前画像中没有足够证据"。
3. 不使用年龄、性别、婚育等敏感信息。
4. 回答要具体、可执行，引用报告中的具体字段。

## 上下文
{context}

## 用户问题
{question}

请直接回答，不要输出 JSON。"""

    answer = call_llm(prompt, max_tokens=1000, timeout=30)
    analysis_mode = "agent"

    if not answer:
        # 规则兜底
        answer = _advisor_rule_fallback(question, report)
        analysis_mode = "rule_fallback"

    # 证据引用
    evidence_refs = report.get("evidence_refs", [])[:5]

    return {
        "code": 200,
        "report_id": report_id,
        "answer": answer,
        "analysis_mode": analysis_mode,
        "evidence_refs": evidence_refs,
    }


def _build_advisor_context(report: dict, job_profile: dict, candidate_profile: dict) -> str:
    """构建顾问上下文"""
    lines = []
    lines.append(f"### 适配报告")
    lines.append(f"- 适配等级: {report.get('overall_fit_level', '未知')}")
    lines.append(f"- 综合分: {report.get('overall_score', 0)}")
    lines.append(f"- 摘要: {report.get('fit_summary', '')}")
    lines.append(f"- 优势: {', '.join(report.get('strengths', []))}")
    lines.append(f"- 差距: {', '.join(report.get('gaps', []))}")
    lines.append(f"- 学习计划: {', '.join(report.get('learning_plan', []))}")
    lines.append(f"- 面试策略: {', '.join(report.get('interview_strategy', []))}")
    lines.append(f"- 证据引用: {', '.join(report.get('evidence_refs', []))}")
    lines.append("")
    lines.append(f"### 岗位画像")
    lines.append(f"- 岗位: {job_profile.get('job_name', '')}")
    lines.append(f"- 必备能力: {', '.join(job_profile.get('must_have_capabilities', []))}")
    lines.append(f"- 加分能力: {', '.join(job_profile.get('nice_to_have_capabilities', []))}")
    lines.append(f"- 职责: {', '.join(job_profile.get('responsibilities', []))}")
    lines.append("")
    lines.append(f"### 候选人画像")
    edu = candidate_profile.get("education_background", {})
    lines.append(f"- 教育: {edu.get('degree', '')} {edu.get('major', '')} {edu.get('school', '')}")
    skills = [s.get("skill", s) if isinstance(s, dict) else str(s) for s in candidate_profile.get("skill_stack", [])]
    lines.append(f"- 技能: {', '.join(skills[:10])}")
    return "\n".join(lines)


def _advisor_rule_fallback(question: str, report: dict) -> str:
    """规则兜底回答"""
    q = question.lower()
    level = report.get("overall_fit_level", "未知")
    score = report.get("overall_score", 0)
    strengths = report.get("strengths", [])
    gaps = report.get("gaps", [])
    learning = report.get("learning_plan", [])
    interview = report.get("interview_strategy", [])

    if "适配等级" in q or "为什么" in q:
        return f"综合适配等级为 **{level}**（{score}分）。" + (
            f"主要优势: {'、'.join(strengths[:3])}。" if strengths else ""
        ) + (f"主要差距: {'、'.join(gaps[:3])}。" if gaps else "")

    if "优先补" in q or "差距" in q:
        if gaps:
            return f"最应该优先补充的差距: {'、'.join(gaps[:5])}。" + (
                f"建议学习计划: {'、'.join(learning[:3])}。" if learning else ""
            )
        return "当前画像中没有明确的差距记录。"

    if "学习" in q or "计划" in q:
        if learning:
            return f"建议学习计划:\n" + "\n".join(f"- {l}" for l in learning[:5])
        return "当前画像中没有足够证据生成学习计划。"

    if "面试" in q:
        if interview:
            return f"面试准备建议:\n" + "\n".join(f"- {s}" for s in interview[:5])
        return "当前画像中没有足够证据生成面试建议。"

    if "简历" in q or "优化" in q:
        if gaps:
            return f"建议在简历中补充以下方面: {'、'.join(gaps[:3])}。"
        return "当前画像中没有明确的优化建议。"

    return f"综合适配等级为 {level}（{score}分）。如需更详细的分析，请提出具体问题。"


# ── 适配报告历史管理 ──
@app.get("/fit_analysis_reports")
def list_fit_reports_endpoint(user_id: int = Query(0), job_name: str = Query(""), limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0)):
    from services.fit_report_history_service import list_fit_reports
    items, total, page = list_fit_reports(user_id=user_id, job_name=job_name, limit=limit, offset=offset)
    return {"code": 200, "items": items, "total": total, **page}


@app.delete("/fit_analysis_reports/{report_id}")
def delete_fit_report(report_id: int, user_id: int = Query(0)):
    from services.fit_report_history_service import delete_fit_report
    ok = delete_fit_report(report_id, user_id=user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="报告不存在或无权删除")
    return {"code": 200, "message": "已删除"}


class RerunRequest(BaseModel):
    user_id: int = 0


@app.post("/fit_analysis_reports/{report_id}/rerun")
def rerun_fit_report(report_id: int, request: RerunRequest):
    from services.fit_report_history_service import rerun_fit_report
    result = rerun_fit_report(report_id, user_id=request.user_id)
    if not result:
        raise HTTPException(status_code=404, detail="报告不存在或无权操作")
    return {"code": 200, "new_report_id": result["id"], "report": result["report"]}


# ── 通用画像反馈 ──
class ProfileFeedbackRequest(BaseModel):
    user_id: int
    target_type: str = Field(pattern="^(job_profile|candidate_profile|fit_analysis_report)$")
    target_id: int
    field_name: str = ""
    item_name: str = ""
    action: str = Field(pattern="^(reject|important|correct|wrong|missing|confirm)$")
    comment: str = ""


@app.post("/profile_feedback")
def post_profile_feedback(request: ProfileFeedbackRequest):
    from models.profile import ProfileFeedback
    from models.database import SessionLocal as _SL
    with _SL() as session:
        session.add(ProfileFeedback(
            user_id=request.user_id,
            target_type=request.target_type,
            target_id=request.target_id,
            field_name=request.field_name,
            item_name=request.item_name,
            action=request.action,
            comment=request.comment,
        ))
        session.commit()
    return {"code": 200, "message": "ok"}


# ── 人工评估 ──
class ProfileEvaluationRequest(BaseModel):
    user_id: int
    target_type: str = Field(pattern="^(job_profile|candidate_profile|fit_analysis_report)$")
    target_id: int
    rating: int = Field(default=0, ge=0, le=5)
    is_correct: bool = True
    error_type: str = Field(default="", pattern="^(|missing_info|wrong_info|hallucination|weak_evidence|bad_suggestion|unfair_judgment|other)$")
    field_name: str = ""
    comment: str = ""
    useful_for_training: bool = False


@app.post("/profile_evaluations")
def post_evaluation(request: ProfileEvaluationRequest):
    from services.profile_evaluation_service import create_evaluation
    try:
        eval_id = create_evaluation(
            user_id=request.user_id,
            target_type=request.target_type,
            target_id=request.target_id,
            rating=request.rating,
            is_correct=request.is_correct,
            error_type=request.error_type,
            field_name=request.field_name,
            comment=request.comment,
            useful_for_training=request.useful_for_training,
        )
        return {"code": 200, "evaluation_id": eval_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/profile_evaluations")
def list_evaluations(
    target_type: str = Query(""),
    target_id: int = Query(0),
    user_id: int = Query(0),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    from services.profile_evaluation_service import list_evaluations
    items = list_evaluations(
        target_type=target_type,
        target_id=target_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    return {"code": 200, "items": items}


@app.get("/profile_evaluations/summary")
def get_evaluation_summary(
    target_type: str = Query(""),
    user_id: int = Query(0),
):
    from services.profile_evaluation_service import summarize_evaluations
    summary = summarize_evaluations(target_type=target_type, user_id=user_id)
    return {"code": 200, **summary}


# ── 统计 ──
@app.get("/stats")
def get_stats():
    """返回技能库总数和JD总量"""
    from models.database import SessionLocal
    from sqlalchemy import text
    with SessionLocal() as session:
        skill_count = session.execute(text("SELECT COUNT(DISTINCT skill_name) FROM job_skills")).scalar() or 0
        jd_count = session.execute(text("SELECT COUNT(*) FROM jd_documents")).scalar() or 0
    return {"code": 200, "skill_count": skill_count, "jd_count": jd_count}


# ═══ v0.25.1 Boss 浏览器采集控制 ═══

@app.post("/boss/browser/start")
def start_boss_browser():
    """启动 Boss 直聘浏览器"""
    from services.boss_browser_capture import start_browser
    result = start_browser(headless=False)
    return {"code": 200, **result.model_dump()}


@app.post("/boss/browser/stop")
def stop_boss_browser():
    """停止 Boss 直聘浏览器"""
    from services.boss_browser_capture import stop_browser
    result = stop_browser()
    return {"code": 200, **result.model_dump()}


@app.get("/boss/browser/status")
def get_boss_browser_status():
    """获取 Boss 直聘浏览器状态"""
    from services.boss_browser_capture import get_browser_status
    result = get_browser_status()
    return {"code": 200, **result.model_dump()}


@app.post("/boss/browser/open-login")
def open_boss_login_page():
    """打开 Boss 登录页"""
    from services.boss_browser_capture import open_login_page
    result = open_login_page()
    return {"code": 200, **result.model_dump()}


# ═══ v0.25 Boss 岗位采集 + 岗位画像重建 ═══

class BossCaptureRequest(BaseModel):
    job_name: str
    extra_job_keywords: list[str] = Field(default_factory=list)
    city: str = ""
    max_jobs: int = Field(default=10, ge=1, le=30)
    filters: dict = Field(default_factory=dict)


@app.post("/jd_sources/boss/capture")
def boss_capture(request: BossCaptureRequest):
    """Boss 直聘 JD 采集"""
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest as _Req
    req = _Req(
        job_name=request.job_name,
        extra_job_keywords=request.extra_job_keywords,
        city=request.city,
        max_jobs=request.max_jobs,
        filters=request.filters,
    )
    result = capture_boss_jds(req)
    return {"code": 200, **result.model_dump()}


class BossManualImportRequest(BaseModel):
    job_name: str
    jd_text: str
    title: str = ""
    company: str = ""


@app.post("/jd_sources/boss/import")
def boss_manual_import(request: BossManualImportRequest):
    """手动导入单条 JD 文本"""
    from services.boss_capture_service import import_manual_jd
    result = import_manual_jd(
        job_name=request.job_name,
        jd_text=request.jd_text,
        title=request.title,
        company=request.company,
    )
    return {"code": 200, **result.model_dump()}


class JobProfileRebuildRequest(BaseModel):
    job_name: str
    source_platform: str = "boss"
    top_n: int = Field(default=20, ge=1, le=50)


@app.post("/job_profiles/rebuild")
def rebuild_job_profile(request: JobProfileRebuildRequest):
    """从最近采集的 JD 重建岗位画像（Agent 优先 + 规则兜底）"""
    from services.job_profile_agent import build_job_profile_from_jds
    from services.job_profile_service import save_job_profile
    profile, doc_ids = build_job_profile_from_jds(
        request.job_name, request.source_platform, request.top_n
    )
    profile_id = save_job_profile(profile, source_doc_ids=doc_ids)
    return {
        "code": 200,
        "job_profile_id": profile_id,
        "profile": profile.model_dump(),
    }


# ── 岗位分析（保留 API） ──
class JobRequest(BaseModel):
    job_name: str


@app.post("/analyze_job")
def analyze_job(request: JobRequest):
    t0 = time.time()
    thread_id = str(uuid.uuid4())
    job_name = normalize_job_name(request.job_name)
    logger.info(f"POST /analyze_job job_name={job_name} thread_id={thread_id}")

    result = analyze_graph.invoke(
        {"job_name": job_name, "status": "开始执行"},
        config={"configurable": {"thread_id": thread_id}},
    )

    elapsed = time.time() - t0
    logger.info(f"POST /analyze_job 完成 -> 耗时 {elapsed:.1f}s")

    return {
        "code": 200,
        "msg": "分析完成",
        "status": result["status"],
        "elapsed": f"{elapsed:.1f}s",
        "thread_id": thread_id,
    }


# ═══ v0.42 岗位数据看板 ═══


def _normalize_job_type(raw: str | None) -> str:
    """统一岗位类型分类，不修改数据库原始数据。"""
    if not raw or not raw.strip():
        return "未分类"
    v = raw.strip().lower()
    if v in ("unknown", "未知", ""):
        return "未分类"
    if v in ("正式", "全职"):
        return "全职"
    if v == "实习":
        return "实习"
    if v == "校招":
        return "校招"
    return raw.strip()


def _build_dashboard_filters(
    job_keyword: str, job_type: str, start_date: str, end_date: str
) -> tuple[dict, list[str], str | None]:
    """校验并构建筛选条件。返回 (params, scope_notes, error)。"""
    import re
    from datetime import datetime

    params: dict = {}
    scope_notes: list[str] = []
    kw = job_keyword.strip()
    jt = job_type.strip()
    sd = start_date.strip()
    ed = end_date.strip()

    if kw:
        params["keyword"] = f"%{kw}%"
        scope_notes.append("岗位关键词影响 JD、画像和技能指标")

    if jt:
        params["job_type_filter"] = jt
        scope_notes.append("岗位类型仅影响画像类型分布")

    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if sd:
        if not date_pattern.match(sd):
            return params, scope_notes, "start_date 格式无效，需 YYYY-MM-DD"
        params["start_date"] = sd
    if ed:
        if not date_pattern.match(ed):
            return params, scope_notes, "end_date 格式无效，需 YYYY-MM-DD"
        params["end_date"] = ed
    if sd and ed:
        try:
            if datetime.strptime(sd, "%Y-%m-%d") > datetime.strptime(ed, "%Y-%m-%d"):
                return params, scope_notes, "start_date 不能晚于 end_date"
        except ValueError:
            return params, scope_notes, "日期格式无效"
    if sd or ed:
        scope_notes.append("时间范围影响 JD 和画像指标（基于 fetched_at / created_at）")

    return params, scope_notes, None


def _build_where_clauses(params: dict) -> tuple[str, str, str]:
    """根据筛选参数构建三组 WHERE 子句片段。
    返回 (jd_where, profile_where, skill_where) 及各自的绑定参数。
    """
    jd_clauses = []
    profile_clauses = []
    skill_clauses = []

    if "keyword" in params:
        jd_clauses.append("AND job_name LIKE :keyword")
        profile_clauses.append("AND job_name LIKE :keyword")
        skill_clauses.append("AND job_name LIKE :keyword")
    if "start_date" in params:
        jd_clauses.append("AND fetched_at >= :start_date")
        profile_clauses.append("AND created_at >= :start_date")
    if "end_date" in params:
        jd_clauses.append("AND fetched_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        profile_clauses.append("AND created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")

    return (
        " ".join(jd_clauses),
        " ".join(profile_clauses),
        " ".join(skill_clauses),
    )


def _build_insights(top_jobs, type_dist, skill_heatmap, kpis) -> list[dict]:
    """根据聚合结果生成最多 3 条数据洞察。"""
    insights = []

    if top_jobs:
        t = top_jobs[0]
        insights.append({
            "icon": "📊",
            "text": f"当前采集量最高的岗位是 {t['job_name']}，共 {t['jd_count']} 条 JD",
        })

    total_profiles = sum(d["count"] for d in type_dist) if type_dist else 0
    fulltime_count = sum(d["count"] for d in type_dist if d["type"] == "全职") if type_dist else 0
    if total_profiles > 0:
        pct = round(fulltime_count / total_profiles * 100)
        if pct > 0:
            insights.append({
                "icon": "💼",
                "text": f"全职岗位占岗位画像的 {pct}%",
            })

    if skill_heatmap:
        s = skill_heatmap[0]
        insights.append({
            "icon": "🔥",
            "text": f"{s['skill']} 是覆盖岗位最多的技能之一（覆盖 {s['jobs']} 个岗位）",
        })

    return insights[:3]


@app.get("/dashboard/overview")
def dashboard_overview(
    job_keyword: str = Query(""),
    job_type: str = Query(""),
    start_date: str = Query(""),
    end_date: str = Query(""),
):
    """返回看板所需的全部聚合数据，支持可选筛选。"""
    from models.database import SessionLocal
    from sqlalchemy import text

    # 校验筛选参数
    params, scope_notes, err = _build_dashboard_filters(
        job_keyword, job_type, start_date, end_date
    )
    if err:
        raise HTTPException(status_code=400, detail=err)

    jd_where, profile_where, skill_where = _build_where_clauses(params)
    type_filter = params.get("job_type_filter", "")

    with SessionLocal() as session:
        # ── KPI ──
        jd_count = session.execute(
            text(f"SELECT COUNT(*) FROM jd_documents WHERE 1=1 {jd_where}"), params
        ).scalar() or 0

        profile_count = session.execute(
            text(f"SELECT COUNT(*) FROM job_profiles WHERE 1=1 {profile_where}"), params
        ).scalar() or 0

        skill_count = session.execute(
            text(f"SELECT COUNT(DISTINCT skill_name) FROM job_skills WHERE 1=1 {skill_where}"), params
        ).scalar() or 0

        report_count = session.execute(
            text("SELECT COUNT(*) FROM fit_analysis_reports"), {}
        ).scalar() or 0

        # ── Top 10 岗位 ──
        top_jobs_rows = session.execute(text(
            f"SELECT job_name, COUNT(*) as cnt FROM jd_documents "
            f"WHERE 1=1 {jd_where} GROUP BY job_name ORDER BY cnt DESC LIMIT 10"
        ), params).fetchall()
        top_jobs = [{"job_name": r[0], "jd_count": r[1]} for r in top_jobs_rows]

        # ── 岗位类型分布（清洗后） ──
        type_rows = session.execute(text(
            f"SELECT job_type FROM job_profiles WHERE 1=1 {profile_where}"
        ), params).fetchall()
        type_counter: dict[str, int] = {}
        for r in type_rows:
            normalized = _normalize_job_type(r[0])
            type_counter[normalized] = type_counter.get(normalized, 0) + 1
        # 如果有类型筛选，只保留匹配的
        if type_filter:
            type_counter = {k: v for k, v in type_counter.items() if k == type_filter}
        job_type_distribution = [{"type": k, "count": v} for k, v in sorted(
            type_counter.items(), key=lambda x: -x[1]
        )]

        # ── 技能热度 ──
        # 优先从 job_skills 表查询（旧分析管道的输出）
        skill_rows = session.execute(text(
            f"SELECT skill_name, SUM(count) as total_count, COUNT(DISTINCT job_name) as job_cnt "
            f"FROM job_skills WHERE 1=1 {skill_where} "
            f"GROUP BY skill_name ORDER BY total_count DESC LIMIT 30"
        ), params).fetchall()
        skill_heatmap = [
            {"skill": r[0], "count": int(r[1] or 0), "jobs": int(r[2] or 0)}
            for r in skill_rows
        ]
        # 如果 job_skills 为空，从 job_profiles 的 JSON 字段兜底提取技能
        if not skill_heatmap:
            import json as _json
            profile_rows = session.execute(text(
                "SELECT job_name, must_have_capabilities, nice_to_have_capabilities FROM job_profiles"
            )).fetchall()
            skill_counter = {}
            for row in profile_rows:
                job_name = row[0] or "未知"
                for field in [row[1], row[2]]:
                    if not field:
                        continue
                    try:
                        skills = _json.loads(field)
                        if isinstance(skills, list):
                            for s in skills:
                                if isinstance(s, str) and s.strip():
                                    key = s.strip()
                                    if key not in skill_counter:
                                        skill_counter[key] = {"count": 0, "jobs": set()}
                                    skill_counter[key]["count"] += 1
                                    skill_counter[key]["jobs"].add(job_name)
                    except (_json.JSONDecodeError, TypeError):
                        pass
            # 按出现次数降序排列，取前30
            sorted_skills = sorted(skill_counter.items(), key=lambda x: -x[1]["count"])[:30]
            skill_heatmap = [
                {"skill": name, "count": info["count"], "jobs": len(info["jobs"])}
                for name, info in sorted_skills
            ]

        # ── 适配分分布 ──
        score_ranges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
        fit_score_distribution = []
        for low, high in score_ranges:
            label = f"{low}-{high - 1}" if high <= 100 else f"{low}-100"
            cnt = session.execute(text(
                "SELECT COUNT(*) FROM fit_analysis_reports "
                "WHERE overall_score >= :low AND overall_score < :high"
            ), {"low": low, "high": high}).scalar() or 0
            fit_score_distribution.append({"range": label, "count": cnt})

        # ── 适配等级分布 ──
        level_rows = session.execute(text(
            "SELECT COALESCE(overall_fit_level, '未知') as lvl, COUNT(*) as cnt "
            "FROM fit_analysis_reports GROUP BY lvl"
        )).fetchall()
        fit_level_distribution = [{"level": r[0], "count": r[1]} for r in level_rows]

        # ── 更新时间 ──
        max_fetched = session.execute(
            text("SELECT MAX(fetched_at) FROM jd_documents")
        ).scalar()
        updated_at = max_fetched if max_fetched else "更新时间暂不可用"

    # ── 洞察 ──
    insights = _build_insights(top_jobs, job_type_distribution, skill_heatmap,
                               {"jd_count": jd_count, "profile_count": profile_count})

    return {
        "code": 200,
        "meta": {
            "source": "JobLab MySQL",
            "updated_at": updated_at,
            "filters": {
                "job_keyword": job_keyword.strip(),
                "job_type": job_type.strip(),
                "start_date": start_date.strip(),
                "end_date": end_date.strip(),
            },
            "filter_scope_notes": scope_notes,
        },
        "kpis": {
            "jd_count": jd_count,
            "profile_count": profile_count,
            "skill_count": skill_count,
            "report_count": report_count,
        },
        "top_jobs": top_jobs,
        "job_type_distribution": job_type_distribution,
        "skill_heatmap": skill_heatmap,
        "fit_score_distribution": fit_score_distribution,
        "fit_level_distribution": fit_level_distribution,
        "insights": insights,
    }


@app.get("/dashboard/skill_trend")
def dashboard_skill_trend(job_name: str = Query("")):
    """返回指定岗位的技能热度排名"""
    if not job_name or not job_name.strip():
        raise HTTPException(status_code=400, detail="job_name 不能为空")
    from models.database import SessionLocal
    from sqlalchemy import text

    job_name_norm = normalize_job_name(job_name)
    with SessionLocal() as session:
        rows = session.execute(text(
            "SELECT skill_name, count, last_seen_at FROM job_skills "
            "WHERE job_name = :job ORDER BY count DESC LIMIT 20"
        ), {"job": job_name_norm}).fetchall()

        skills = [
            {
                "skill": r[0],
                "count": r[1],
                "last_seen": r[2] if r[2] else "",
            }
            for r in rows
        ]

    return {"code": 200, "job_name": job_name_norm, "skills": skills}


def _extract_skill_excerpt(raw_text: str, skill_name: str, max_length: int = 220) -> str:
    """从 JD 原文中截取包含技能词的一句上下文。"""
    import re

    text_value = (raw_text or "").strip()
    skill_value = (skill_name or "").strip()
    if not text_value or not skill_value:
        return ""

    normalized = re.sub(r"\r\n?", "\n", text_value)
    sentences = re.split(r"(?<=[。！？!?；;])\s*|\n+", normalized)
    skill_lower = skill_value.lower()
    sentence = next(
        (item.strip(" \t-•·、") for item in sentences if skill_lower in item.lower()),
        "",
    )
    if not sentence:
        return ""
    sentence = re.sub(r"\s+", " ", sentence).strip()
    if len(sentence) <= max_length:
        return sentence

    index = sentence.lower().find(skill_lower)
    start = max(0, index - max_length // 3)
    end = min(len(sentence), start + max_length)
    excerpt = sentence[start:end].strip()
    return ("…" if start > 0 else "") + excerpt + ("…" if end < len(sentence) else "")


@app.get("/dashboard/skill_evidence")
def dashboard_skill_evidence(
    skill_name: str = Query(""),
    job_name: str = Query(""),
):
    """返回一条真实 JD 中包含指定技能词的文本证据。"""
    if not skill_name or not skill_name.strip():
        raise HTTPException(status_code=400, detail="skill_name 不能为空")

    from models.database import SessionLocal
    from sqlalchemy import text

    skill_value = skill_name.strip()
    job_value = job_name.strip()
    params = {"skill_pattern": f"%{skill_value}%"}
    job_clause = ""
    if job_value:
        params["job_pattern"] = f"%{normalize_job_name(job_value)}%"
        job_clause = "AND job_name LIKE :job_pattern"

    with SessionLocal() as session:
        rows = session.execute(text(
            "SELECT job_name, title, company, raw_text, fetched_at "
            "FROM jd_documents "
            "WHERE raw_text LIKE :skill_pattern "
            f"{job_clause} "
            "ORDER BY fetched_at DESC LIMIT 20"
        ), params).fetchall()

    for row in rows:
        excerpt = _extract_skill_excerpt(row[3], skill_value)
        if excerpt:
            return {
                "code": 200,
                "skill": skill_value,
                "evidence": {
                    "excerpt": excerpt,
                    "job_name": row[0] or "",
                    "title": row[1] or "",
                    "company": row[2] or "",
                    "fetched_at": row[4] if row[4] else "",
                },
            }

    return {"code": 200, "skill": skill_value, "evidence": None}


@app.get("/dashboard/fit_level_detail")
def dashboard_fit_level_detail(
    level: str = Query(""),
    limit: int = Query(6, ge=1, le=12),
):
    """返回指定适配等级的聚合指标与脱敏报告摘要。"""
    import json
    from models.database import SessionLocal
    from models.profile import FitAnalysisReport, JobProfile
    from sqlalchemy import func

    level_value = (level or "").strip().lower()
    if level_value not in {"strong", "moderate", "weak"}:
        raise HTTPException(status_code=400, detail="level 必须是 strong、moderate 或 weak")

    dimension_fields = [
        ("capability_fit", "能力匹配"),
        ("experience_relevance", "经历相关"),
        ("growth_potential", "成长潜力"),
        ("evidence_strength", "证据充分"),
        ("risks_and_gaps", "风险控制"),
    ]

    def parse_json(value, fallback):
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value or "")
        except (json.JSONDecodeError, TypeError):
            return fallback

    with SessionLocal() as session:
        query = session.query(FitAnalysisReport).filter(
            FitAnalysisReport.overall_fit_level == level_value
        )
        total = query.count()
        rows = query.order_by(FitAnalysisReport.created_at.desc()).limit(limit).all()
        avg_score = query.with_entities(
            func.avg(FitAnalysisReport.overall_score)
        ).scalar() or 0

        dimension_totals = {field: [] for field, _ in dimension_fields}
        cards = []
        complete_report_count = 0
        for report in rows:
            job_profile = session.get(JobProfile, report.job_profile_id)
            report_has_detail = False
            for field, _ in dimension_fields:
                dimension = parse_json(getattr(report, field, ""), {})
                score = dimension.get("score")
                if isinstance(score, (int, float)):
                    dimension_totals[field].append(float(score))
                    report_has_detail = True

            gaps = parse_json(report.gaps, [])
            strengths = parse_json(report.strengths, [])
            learning_plan = parse_json(report.learning_plan, [])
            interview_strategy = parse_json(report.interview_strategy, [])
            evidence_refs = parse_json(report.evidence_refs, [])
            if report_has_detail or gaps or strengths or evidence_refs:
                complete_report_count += 1

            cards.append({
                "id": report.id,
                "job_name": job_profile.job_name if job_profile else "",
                "score": round(float(report.overall_score or 0), 1),
                "summary": report.fit_summary or "",
                "strengths": strengths[:3],
                "gaps": gaps[:3],
                "learning_plan": learning_plan[:3],
                "interview_strategy": interview_strategy[:3],
                "evidence_refs": evidence_refs[:3],
                "has_detail": bool(report_has_detail or gaps or strengths or evidence_refs),
                "confidence": report.confidence or "",
                "created_at": report.created_at.strftime("%Y-%m-%d %H:%M")
                if report.created_at else "",
            })

    dimensions = [
        {
            "key": field,
            "label": label,
            "score": round(
                sum(dimension_totals[field]) / len(dimension_totals[field]), 1
            ) if dimension_totals[field] else 0,
        }
        for field, label in dimension_fields
    ]

    return {
        "code": 200,
        "level": level_value,
        "total": total,
        "average_score": round(float(avg_score), 1),
        "complete_report_count": complete_report_count,
        "completeness_rate": round(complete_report_count / len(rows) * 100)
        if rows else 0,
        "dimensions": dimensions,
        "reports": cards,
    }
