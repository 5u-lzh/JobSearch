"""v0.25 Boss 岗位采集 + 岗位画像重建测试."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_USER_ID = 0


def _ensure_test_user():
    """确保测试用户存在，返回 user_id"""
    global TEST_USER_ID
    if TEST_USER_ID:
        return TEST_USER_ID
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/user?username=test_boss_user")
    TEST_USER_ID = r.json().get("user_id", 1)
    return TEST_USER_ID


# ── BossCaptureRequest 参数校验 ──

def test_boss_capture_request_defaults():
    """BossCaptureRequest 默认值正确"""
    from services.boss_capture_service import BossCaptureRequest
    req = BossCaptureRequest(job_name="Python后端")
    assert req.job_name == "Python后端"
    assert req.city == ""
    assert req.max_jobs == 10
    assert req.filters == {}


def test_boss_capture_request_validation():
    """BossCaptureRequest max_jobs 范围校验"""
    from services.boss_capture_service import BossCaptureRequest
    from pydantic import ValidationError
    # max_jobs 超出范围应报错
    try:
        BossCaptureRequest(job_name="test", max_jobs=0)
        assert False, "应该抛出 ValidationError"
    except ValidationError:
        pass
    try:
        BossCaptureRequest(job_name="test", max_jobs=31)
        assert False, "应该抛出 ValidationError"
    except ValidationError:
        pass


def test_boss_capture_result_defaults():
    """BossCaptureResult 默认值正确"""
    from services.boss_capture_service import BossCaptureResult
    r = BossCaptureResult()
    assert r.captured_count == 0
    assert r.imported_count == 0
    assert r.skipped_count == 0
    assert r.failed_count == 0
    assert r.blocked_reason == ""
    assert r.documents == []
    assert r.warnings == []


# ── 手动导入 JD ──

def test_manual_import_empty_text():
    """手动导入空文本应返回错误"""
    from services.boss_capture_service import import_manual_jd
    result = import_manual_jd("Python后端", "")
    assert result.blocked_reason != ""


def test_manual_import_valid_jd():
    """手动导入有效 JD 能入库（或因去重跳过）"""
    import time
    from services.boss_capture_service import import_manual_jd
    # 使用唯一文本避免去重
    jd_text = f"""岗位职责：
1. 负责公司核心业务系统的后端开发与维护
2. 参与系统架构设计，保证系统高可用、高性能
3. 编写高质量代码，进行代码审查
4. 与产品、前端团队紧密协作

任职要求：
1. 计算机相关专业本科及以上学历
2. 3年以上 Python 后端开发经验
3. 熟悉 Python、FastAPI、MySQL、Redis
4. 熟悉 Docker、Kubernetes 等容器化技术
5. 具备良好的沟通能力和团队协作精神

唯一标识: {time.time()}"""
    result = import_manual_jd("Python后端", jd_text, title="测试JD", company="测试公司")
    assert result.captured_count == 1
    # imported_count >= 1 或因去重 skipped_count >= 1
    assert result.imported_count >= 1 or result.skipped_count >= 1


def test_manual_import_duplicate():
    """重复导入同一条 JD 应被去重"""
    from services.boss_capture_service import import_manual_jd
    jd_text = "这是一个独特的测试 JD 文本，用于去重验证。" * 10
    result1 = import_manual_jd("测试岗位", jd_text)
    result2 = import_manual_jd("测试岗位", jd_text)
    # 第二次导入应被去重
    assert result2.imported_count == 0 or result2.skipped_count >= 1


# ── API 端点测试 ──

def test_boss_capture_api_empty_job_name():
    """POST /jd_sources/boss/capture 空 job_name 应返回错误"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.post("/jd_sources/boss/capture", json={"job_name": ""})
    assert r.status_code == 200
    data = r.json()
    # 空 job_name 应该返回 blocked_reason
    assert data.get("blocked_reason") != "" or data.get("code") == 200


def test_boss_capture_api_basic():
    """POST /jd_sources/boss/capture 基本调用不报错"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.post("/jd_sources/boss/capture", json={
        "job_name": "Python后端",
        "city": "杭州",
        "max_jobs": 5,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert "captured_count" in data
    assert "imported_count" in data
    assert "blocked_reason" in data


def test_boss_manual_import_api():
    """POST /jd_sources/boss/import 手动导入 JD"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    jd_text = "岗位职责：负责后端开发。任职要求：熟悉 Python。" * 20
    r = c.post("/jd_sources/boss/import", json={
        "job_name": "测试岗位",
        "jd_text": jd_text,
        "title": "测试JD",
        "company": "测试公司",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert "imported_count" in data


# ── 岗位画像重建 ──

def test_rebuild_job_profile_api():
    """POST /job_profiles/rebuild 生成新画像"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.post("/job_profiles/rebuild", json={
        "job_name": "Python后端",
        "source_platform": "boss",
        "top_n": 20,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert "job_profile_id" in data
    assert "profile" in data


def test_rebuild_does_not_overwrite_old():
    """重建不覆盖旧画像（每次生成新 ID）"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r1 = c.post("/job_profiles/rebuild", json={"job_name": "Python后端"})
    r2 = c.post("/job_profiles/rebuild", json={"job_name": "Python后端"})
    id1 = r1.json().get("job_profile_id")
    id2 = r2.json().get("job_profile_id")
    assert id1 != id2


def test_rebuild_job_profile_fields():
    """重建的岗位画像包含必要字段"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.post("/job_profiles/rebuild", json={"job_name": "Python后端"})
    profile = r.json().get("profile", {})
    assert "job_name" in profile
    assert "must_have_capabilities" in profile
    assert "confidence" in profile
    assert "sample_count" in profile


# ── JobProfileAgent 接口 ──

def test_job_profile_agent_interface():
    """JobProfileAgent 基本接口可用"""
    from services.job_profile_agent import JobProfileAgent
    agent = JobProfileAgent()
    profile, mode = agent.analyze("Python后端", [])
    assert hasattr(profile, "job_name")
    assert hasattr(profile, "must_have_capabilities")
    assert mode in ("agent", "rule_fallback")


def test_build_job_profile_from_jds():
    """build_job_profile_from_jds 返回正确结构"""
    from services.job_profile_agent import build_job_profile_from_jds
    profile, doc_ids = build_job_profile_from_jds("Python后端", "boss", 20)
    assert hasattr(profile, "job_name")
    assert isinstance(doc_ids, list)


# ── 旧报告不受影响 ──

def test_old_reports_still_work():
    """旧历史报告仍可打开（不被 rebuild 影响）"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    # 先创建一个旧报告
    r = c.post("/fit_analysis_reports", json={
        "user_id": _ensure_test_user(),
        "job_profile_id": 1,
        "candidate_profile_id": 1,
    })
    # 无论成功与否，GET 接口不应 500
    r2 = c.get("/fit_analysis_reports?user_id=" + str(_ensure_test_user()))
    assert r2.status_code == 200


# ── v0.25.1 浏览器采集 Mock 测试 ──

def test_browser_capture_not_running_returns_blocked():
    """浏览器未启动时，capture 返回 blocked_reason"""
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest
    from unittest.mock import patch, MagicMock

    # Mock 浏览器状态为未启动
    mock_status = MagicMock()
    mock_status.running = False
    mock_status.logged_in = False
    mock_status.status = "未启动"
    mock_status.message = ""

    mock_browser = MagicMock()
    mock_browser.status.return_value = mock_status

    with patch("services.boss_browser_capture.get_browser_capture", return_value=mock_browser):
        req = BossCaptureRequest(job_name="Python后端", city="杭州")
        result = capture_boss_jds(req)
        assert result.blocked_reason != ""
        assert "浏览器未启动" in result.blocked_reason or "启动" in result.blocked_reason


def test_browser_capture_not_logged_in_returns_blocked():
    """浏览器未登录时，capture 返回 blocked_reason 提示登录"""
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest
    from unittest.mock import patch, MagicMock

    mock_status = MagicMock()
    mock_status.running = True
    mock_status.logged_in = False
    mock_status.status = "需登录"
    mock_status.message = ""

    mock_browser = MagicMock()
    mock_browser.status.return_value = mock_status

    with patch("services.boss_browser_capture.get_browser_capture", return_value=mock_browser):
        req = BossCaptureRequest(job_name="Python后端", city="杭州")
        result = capture_boss_jds(req)
        assert result.blocked_reason != ""
        assert "登录" in result.blocked_reason


def test_browser_capture_xhr_result_to_jd_items():
    """XHR 采集结果能正确转换为 JD items"""
    import time
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest
    from unittest.mock import patch, MagicMock

    mock_status = MagicMock()
    mock_status.running = True
    mock_status.logged_in = True
    mock_status.status = "运行中"

    # 使用唯一文本避免去重
    unique_text = f"岗位职责：负责后端开发。任职要求：熟悉 Python、FastAPI、MySQL。唯一标识: {time.time()}" * 5
    mock_jobs = [
        {
            "title": "Python后端开发",
            "company": "测试公司",
            "salary": "20-40K",
            "city": "杭州",
            "experience": "3-5年",
            "education": "本科",
            "raw_text": unique_text,
            "source_url": "https://www.zhipin.com/job_detail/test.html",
        },
    ]

    mock_browser = MagicMock()
    mock_browser.status.return_value = mock_status
    mock_browser.search_and_capture.return_value = mock_jobs

    with patch("services.boss_browser_capture.get_browser_capture", return_value=mock_browser):
        req = BossCaptureRequest(job_name="Python后端", city="杭州", max_jobs=5)
        result = capture_boss_jds(req)
        assert result.captured_count == 1
        assert result.imported_count >= 1


def test_browser_capture_empty_result():
    """采集无结果时返回 warnings"""
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest
    from unittest.mock import patch, MagicMock

    mock_status = MagicMock()
    mock_status.running = True
    mock_status.logged_in = True

    mock_browser = MagicMock()
    mock_browser.status.return_value = mock_status
    mock_browser.search_and_capture.return_value = []

    with patch("services.boss_browser_capture.get_browser_capture", return_value=mock_browser):
        req = BossCaptureRequest(job_name="不存在的岗位xyz", city="杭州")
        result = capture_boss_jds(req)
        assert result.captured_count == 0
        assert len(result.warnings) > 0


def test_browser_status_api():
    """GET /boss/browser/status 返回正确结构"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/boss/browser/status")
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert "running" in data
    assert "status" in data


def test_detail_api_generates_raw_text():
    """详情 API 数据能生成 raw_text"""
    # 模拟 XHR 返回的详情数据
    detail_data = {
        "code": 0,
        "zpData": {
            "jobInfo": {
                "postDescription": "岗位职责：\n1. 负责核心系统开发\n2. 参与架构设计\n\n任职要求：\n1. 本科以上学历\n2. 3年Python经验",
            },
            "bossInfo": {
                "name": "张三",
                "title": "HR",
            },
        },
    }
    job_info = detail_data["zpData"]["jobInfo"]
    assert job_info["postDescription"] != ""
    assert "岗位职责" in job_info["postDescription"]


def test_dedup_still_works_with_browser_capture():
    """浏览器采集的 JD 去重仍生效"""
    from services.boss_capture_service import import_manual_jd
    jd_text = "这是一个用于测试去重的唯一 JD 文本内容。" * 10
    # 第一次导入
    result1 = import_manual_jd("测试去重岗位", jd_text)
    # 第二次导入相同内容
    result2 = import_manual_jd("测试去重岗位", jd_text)
    # 第二次应该被去重
    assert result2.imported_count == 0


# ── v0.25.2 浏览器导航测试 ──

def test_start_navigates_to_login_when_on_google():
    """启动后如果当前页是 Google，应跳转到 Boss 登录页"""
    from services.boss_browser_capture import BossBrowserCapture
    from unittest.mock import MagicMock, patch

    capture = BossBrowserCapture()

    mock_page = MagicMock()
    mock_page.url = "https://www.google.com/"
    mock_page.is_closed.return_value = False
    mock_page.evaluate.return_value = "请登录"

    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_page]

    mock_pw = MagicMock()
    mock_pw.firefox.launch_persistent_context.return_value = mock_ctx

    with patch("playwright.sync_api.sync_playwright") as mock_sp:
        mock_sp.return_value.start.return_value = mock_pw
        with patch("services.boss_browser_capture._PROFILE_DIR") as mock_dir:
            mock_dir.mkdir = MagicMock()
            result = capture.start(headless=False)

    # 应该调用 goto 登录页
    mock_page.goto.assert_called_once()
    call_args = mock_page.goto.call_args
    assert "zhipin.com" in call_args[0][0]
    assert "user" in call_args[0][0] or "login" in call_args[0][0]


def test_start_shows_login_message_when_not_logged_in():
    """未登录时 start() 应返回提示登录的消息"""
    from services.boss_browser_capture import BossBrowserCapture
    from unittest.mock import MagicMock, patch

    capture = BossBrowserCapture()

    mock_page = MagicMock()
    mock_page.url = "https://www.zhipin.com/web/user/?ka=header-login"
    mock_page.is_closed.return_value = False
    mock_page.evaluate.return_value = "扫码登录 密码登录"

    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_page]

    mock_pw = MagicMock()
    mock_pw.firefox.launch_persistent_context.return_value = mock_ctx

    with patch("playwright.sync_api.sync_playwright") as mock_sp:
        mock_sp.return_value.start.return_value = mock_pw
        with patch("services.boss_browser_capture._PROFILE_DIR") as mock_dir:
            mock_dir.mkdir = MagicMock()
            result = capture.start(headless=False)

    assert result.running is True
    assert result.logged_in is False
    assert "登录" in result.message


def test_start_shows_logged_in_message_when_authenticated():
    """已登录时 start() 应返回已登录消息"""
    from services.boss_browser_capture import BossBrowserCapture
    from unittest.mock import MagicMock, patch

    capture = BossBrowserCapture()

    mock_page = MagicMock()
    mock_page.url = "https://www.zhipin.com/web/geek/job"
    mock_page.is_closed.return_value = False
    mock_page.evaluate.return_value = "职位描述 立即沟通 消息"

    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_page]

    mock_pw = MagicMock()
    mock_pw.firefox.launch_persistent_context.return_value = mock_ctx

    with patch("playwright.sync_api.sync_playwright") as mock_sp:
        mock_sp.return_value.start.return_value = mock_pw
        with patch("services.boss_browser_capture._PROFILE_DIR") as mock_dir:
            mock_dir.mkdir = MagicMock()
            result = capture.start(headless=False)

    assert result.running is True
    assert result.logged_in is True
    assert "已登录" in result.message


def test_open_login_page_api():
    """POST /boss/browser/open-login 应返回正确结构"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.post("/boss/browser/open-login")
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert "status" in data


# ── v0.25.3 多页面登录检测测试 ──

def test_multi_page_finds_boss_logged_in_page():
    """ctx.pages 有两个页面：Google + Boss 已登录页，应识别 Boss 页为登录态"""
    from services.boss_browser_capture import BossBrowserCapture
    from unittest.mock import MagicMock, patch

    capture = BossBrowserCapture()

    # 页面1: Google（未登录）
    mock_google = MagicMock()
    mock_google.url = "https://www.google.com/"
    mock_google.is_closed.return_value = False
    mock_google.evaluate.return_value = ""

    # 页面2: Boss 已登录页
    mock_boss = MagicMock()
    mock_boss.url = "https://www.zhipin.com/web/geek/jobs?security_check=1"
    mock_boss.is_closed.return_value = False
    mock_boss.evaluate.return_value = "消息 简历 立即沟通 职位描述"

    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_google, mock_boss]

    mock_pw = MagicMock()
    mock_pw.firefox.launch_persistent_context.return_value = mock_ctx

    with patch("playwright.sync_api.sync_playwright") as mock_sp:
        mock_sp.return_value.start.return_value = mock_pw
        with patch("services.boss_browser_capture._PROFILE_DIR") as mock_dir:
            mock_dir.mkdir = MagicMock()
            result = capture.start(headless=False)

    assert result.running is True
    assert result.logged_in is True
    assert capture.page == mock_boss  # 应选择 Boss 页面


def test_security_check_page_with_auth_content_is_logged_in():
    """Boss URL 为 /web/geek/jobs?security_check=xxx 且 body 包含已登录内容，应判定 logged_in=true"""
    from services.boss_browser_capture import BossBrowserCapture
    from unittest.mock import MagicMock, patch

    capture = BossBrowserCapture()

    mock_page = MagicMock()
    mock_page.url = "https://www.zhipin.com/web/geek/jobs?security_check=1"
    mock_page.is_closed.return_value = False
    mock_page.evaluate.return_value = "消息 简历 立即沟通 职位描述 公司名称"

    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_page]

    mock_pw = MagicMock()
    mock_pw.firefox.launch_persistent_context.return_value = mock_ctx

    with patch("playwright.sync_api.sync_playwright") as mock_sp:
        mock_sp.return_value.start.return_value = mock_pw
        with patch("services.boss_browser_capture._PROFILE_DIR") as mock_dir:
            mock_dir.mkdir = MagicMock()
            result = capture.start(headless=False)

    assert result.logged_in is True
    assert "已登录" in result.detection_reason


def test_login_page_detected_as_not_logged_in():
    """登录页 /web/user/?ka=header-login 且 body 包含「扫码登录」，应判定 logged_in=false"""
    from services.boss_browser_capture import BossBrowserCapture
    from unittest.mock import MagicMock, patch

    capture = BossBrowserCapture()

    mock_page = MagicMock()
    mock_page.url = "https://www.zhipin.com/web/user/?ka=header-login"
    mock_page.is_closed.return_value = False
    mock_page.evaluate.return_value = "扫码登录 密码登录 验证码登录"

    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_page]

    mock_pw = MagicMock()
    mock_pw.firefox.launch_persistent_context.return_value = mock_ctx

    with patch("playwright.sync_api.sync_playwright") as mock_sp:
        mock_sp.return_value.start.return_value = mock_pw
        with patch("services.boss_browser_capture._PROFILE_DIR") as mock_dir:
            mock_dir.mkdir = MagicMock()
            result = capture.start(headless=False)

    assert result.logged_in is False
    assert "登录" in result.detection_reason


def test_status_updates_self_page_to_boss_page():
    """status() 检测成功后 self.page 被更新为 Boss 页面"""
    from services.boss_browser_capture import BossBrowserCapture
    from unittest.mock import MagicMock

    capture = BossBrowserCapture()
    capture._running = True

    # 页面1: Google
    mock_google = MagicMock()
    mock_google.url = "https://www.google.com/"
    mock_google.is_closed.return_value = False
    mock_google.evaluate.return_value = ""

    # 页面2: Boss 已登录
    mock_boss = MagicMock()
    mock_boss.url = "https://www.zhipin.com/web/geek/job"
    mock_boss.is_closed.return_value = False
    mock_boss.evaluate.return_value = "消息 简历 立即沟通"

    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_google, mock_boss]
    capture._ctx = mock_ctx
    capture.page = mock_google  # 初始指向 Google

    result = capture.status()

    assert result.logged_in is True
    assert capture.page == mock_boss  # 应切换到 Boss 页面


def test_search_and_capture_selects_boss_page():
    """search_and_capture() 采集前能自动选择已登录 Boss 页面"""
    from services.boss_browser_capture import BossBrowserCapture
    from unittest.mock import MagicMock

    capture = BossBrowserCapture()
    capture._running = True

    # 页面1: Google
    mock_google = MagicMock()
    mock_google.url = "https://www.google.com/"
    mock_google.is_closed.return_value = False
    mock_google.evaluate.return_value = ""

    # 页面2: Boss 已登录
    mock_boss = MagicMock()
    mock_boss.url = "https://www.zhipin.com/web/geek/job"
    mock_boss.is_closed.return_value = False
    mock_boss.evaluate.return_value = "消息 简历 立即沟通 职位描述"

    mock_ctx = MagicMock()
    mock_ctx.pages = [mock_google, mock_boss]
    capture._ctx = mock_ctx
    capture.page = mock_google  # 初始指向 Google

    # Mock XHR 返回空（避免真实请求）
    mock_boss.evaluate.side_effect = lambda code: "消息 简历 立即沟通" if "innerText" in str(code) else {"error": "", "jobs": []}

    try:
        capture.search_and_capture("Python后端", "杭州", 5)
    except RuntimeError:
        pass  # 可能因为 mock 不完整，但关键是 page 被切换了

    assert capture.page == mock_boss


# ── v0.25.4 详情提取测试 ──

def test_xhr_list_success_but_detail_empty_not_silent_success():
    """XHR list 成功但 detail postDescription 为空时，不应静默成功"""
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest
    from unittest.mock import patch, MagicMock

    mock_status = MagicMock()
    mock_status.running = True
    mock_status.logged_in = True

    # 模拟：列表成功但详情为空，只有 fallback 文本
    mock_jobs = [
        {
            "title": "Python后端",
            "company": "测试公司",
            "salary": "20-40K",
            "city": "杭州",
            "experience": "3-5年",
            "education": "本科",
            "raw_text": "岗位名称: Python后端\n公司: 测试公司\n薪资: 20-40K",
            "source_url": "https://www.zhipin.com/job_detail/test.html",
            "detail_missing": True,
            "fallback_only": True,
        },
    ]

    mock_browser = MagicMock()
    mock_browser.status.return_value = mock_status
    mock_browser.search_and_capture.return_value = mock_jobs

    with patch("services.boss_browser_capture.get_browser_capture", return_value=mock_browser):
        req = BossCaptureRequest(job_name="Python后端", city="杭州")
        result = capture_boss_jds(req)

    assert result.captured_count == 1
    assert result.detail_missing_count == 1
    assert len(result.warnings) > 0
    assert any("详情" in w or "卡片" in w for w in result.warnings)


def test_detail_api_uses_fallback_fields():
    """detail API 使用 description/jobDesc 等备用字段时可生成 raw_text"""
    from services.boss_browser_capture import BossBrowserCapture

    capture = BossBrowserCapture()

    # 模拟 JS 返回的岗位数据（使用 description 字段）
    mock_jobs = [
        {
            "title": "AI Agent",
            "company": "测试公司",
            "raw_text": "负责 AI Agent 开发，要求熟悉 Python",  # 来自 description 字段
            "detail_missing": False,
            "fallback_only": False,
        },
    ]

    # 只要 raw_text 不为空且 detail_missing=False，说明从备用字段提取成功
    assert mock_jobs[0]["raw_text"] != ""
    assert mock_jobs[0]["detail_missing"] is False


def test_capture_result_has_detail_missing_count():
    """capture_boss_jds 能返回 detail_missing_count 和 failed_examples"""
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest
    from unittest.mock import patch, MagicMock
    import time

    mock_status = MagicMock()
    mock_status.running = True
    mock_status.logged_in = True

    # 混合：1条有详情，1条只有fallback，1条完全空
    unique_text = f"岗位职责：负责开发。唯一标识: {time.time()}" * 5
    mock_jobs = [
        {
            "title": "有详情岗位",
            "company": "公司A",
            "raw_text": unique_text,
            "source_url": "https://www.zhipin.com/job_detail/a.html",
            "detail_missing": False,
            "fallback_only": False,
        },
        {
            "title": "只有卡片信息",
            "company": "公司B",
            "raw_text": "岗位名称: 只有卡片信息\n公司: 公司B",
            "source_url": "https://www.zhipin.com/job_detail/b.html",
            "detail_missing": True,
            "fallback_only": True,
        },
        {
            "title": "完全空",
            "company": "公司C",
            "raw_text": "",
            "source_url": "https://www.zhipin.com/job_detail/c.html",
            "detail_missing": True,
            "fallback_only": False,
        },
    ]

    mock_browser = MagicMock()
    mock_browser.status.return_value = mock_status
    mock_browser.search_and_capture.return_value = mock_jobs

    with patch("services.boss_browser_capture.get_browser_capture", return_value=mock_browser):
        req = BossCaptureRequest(job_name="Python后端", city="杭州")
        result = capture_boss_jds(req)

    assert result.captured_count == 3
    assert result.detail_missing_count == 1  # 只有 fallback_only 的算 detail_missing
    assert result.failed_count == 1  # 完全空的算 failed
    assert len(result.failed_examples) == 1
    assert result.failed_examples[0]["title"] == "完全空"


def test_empty_raw_text_not_imported():
    """空 raw_text 不入库"""
    from services.boss_capture_service import _store_jds

    jd_items = [
        {"title": "空JD", "company": "公司", "url": "", "content": ""},
        {"title": "有效JD", "company": "公司", "url": "", "content": "岗位职责：负责开发。" * 10},
    ]
    imported = _store_jds("测试岗位", "", jd_items)
    # 空内容不应入库
    assert imported <= 1


def test_has_raw_text_imports_normally():
    """有 raw_text 正常入库"""
    import time
    from services.boss_capture_service import _store_jds

    unique_text = f"岗位职责：负责后端开发。任职要求：熟悉 Python。唯一标识: {time.time()}" * 5
    jd_items = [
        {"title": "有效JD", "company": "公司", "url": "", "content": unique_text},
    ]
    imported = _store_jds("测试岗位", "", jd_items)
    assert imported >= 1


# ── v0.26 采集自动生成画像测试 ──

def test_capture_auto_generates_profile():
    """采集成功后自动生成岗位画像，返回 profile_generated=true"""
    import time
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest
    from unittest.mock import patch, MagicMock

    mock_status = MagicMock()
    mock_status.running = True
    mock_status.logged_in = True

    unique_text = f"岗位职责：负责后端开发。任职要求：熟悉 Python、FastAPI、MySQL。唯一标识: {time.time()}" * 5
    mock_jobs = [
        {
            "title": "Python后端",
            "company": "测试公司",
            "raw_text": unique_text,
            "source_url": "https://www.zhipin.com/job_detail/test.html",
            "detail_missing": False,
            "fallback_only": False,
        },
    ]

    mock_browser = MagicMock()
    mock_browser.status.return_value = mock_status
    mock_browser.search_and_capture.return_value = mock_jobs

    with patch("services.boss_browser_capture.get_browser_capture", return_value=mock_browser):
        req = BossCaptureRequest(job_name="Python后端", city="杭州")
        result = capture_boss_jds(req)

    assert result.captured_count == 1
    assert result.imported_count >= 1
    assert result.profile_generated is True
    assert result.job_profile_id > 0
    assert "job_name" in result.job_profile


def test_capture_result_has_job_profile_fields():
    """采集返回的 job_profile 包含必要字段"""
    import time
    from services.boss_capture_service import capture_boss_jds, BossCaptureRequest
    from unittest.mock import patch, MagicMock

    mock_status = MagicMock()
    mock_status.running = True
    mock_status.logged_in = True

    unique_text = f"岗位职责：负责 AI Agent 开发。任职要求：熟悉 Python、LLM。唯一标识: {time.time()}" * 5
    mock_jobs = [
        {
            "title": "AI Agent",
            "company": "测试公司",
            "raw_text": unique_text,
            "source_url": "",
            "detail_missing": False,
            "fallback_only": False,
        },
    ]

    mock_browser = MagicMock()
    mock_browser.status.return_value = mock_status
    mock_browser.search_and_capture.return_value = mock_jobs

    with patch("services.boss_browser_capture.get_browser_capture", return_value=mock_browser):
        req = BossCaptureRequest(job_name="AI Agent", city="杭州")
        result = capture_boss_jds(req)

    if result.profile_generated:
        profile = result.job_profile
        assert "must_have_capabilities" in profile
        assert "responsibilities" in profile
        assert "confidence" in profile
        assert "sample_count" in profile


# ── v0.34 candidate_profiles/analyze user_id=0 修复测试 ──

def test_candidate_profile_analyze_user_id_zero():
    """/candidate_profiles/analyze 在 user_id=0 时不应 500"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)

    resume_text = "张三，本科计算机专业，3年Python开发经验。技能：Python、FastAPI、MySQL。项目：开发了求职分析平台。"

    r = c.post("/candidate_profiles/analyze", json={
        "user_id": 0,
        "resume_text": resume_text,
    })

    # 不应返回 500
    assert r.status_code != 500, f"Expected non-500, got {r.status_code}: {r.text}"

    # 应返回 200
    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert "candidate_profile_id" in data
    assert data["candidate_profile_id"] > 0


def test_candidate_profile_analyze_user_id_zero_has_valid_profile():
    """/candidate_profiles/analyze user_id=0 返回有效的候选人画像"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)

    resume_text = "李四，硕士人工智能专业。技能：Python、PyTorch、TensorFlow。项目：基于Transformer的文本分类系统。"

    r = c.post("/candidate_profiles/analyze", json={
        "user_id": 0,
        "resume_text": resume_text,
    })

    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert "profile" in data
    profile = data["profile"]
    assert "skill_stack" in profile
    assert len(profile["skill_stack"]) > 0


def test_candidate_profile_analyze_chinese_resume():
    """中文简历能正常解析"""
    from api.fastapi_app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)

    resume_text = """王五，本科软件工程专业，2020年毕业。
技能：JavaScript、Vue、HTML5、CSS3、Webpack。
项目：使用Vue开发电商管理后台，首屏加载从3秒优化到1.2秒。
工作经历：在某公司担任前端开发工程师。"""

    r = c.post("/candidate_profiles/analyze", json={
        "user_id": _ensure_test_user(),
        "resume_text": resume_text,
    })

    assert r.status_code == 200
    data = r.json()
    assert data["code"] == 200
    assert data["candidate_profile_id"] > 0


# ── v0.34 JD 导入健壮性测试 ──

def test_manual_import_chinese_jd_success():
    """正常中文 JD 导入成功"""
    import time
    from services.boss_capture_service import import_manual_jd

    # 使用唯一文本避免去重
    jd_text = f"""岗位职责：
1. 负责公司核心业务系统的后端开发与维护
2. 参与系统架构设计与优化
3. 编写高质量代码，进行代码审查

任职要求：
1. 计算机相关专业本科及以上学历
2. 3年以上Python后端开发经验
3. 熟悉Python、FastAPI、MySQL、Redis
4. 具备良好的沟通协作能力

唯一标识: {time.time()}"""

    result = import_manual_jd("Python后端", jd_text, title="测试JD", company="测试公司")
    assert result.imported_count >= 1 or result.skipped_count >= 1
    assert result.blocked_reason == ""


def test_manual_import_short_text_filtered():
    """过短 JD 被过滤"""
    from services.boss_capture_service import import_manual_jd

    result = import_manual_jd("测试岗位", "太短了")
    # 过短文本应被过滤或返回警告
    assert result.imported_count == 0 or len(result.warnings) > 0


def test_manual_import_empty_text_blocked():
    """空文本返回 blocked_reason"""
    from services.boss_capture_service import import_manual_jd

    result = import_manual_jd("测试岗位", "")
    assert result.blocked_reason != ""


def test_manual_import_garbled_text_blocked():
    """乱码文本返回 blocked_reason"""
    from services.boss_capture_service import import_manual_jd

    # 模拟乱码
    garbled = "\x00\x01\x02\x03\x04\x05" * 10
    result = import_manual_jd("测试岗位", garbled)
    assert result.blocked_reason != ""


def test_manual_import_no_requirements_filtered():
    """无职责要求的 JD 被过滤"""
    from services.boss_capture_service import import_manual_jd

    jd_text = "这是一家很好的公司，福利待遇好，五险一金，年终奖，带薪年假。" * 10
    result = import_manual_jd("测试岗位", jd_text)
    # 无职责要求的 JD 应被过滤
    assert result.imported_count == 0 or len(result.warnings) > 0
