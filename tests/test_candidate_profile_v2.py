"""v0.35 候选人画像提取质量测试."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_education_school_detection():
    """能正确识别院校"""
    from services.candidate_profile_service import _extract_education_v2
    text = "张三，本科计算机科学与技术专业，2024年毕业于北京大学。"
    result = _extract_education_v2(text)
    assert "北京大学" in result["school"]
    assert result["degree"] == "本科"
    assert result["graduation_year"] == "2024"


def test_education_major_not_misidentify():
    """不把'数学建模'误识别为'数学专业'"""
    from services.candidate_profile_service import _extract_education_v2
    text = "李四，参加数学建模竞赛获奖，本科计算机专业，2023年毕业。"
    result = _extract_education_v2(text)
    assert "数学建模" not in result["major"]
    assert "计算机" in result["major"]


def test_education_explicit_major():
    """能识别明确声明的专业"""
    from services.candidate_profile_service import _extract_education_v2
    text = "王五，专业：软件工程，硕士学历，2025年毕业。"
    result = _extract_education_v2(text)
    assert "软件工程" in result["major"]
    assert result["degree"] == "硕士"


def test_education_graduation_year_formats():
    """能识别不同格式的毕业年份"""
    from services.candidate_profile_service import _extract_education_v2

    # 2026届
    r1 = _extract_education_v2("2026届应届毕业生")
    assert r1["graduation_year"] == "2026"

    # 2026年毕业
    r2 = _extract_education_v2("2026年毕业")
    assert r2["graduation_year"] == "2026"

    # 毕业时间：2026
    r3 = _extract_education_v2("毕业时间：2026")
    assert r3["graduation_year"] == "2026"


def test_education_university_english():
    """能识别英文院校名"""
    from services.candidate_profile_service import _extract_education_v2
    text = "Graduated from Tsinghua University in 2024"
    result = _extract_education_v2(text)
    assert "University" in result["school"]


def test_empty_resume_safe_fallback():
    """空简历安全兜底，不 500"""
    from services.candidate_profile_service import extract_candidate_profile
    result = extract_candidate_profile(resume_text="")
    assert result.confidence == "low"
    assert "简历文本为空或过短" in (result.risk_points[0] if result.risk_points else "")


def test_short_resume_safe_fallback():
    """短简历安全兜底"""
    from services.candidate_profile_service import extract_candidate_profile
    result = extract_candidate_profile(resume_text="张三")
    assert result.confidence == "low"


def test_project_extraction():
    """能识别项目经历"""
    from services.candidate_profile_service import _extract_projects
    text = """项目经历：
1. 基于LangChain的智能问答系统
   使用LangChain + OpenAI API构建RAG问答系统，支持PDF文档上传和多轮对话。
2. 求职分析平台后端开发
   使用FastAPI开发RESTful API，MySQL存储数据，Redis缓存。"""
    projects = _extract_projects(text)
    assert len(projects) >= 2


def test_internship_extraction():
    """能识别实习经历"""
    from services.candidate_profile_service import _extract_internships
    text = """实习经历：
在某科技公司担任后端开发实习生，参与内部AI助手项目的开发。
负责后端API开发和数据库设计。"""
    internships = _extract_internships(text)
    assert len(internships) >= 1


def test_skill_dedup():
    """技能去重规范化"""
    from services.candidate_profile_service import _dedupe_skills
    skills = [
        {"skill": "Python", "confidence": "explicit"},
        {"skill": "python", "confidence": "inferred"},
        {"skill": "FastAPI", "confidence": "explicit"},
        {"skill": "fastapi", "confidence": "inferred"},
    ]
    result = _dedupe_skills(skills)
    assert len(result) == 2
    skill_names = [s["skill"] for s in result]
    assert "Python" in skill_names
    assert "FastAPI" in skill_names


def test_skill_extraction_from_resume():
    """技能栈提取"""
    from services.candidate_profile_service import _extract_skills_from_text
    text = "技能：Python、FastAPI、MySQL、Redis、Docker、LangChain、OpenAI API"
    skills = _extract_skills_from_text(text)
    skill_names = [s["skill"].lower() for s in skills]
    assert "python" in skill_names
    assert "fastapi" in skill_names
    assert "mysql" in skill_names


def test_achievement_extraction():
    """成果证据提取"""
    from services.candidate_profile_service import _extract_achievements
    text = "项目上线后日活用户500+，客户满意度提升20%。"
    achievements = _extract_achievements(text)
    assert len(achievements) >= 1
    assert achievements[0]["has_metric"] is True


def test_learning_signals():
    """学习信号提取"""
    from services.candidate_profile_service import _extract_learning_signals
    text = "自学Python，参加ACM竞赛获得银奖，在GitHub上开源了多个项目。"
    signals = _extract_learning_signals(text)
    assert len(signals) >= 2


def test_full_resume_extraction():
    """完整简历提取"""
    from services.candidate_profile_service import extract_candidate_profile
    resume = """张三，本科计算机科学与技术专业，2024年毕业于浙江大学。
技能：Python、FastAPI、MySQL、Redis、Docker、LangChain。

项目经历：
1. 基于LangChain的智能问答系统
   使用LangChain + OpenAI API构建RAG问答系统，支持PDF文档上传和多轮对话。
   项目上线后日均查询200+次。

2. 求职分析平台后端开发
   使用FastAPI开发RESTful API，MySQL存储数据，Redis缓存。
   Docker容器化部署，日活用户500+。

实习经历：
在某科技公司担任后端开发实习生，参与内部AI助手项目的开发。

获奖：ACM校赛银奖"""

    result = extract_candidate_profile(resume_text=resume)

    # 教育背景
    assert result.education_background.get("degree") == "本科"
    assert "计算机" in (result.education_background.get("major") or "")
    assert result.education_background.get("school") != ""
    assert result.education_background.get("graduation_year") == "2024"

    # 技能栈
    assert len(result.skill_stack) >= 3

    # 项目经历
    assert len(result.projects) >= 2

    # 实习经历
    assert len(result.internships) >= 1

    # 成果证据
    assert len(result.achievements) >= 1

    # 置信度
    assert result.confidence in ("high", "medium")
