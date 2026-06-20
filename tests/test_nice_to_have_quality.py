"""v0.36 加分能力质量治理测试."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_nice_to_have_removes_noise():
    """删除招聘话术和人格标签"""
    from services.job_profile_service import _clean_nice_to_have
    raw = ["优先加分", "无相关经验也可", "重点看个人价值观", "踏实靠谱", "Python"]
    result = _clean_nice_to_have(raw, set())
    assert "优先加分" not in result
    assert "无相关经验也可" not in result
    assert "重点看个人价值观" not in result
    assert "踏实靠谱" not in result
    assert "Python" in result


def test_nice_to_have_merges_b2b():
    """B2B + 商务销售 → B2B商务拓展"""
    from services.job_profile_service import _clean_nice_to_have
    raw = ["B2B", "商务销售", "市场营销"]
    result = _clean_nice_to_have(raw, set())
    # 应该合并为 B2B商务拓展
    assert any("B2B" in s for s in result)
    assert "商务销售" not in result


def test_nice_to_have_removes_duplicates_with_must():
    """与 must_have 重复时只保留在 must_have"""
    from services.job_profile_service import _clean_nice_to_have
    raw = ["Python", "FastAPI", "Docker"]
    must_set = {"python", "fastapi"}
    result = _clean_nice_to_have(raw, must_set)
    assert "Python" not in result
    assert "FastAPI" not in result
    assert "Docker" in result


def test_nice_to_have_max_5_items():
    """最多保留 5 项"""
    from services.job_profile_service import _clean_nice_to_have
    raw = ["A", "B", "C", "D", "E", "F", "G", "H"]
    result = _clean_nice_to_have(raw, set())
    assert len(result) <= 5


def test_nice_to_have_normalizes_suffix():
    """'企业服务类经验者' → '企业服务经验'"""
    from services.job_profile_service import _clean_nice_to_have
    raw = ["企业服务类经验者"]
    result = _clean_nice_to_have(raw, set())
    assert len(result) == 1
    assert "类经验者" not in result[0]
    assert "经验" in result[0]


def test_nice_to_have_removes_priority_suffix():
    """删除 '优先' 后缀"""
    from services.job_profile_service import _clean_nice_to_have
    raw = ["机器学习优先", "数据分析优先"]
    result = _clean_nice_to_have(raw, set())
    for s in result:
        assert not s.endswith("优先")


def test_nice_to_have_empty_when_only_noise():
    """全是噪声时返回空"""
    from services.job_profile_service import _clean_nice_to_have
    raw = ["优先加分", "无相关经验也可", "踏实靠谱"]
    result = _clean_nice_to_have(raw, set())
    assert len(result) == 0


def test_nice_to_have_preserves_domain_experience():
    """保留领域经验"""
    from services.job_profile_service import _clean_nice_to_have
    raw = ["B2B商务拓展", "企业服务经验", "AI产品运营"]
    result = _clean_nice_to_have(raw, set())
    assert len(result) >= 2
    assert any("B2B" in s for s in result)
    assert any("企业服务" in s for s in result)


def test_nice_to_have_full_pipeline():
    """完整测试：输入噪声+技能，输出干净列表"""
    from services.job_profile_service import _clean_nice_to_have
    raw = [
        "B2B", "商务销售", "企业服务类经验者", "优先加分",
        "无相关经验也可", "重点看个人价值观", "踏实靠谱", "市场营销",
    ]
    result = _clean_nice_to_have(raw, set())

    # 应该包含 B2B 相关
    assert any("B2B" in s for s in result), f"Expected B2B in {result}"
    # 可以包含企业服务经验
    # 不包含噪声
    assert "优先加分" not in result
    assert "无相关经验也可" not in result
    assert "踏实靠谱" not in result
    assert "重点看个人价值观" not in result
    # 不超过 5 项
    assert len(result) <= 5


def test_nice_to_have_from_jd_extraction():
    """从 JD 文本提取加分能力"""
    from services.job_profile_service import _extract_nice_to_have_skills
    jd = """任职要求：
1. 本科及以上学历
2. 熟悉Python、FastAPI

加分项：
1. 有大模型应用经验优先
2. 了解RAG、Agent等概念
3. 有企业服务类经验者优先"""
    result = _extract_nice_to_have_skills([jd], must_have=["Python", "FastAPI"])
    # 应该包含技术相关加分项
    assert len(result) >= 1
    # 不包含噪声
    assert not any("优先加分" in s for s in result)


def test_is_soft_skill_catches_personality():
    """_is_soft_skill 能识别人格标签"""
    from services.job_profile_service import _is_soft_skill
    assert _is_soft_skill("踏实靠谱") is True
    assert _is_soft_skill("认真负责") is True
    assert _is_soft_skill("自驱力") is True
    assert _is_soft_skill("Python") is False
    assert _is_soft_skill("RAG") is False
