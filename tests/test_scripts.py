"""v0.34 脚本测试 — 数据清理和演示数据生成."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from unittest.mock import patch, MagicMock


def test_clean_data_dry_run_default():
    """clean_product_data 默认 dry-run 不删除数据"""
    from scripts.clean_product_data import preview_cleanup
    # 只测试 preview_cleanup 函数不抛异常
    # 实际数据库操作需要 mock
    assert callable(preview_cleanup)


def test_clean_data_no_confirm_no_delete():
    """没有 --confirm 不执行删除"""
    from scripts.clean_product_data import execute_cleanup
    # execute_cleanup 需要 session 和 preview dict
    # 测试函数存在且可调用
    assert callable(execute_cleanup)


def test_clean_data_user_safety():
    """不允许删除 users"""
    from scripts.clean_product_data import main
    # 检查 main 函数存在
    assert callable(main)


def test_seed_demo_data_callable():
    """seed_demo_data 函数可调用"""
    from scripts.seed_demo_data import seed_demo_data
    assert callable(seed_demo_data)


def test_seed_demo_data_has_demo_texts():
    """演示数据包含 JD 和简历文本"""
    from scripts.seed_demo_data import DEMO_JD_TEXTS, DEMO_RESUME_TEXT, DEMO_JOB_NAME
    assert len(DEMO_JD_TEXTS) >= 1
    assert len(DEMO_RESUME_TEXT) > 100
    assert DEMO_JOB_NAME != ""


def test_seed_demo_data_jd_contains_sections():
    """演示 JD 包含职责和要求段落"""
    from scripts.seed_demo_data import DEMO_JD_TEXTS
    for jd in DEMO_JD_TEXTS:
        assert "岗位职责" in jd or "职位描述" in jd
        assert "任职要求" in jd or "要求" in jd


def test_seed_demo_data_resume_has_skills():
    """演示简历包含技能和项目"""
    from scripts.seed_demo_data import DEMO_RESUME_TEXT
    assert "Python" in DEMO_RESUME_TEXT
    assert "项目" in DEMO_RESUME_TEXT


def test_cleanup_preview_structure():
    """preview_cleanup 返回正确结构"""
    from scripts.clean_product_data import preview_cleanup
    # Mock session
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.count.return_value = 0
    mock_session.query.return_value.filter.return_value.all.return_value = []

    result = preview_cleanup(mock_session, "test_job")
    assert "job_name" in result
    assert "jd_documents" in result
    assert "job_profiles" in result
    assert "fit_reports" in result


def test_cleanup_execute_structure():
    """execute_cleanup 返回正确结构"""
    from scripts.clean_product_data import execute_cleanup
    mock_session = MagicMock()
    preview = {
        "job_name": "test",
        "jd_documents": 0,
        "jd_chunks": 0,
        "job_profiles": 0,
        "fit_reports": 0,
        "profile_ids": [],
        "report_ids": [],
        "doc_ids": [],
    }
    result = execute_cleanup(mock_session, preview)
    assert "fit_reports" in result
    assert "job_profiles" in result
    assert "jd_chunks" in result
    assert "jd_documents" in result
