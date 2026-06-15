"""Boss 直聘 Playwright 浏览器采集服务 — v0.25.3

使用 Playwright 持久化浏览器上下文采集 Boss 直聘 JD。
用户可在浏览器中手动登录/验证，系统在该上下文中采集。

参考: D:\boss投递\boss_firefox.py (BossScraper)
"""
from __future__ import annotations
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from core.logger import get_logger

logger = get_logger(__name__)

# 浏览器 profile 目录（项目根目录下 .boss_profile）
_PROFILE_DIR = Path(__file__).resolve().parent.parent / ".boss_profile" / "jobsearch_browser"
_STATE_FILE = Path(__file__).resolve().parent.parent / ".boss_profile" / "browser_state.json"

# Boss 直聘 URL
BOSS_LOGIN_URL = "https://www.zhipin.com/web/user/?ka=header-login"
BOSS_SEARCH_URL = "https://www.zhipin.com/web/geek/job"
_BOSS_JOB_LIST_API = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"
_BOSS_JOB_DETAIL_API = "https://www.zhipin.com/wapi/zpgeek/job/detail.json"

# 城市代码映射
_CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101270100",
    "南京": "101190100", "武汉": "101200100", "西安": "101110100",
    "苏州": "101190400", "长沙": "101250100", "重庆": "101040100",
    "天津": "101030100", "厦门": "101230200", "青岛": "101120200",
    "郑州": "101180100", "合肥": "101220100", "宁波": "101210400",
    "东莞": "101281600", "佛山": "101280800", "珠海": "101280700",
}

# 反检测脚本（参考 boss_firefox.py ANTI_DETECT）
_ANTI_DETECT_SCRIPT = """
// 移除 webdriver 标记
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// 语言
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
// 时区
const DateTimeFormat = Intl.DateTimeFormat;
Intl.DateTimeFormat = function(...args) {
    if (args[1]) args[1].timeZone = 'Asia/Shanghai';
    else args[1] = {timeZone: 'Asia/Shanghai'};
    return new DateTimeFormat(...args);
};
"""

# 已登录正向信号（页面文本）
_AUTH_TEXT_SIGNALS = [
    "消息", "简历", "立即沟通", "职位描述", "职位职责",
    "岗位职责", "任职要求", "收藏", "添加求职期望",
    "聊天", "公司名称", "相似职位", "在线",
]

# 未登录负向信号（页面文本）
_LOGIN_TEXT_SIGNALS = [
    "扫码登录", "密码登录", "验证码登录", "登录BOSS直聘", "请登录",
    "微信扫码", "手机号登录",
]

# 登录态 Cookie 辅助信号
_AUTH_COOKIE_NAMES = ["wt2", "zp_token", "sid", "lastCity"]


class BrowserStatus(BaseModel):
    """浏览器状态"""
    running: bool = False
    logged_in: bool = False
    status: str = "未启动"  # 未启动 / 运行中 / 需登录 / 风控
    message: str = ""
    current_url: str = ""
    page_count: int = 0
    detection_reason: str = ""


class BossBrowserCapture:
    """Boss 直聘 Playwright 浏览器采集器。

    使用持久化浏览器上下文，用户可手动登录/验证。
    优先 Firefox，回退 Chromium。
    """

    def __init__(self):
        self._pw = None
        self._ctx = None
        self.page = None
        self._running = False
        self._logged_in = False
        self._status_msg = "未启动"

    def start(self, headless: bool = False) -> BrowserStatus:
        """启动浏览器。

        Args:
            headless: 是否无头模式（默认 False，让用户看到浏览器）

        Returns:
            BrowserStatus
        """
        if self._running and self.page is not None:
            return BrowserStatus(
                running=True, logged_in=self._logged_in,
                status="运行中", message="浏览器已在运行",
                current_url=self.page.url if self.page else "",
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return BrowserStatus(
                running=False, logged_in=False,
                status="未启动",
                message="未安装 playwright，请运行: pip install playwright && playwright install firefox"
            )

        try:
            self._pw = sync_playwright().start()
            _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

            kw = {
                "headless": headless,
                "viewport": {"width": 1280, "height": 800},
                "locale": "zh-CN",
            }

            # 优先 Firefox，回退 Chromium
            try:
                self._ctx = self._pw.firefox.launch_persistent_context(
                    str(_PROFILE_DIR), **kw
                )
                logger.info("Firefox 浏览器已启动")
            except Exception as e:
                logger.warning(f"Firefox 启动失败，尝试 Chromium: {e}")
                try:
                    self._ctx = self._pw.chromium.launch_persistent_context(
                        str(_PROFILE_DIR), **kw
                    )
                    logger.info("Chromium 浏览器已启动")
                except Exception as e2:
                    logger.error(f"Chromium 也启动失败: {e2}")
                    self._pw.stop()
                    self._pw = None
                    return BrowserStatus(
                        running=False, logged_in=False,
                        status="未启动",
                        message=f"浏览器启动失败: {e2}。请运行: playwright install firefox"
                    )

            # 注入反检测脚本
            try:
                self._ctx.add_init_script(_ANTI_DETECT_SCRIPT)
            except Exception:
                pass

            # 获取或创建页面
            if self._ctx.pages:
                self.page = self._ctx.pages[0]
            else:
                self.page = self._ctx.new_page()

            self._running = True
            self._status_msg = "运行中"

            # 主动跳转到 Boss 登录页（参考 boss_firefox.py 启动后 goto 登录页）
            self._navigate_to_boss()

            # 扫描所有页面，选择最佳 Boss 页面
            best_page, logged_in, reason = self._select_best_boss_page()
            if best_page:
                self.page = best_page
            self._logged_in = logged_in

            page_count = len(self._ctx.pages) if self._ctx else 0

            if logged_in:
                return BrowserStatus(
                    running=True, logged_in=True,
                    status="运行中",
                    message="Boss 浏览器已登录",
                    current_url=self.page.url if self.page else "",
                    page_count=page_count,
                    detection_reason=reason,
                )
            else:
                return BrowserStatus(
                    running=True, logged_in=False,
                    status="需登录",
                    message="已打开 Boss 登录页，请扫码/验证登录后重试采集",
                    current_url=self.page.url if self.page else "",
                    page_count=page_count,
                    detection_reason=reason,
                )

        except Exception as e:
            logger.error(f"浏览器启动异常: {e}")
            self.stop()
            return BrowserStatus(
                running=False, logged_in=False,
                status="未启动",
                message=f"启动失败: {str(e)}"
            )

    def stop(self) -> BrowserStatus:
        """停止浏览器"""
        try:
            if self._ctx:
                # 保存状态
                try:
                    state = self._ctx.storage_state()
                    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(_STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(state, f, ensure_ascii=False)
                except Exception:
                    pass
                try:
                    self._ctx.close()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"关闭浏览器异常: {e}")
        finally:
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass
            self._pw = None
            self._ctx = None
            self.page = None
            self._running = False
            self._logged_in = False
            self._status_msg = "已停止"

        return BrowserStatus(
            running=False, logged_in=False,
            status="已停止", message="浏览器已关闭"
        )

    def status(self) -> BrowserStatus:
        """获取当前浏览器状态（扫描所有页面）"""
        if not self._running or self._ctx is None:
            return BrowserStatus(
                running=False, logged_in=False,
                status="未启动", message=self._status_msg
            )

        # 扫描所有页面，选择最佳 Boss 页面
        best_page, logged_in, reason = self._select_best_boss_page()
        if best_page:
            self.page = best_page
        self._logged_in = logged_in

        page_count = len(self._ctx.pages) if self._ctx else 0

        return BrowserStatus(
            running=True,
            logged_in=logged_in,
            status="需登录" if not logged_in else "运行中",
            message="请在浏览器中完成登录" if not logged_in else "Boss 浏览器已登录",
            current_url=self.page.url if self.page else "",
            page_count=page_count,
            detection_reason=reason,
        )

    # ── 多页面检测核心 ──

    def _iter_open_pages(self):
        """遍历所有未关闭的页面"""
        if not self._ctx:
            return
        for p in self._ctx.pages:
            try:
                if not p.is_closed():
                    yield p
            except Exception:
                pass

    def _detect_login_on_page(self, page) -> tuple[bool, str]:
        """检测单个页面的登录状态。

        综合判断：URL + 页面文本 + DOM 元素 + Cookie 辅助信号。

        Returns:
            (is_logged_in, reason)
        """
        try:
            url = page.url or ""
        except Exception:
            return False, "无法获取 URL"

        # ── 负向信号：明确的登录页 ──
        if "/web/user" in url and "ka=header-login" in url:
            # 登录入口页，检查是否有未登录信号
            try:
                text = page.evaluate("document.body.innerText.substring(0, 3000)")
                for sig in _LOGIN_TEXT_SIGNALS:
                    if sig in text:
                        return False, f"登录页，检测到「{sig}」"
            except Exception:
                pass
            return False, "在登录入口页"

        if "/passport/" in url or "/login" in url:
            return False, f"在登录/通行证页: {url}"

        # ── 非 Boss 站 ──
        if "zhipin.com" not in url:
            return False, f"非 Boss 站: {url}"

        # ── Boss 站内，综合判断 ──
        try:
            text = page.evaluate("document.body.innerText.substring(0, 5000)")
        except Exception:
            text = ""

        # 检查未登录负向信号
        for sig in _LOGIN_TEXT_SIGNALS:
            if sig in text:
                # 但 security_check 页面可能同时包含登录信号和已登录内容
                # 需要进一步检查是否有已登录正向信号
                has_auth = any(ind in text for ind in _AUTH_TEXT_SIGNALS)
                if has_auth:
                    return True, f"security_check 页面但包含已登录内容（检测到「{sig}」+ 已登录信号）"
                return False, f"检测到未登录信号「{sig}」"

        # 检查已登录正向信号
        for ind in _AUTH_TEXT_SIGNALS:
            if ind in text:
                return True, f"检测到已登录信号「{ind}」"

        # 检查 URL 路径特征
        if "/web/geek" in url:
            # /web/geek 路径通常是已登录用户的页面
            return True, f"在 /web/geek 路径: {url}"

        # 检查 Cookie 辅助信号
        try:
            cookies = page.evaluate(
                "() => document.cookie.split(';').map(c => c.trim().split('=')[0])"
            )
            auth_cookie_count = sum(1 for c in cookies if c in _AUTH_COOKIE_NAMES)
            if auth_cookie_count >= 2:
                return True, f"检测到 {auth_cookie_count} 个登录态 Cookie"
        except Exception:
            pass

        # 默认：Boss 站内但无法确定，保守返回 False
        return False, f"Boss 站内但未检测到明确登录态: {url}"

    def _select_best_boss_page(self) -> tuple:
        """扫描所有页面，选择最佳的已登录 Boss 页面。

        Returns:
            (best_page, is_logged_in, reason)
        """
        boss_pages = []
        any_logged_in = None

        for page in self._iter_open_pages():
            try:
                url = page.url or ""
            except Exception:
                continue

            if "zhipin.com" not in url:
                continue

            is_logged, reason = self._detect_login_on_page(page)
            boss_pages.append((page, is_logged, reason))

            if is_logged and any_logged_in is None:
                any_logged_in = (page, True, reason)

        # 优先返回已登录的 Boss 页面
        if any_logged_in:
            return any_logged_in

        # 如果有 Boss 页面但都未登录，返回第一个
        if boss_pages:
            return boss_pages[0]

        # 没有 Boss 页面
        return None, False, "未找到 Boss 直聘页面"

    def _check_login(self) -> bool:
        """检查是否已登录（使用当前 self.page）"""
        if not self.page:
            return False
        is_logged, reason = self._detect_login_on_page(self.page)
        logger.debug(f"登录检测: {reason}")
        return is_logged

    def _navigate_to_boss(self) -> None:
        """导航到 Boss 登录页或搜索页。

        启动后主动跳转，避免停留在 Google/空白页。
        参考 boss_firefox.py 启动后 goto 登录页。
        """
        if not self.page:
            return
        try:
            current_url = self.page.url
            # 如果当前不在 Boss 站，跳转到登录页
            if "zhipin.com" not in current_url:
                logger.info(f"当前页面非 Boss，跳转到登录页: {BOSS_LOGIN_URL}")
                self.page.goto(BOSS_LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
            # 如果在 Boss 站但未登录，也跳转到登录页
            elif not self._check_login():
                logger.info("Boss 站内但未登录，跳转到登录页")
                self.page.goto(BOSS_LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            logger.warning(f"跳转 Boss 登录页失败: {e}")

    def open_login_page(self) -> BrowserStatus:
        """强制打开 Boss 登录页。

        如果浏览器未启动，先启动。
        然后跳转到登录页。
        """
        if not self._running or self._ctx is None:
            # 先启动浏览器
            result = self.start(headless=False)
            if not result.running:
                return result

        try:
            self.page.goto(BOSS_LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
            logger.info("已打开 Boss 登录页")
        except Exception as e:
            logger.warning(f"打开登录页失败: {e}")

        # 重新检查登录状态
        best_page, logged_in, reason = self._select_best_boss_page()
        if best_page:
            self.page = best_page
        self._logged_in = logged_in

        page_count = len(self._ctx.pages) if self._ctx else 0

        return BrowserStatus(
            running=True,
            logged_in=logged_in,
            status="需登录" if not logged_in else "运行中",
            message="已打开 Boss 登录页，请扫码/验证登录后重试采集" if not logged_in else "Boss 浏览器已登录",
            current_url=self.page.url if self.page else "",
            page_count=page_count,
            detection_reason=reason,
        )

    def search_and_capture(
        self,
        job_name: str,
        city: str = "",
        max_jobs: int = 10,
        filters: dict = None,
    ) -> list[dict]:
        """搜索并采集 JD 列表。

        Args:
            job_name: 岗位关键词
            city: 城市名
            max_jobs: 最多采集数量
            filters: 筛选条件

        Returns:
            JD 列表 [{"title", "company", "salary", "city", "experience",
                      "education", "raw_text", "source_url", ...}]
        """
        if not self._running or self._ctx is None:
            raise RuntimeError("浏览器未启动")

        # 扫描所有页面，选择最佳已登录 Boss 页面
        best_page, logged_in, reason = self._select_best_boss_page()
        if best_page:
            self.page = best_page
        self._logged_in = logged_in

        if not logged_in:
            raise RuntimeError("需要登录，请在浏览器中完成登录后重试")

        city_code = _CITY_CODES.get(city, "")
        search_url = self._build_search_url(job_name, city_code, filters)
        logger.info(f"Boss 浏览器采集: job_name={job_name}, city={city}, url={search_url}")

        # 导航到搜索页
        try:
            self.page.goto(search_url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            logger.warning(f"页面加载超时，继续尝试: {e}")

        # 再次检查是否被重定向到登录页
        if not self._check_login():
            raise RuntimeError("需要登录，请在浏览器中完成登录后重试")

        # 优先 XHR 采集
        jobs = self._fetch_jobs_via_xhr(job_name, city_code, max_jobs, filters)

        # Fallback: DOM 解析（如果 XHR 完全无结果）
        if not jobs:
            logger.info("XHR 采集无结果，尝试 DOM 解析")
            jobs = self._extract_job_cards()

        # 对 detail_missing 的岗位，尝试 DOM 详情页 fallback
        for job in jobs:
            if job.get("detail_missing") and job.get("source_url"):
                logger.info(f"尝试 DOM 详情 fallback: {job['title']}")
                dom_text = self._fetch_job_detail_via_dom(job["source_url"])
                if dom_text and len(dom_text) > 50:
                    job["raw_text"] = dom_text
                    job["detail_missing"] = False
                    job["fallback_only"] = False
                    logger.info(f"DOM fallback 成功: {job['title']} ({len(dom_text)} 字符)")
                time.sleep(1.5)  # 控制频率

        return jobs[:max_jobs]

    def _build_search_url(self, job_name: str, city_code: str = "", filters: dict = None) -> str:
        """构建搜索 URL"""
        params = f"query={job_name}"
        if city_code:
            params += f"&city={city_code}"
        if filters:
            if filters.get("experience"):
                params += f"&experience={filters['experience']}"
            if filters.get("education"):
                params += f"&degree={filters['education']}"
        return f"{BOSS_SEARCH_URL}?{params}"

    def _fetch_jobs_via_xhr(
        self, job_name: str, city_code: str, max_jobs: int, filters: dict = None
    ) -> list[dict]:
        """通过浏览器内 fetch 调用 Boss API 采集岗位列表。

        参考: boss_firefox.py _fetch_jobs_via_xhr()
        """
        try:
            # 构建请求参数
            ts = int(time.time() * 1000)
            experience = (filters or {}).get("experience", "")
            degree = (filters or {}).get("education", "")

            # 在浏览器中执行 fetch
            js_code = f"""
            async () => {{
                const ts = {ts};
                const city = '{city_code}';
                const query = '{job_name}';
                const experience = '{experience}';
                const degree = '{degree}';
                const pageSize = Math.min({max_jobs}, 30);

                // Step 1: 获取岗位列表
                const listUrl = `{_BOSS_JOB_LIST_API}?_=${{ts}}`;
                const listBody = `page=1&pageSize=${{pageSize}}&city=${{city}}&query=${{encodeURIComponent(query)}}&expectInfo=&multiSubway=&position=&jobType=&salary=&experience=${{experience}}&degree=${{degree}}&industry=&stage=&scene=1&encryptExpectId=`;

                let listResp;
                try {{
                    listResp = await fetch(listUrl, {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-Requested-With': 'XMLHttpRequest',
                        }},
                        credentials: 'include',
                        body: listBody,
                    }});
                }} catch(e) {{
                    return {{error: '列表请求失败: ' + e.message, jobs: []}};
                }}

                let listData;
                try {{
                    listData = await listResp.json();
                }} catch(e) {{
                    return {{error: '列表响应解析失败', jobs: []}};
                }}

                if (listData.code !== 0) {{
                    return {{error: 'API 返回错误 code=' + listData.code, jobs: []}};
                }}

                const jobList = (listData.zpData && listData.zpData.jobList) || [];
                if (!jobList.length) {{
                    return {{error: '', jobs: []}};
                }}

                // Step 2: 获取每个岗位的详情
                const jobs = [];
                for (const job of jobList) {{
                    let detail = null;
                    let detailCode = null;
                    let detailMsg = '';
                    if (job.securityId && job.lid) {{
                        try {{
                            const detailUrl = `${{_BOSS_JOB_DETAIL_API}}?securityId=${{encodeURIComponent(job.securityId)}}&lid=${{encodeURIComponent(job.lid)}}`;
                            const detailResp = await fetch(detailUrl, {{
                                method: 'GET',
                                headers: {{
                                    'X-Requested-With': 'XMLHttpRequest',
                                }},
                                credentials: 'include',
                            }});
                            const detailData = await detailResp.json();
                            detailCode = detailData.code;
                            detailMsg = detailData.message || '';
                            if (detailData.code === 0 && detailData.zpData) {{
                                detail = detailData.zpData;
                            }}
                        }} catch(e) {{
                            detailMsg = '请求异常: ' + e.message;
                        }}
                        // 控制频率
                        await new Promise(r => setTimeout(r, 1500));
                    }}

                    const jobInfo = detail ? detail.jobInfo : null;
                    const bossInfo = detail ? detail.bossInfo : null;

                    // 尝试多种字段名提取 JD 文本
                    let rawText = '';
                    if (jobInfo) {{
                        const descFields = [
                            'postDescription', 'description', 'jobDescription',
                            'jobDesc', 'postDesc', 'jobDetail', 'detail',
                            'requirement', 'responsibility',
                        ];
                        for (const field of descFields) {{
                            if (jobInfo[field] && typeof jobInfo[field] === 'string' && jobInfo[field].length > 20) {{
                                rawText = jobInfo[field];
                                break;
                            }}
                        }}
                        // 如果还没有，拼接职责+要求
                        if (!rawText) {{
                            const parts = [];
                            if (jobInfo.responsibility) parts.push(jobInfo.responsibility);
                            if (jobInfo.requirement) parts.push(jobInfo.requirement);
                            if (parts.length) rawText = parts.join('\\n\\n');
                        }}
                    }}

                    // 如果详情为空，从列表数据拼接最低限度 fallback
                    let detailMissing = !rawText;
                    let fallbackText = '';
                    if (detailMissing) {{
                        const parts = [];
                        if (job.jobName) parts.push('岗位名称: ' + job.jobName);
                        if (job.brandName) parts.push('公司: ' + job.brandName);
                        if (job.salaryDesc) parts.push('薪资: ' + job.salaryDesc);
                        if (job.cityName) parts.push('城市: ' + job.cityName);
                        if (job.jobExperience) parts.push('经验: ' + job.jobExperience);
                        if (job.jobDegree) parts.push('学历: ' + job.jobDegree);
                        // 从列表中提取技能标签
                        if (job.skills && job.skills.length) {{
                            parts.push('技能标签: ' + job.skills.join(', '));
                        }}
                        fallbackText = parts.join('\\n');
                    }}

                    jobs.push({{
                        title: job.jobName || '',
                        company: job.brandName || '',
                        salary: job.salaryDesc || '',
                        city: job.cityName || '',
                        experience: job.jobExperience || '',
                        education: job.jobDegree || '',
                        raw_text: rawText || fallbackText,
                        source_url: job.encryptJobId ? `https://www.zhipin.com/job_detail/${{job.encryptJobId}}.html` : '',
                        hr_name: (bossInfo ? bossInfo.name : '') || job.bossName || '',
                        hr_title: (bossInfo ? bossInfo.title : '') || job.bossTitle || '',
                        securityId: job.securityId || '',
                        lid: job.lid || '',
                        encryptJobId: job.encryptJobId || '',
                        detail_missing: detailMissing,
                        detail_code: detailCode,
                        detail_msg: detailMsg,
                        fallback_only: detailMissing && !!fallbackText,
                    }});
                }}

                return {{error: '', jobs: jobs}};
            }}
            """

            result = self.page.evaluate(js_code)

            if result.get("error"):
                logger.warning(f"XHR 采集失败: {result['error']}")
                return []

            jobs = result.get("jobs", [])
            # 记录详情提取情况
            with_detail = sum(1 for j in jobs if not j.get("detail_missing"))
            without_detail = sum(1 for j in jobs if j.get("detail_missing"))
            logger.info(f"XHR 采集到 {len(jobs)} 个岗位: {with_detail} 有详情, {without_detail} 无详情")
            for j in jobs:
                if j.get("detail_missing"):
                    logger.info(f"  详情缺失: {j['title']} (code={j.get('detail_code')}, msg={j.get('detail_msg', '')[:50]})")
            return jobs

        except Exception as e:
            logger.warning(f"XHR 采集异常: {e}")
            return []

    def _fetch_job_detail_via_dom(self, source_url: str) -> str:
        """通过 DOM 从详情页提取 JD 文本。

        打开岗位详情页，从页面 DOM 提取职位描述。
        """
        if not self.page or not source_url:
            return ""
        try:
            self.page.goto(source_url, wait_until="domcontentloaded", timeout=15000)
            time.sleep(1.5)

            js_code = """
            () => {
                // 尝试多种选择器提取 JD 正文
                const selectors = [
                    '.job-detail-section',
                    '.job-sec-text',
                    '.job-detail-container',
                    '.job-detail',
                    '[class*="job-detail"]',
                    '[class*="job-sec"]',
                    '[class*="detail-content"]',
                    '.text',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.length > 50) {
                        return el.innerText.trim();
                    }
                }
                // 兜底：提取主要区域文本
                const main = document.querySelector('main')
                    || document.querySelector('.main')
                    || document.querySelector('[class*="container"]')
                    || document.querySelector('[class*="content"]');
                if (main && main.innerText.length > 100) {
                    return main.innerText.substring(0, 5000).trim();
                }
                // 最后兜底
                const body = document.body.innerText;
                if (body.length > 200) {
                    return body.substring(0, 5000).trim();
                }
                return '';
            }
            """
            text = self.page.evaluate(js_code) or ""
            if text and len(text) > 50:
                logger.info(f"DOM 详情提取成功: {len(text)} 字符")
                return text
            return ""
        except Exception as e:
            logger.warning(f"DOM 详情提取失败: {e}")
            return ""

    def _extract_job_cards(self) -> list[dict]:
        """DOM fallback：从页面中提取岗位卡片。

        参考: boss_firefox.py _extract_job_cards()
        """
        try:
            js_code = """
            () => {
                const jobs = [];
                const links = document.querySelectorAll('a[href*="/job_detail/"]');
                for (const link of links) {
                    const card = link.closest('.job-card-wrapper')
                        || link.closest('.job-card-body')
                        || link.closest('.job-primary')
                        || link.closest('[class*="job-card"]')
                        || link.parentElement;
                    if (!card) continue;

                    const titleEl = card.querySelector('.job-name')
                        || card.querySelector('[class*="job-name"]')
                        || card.querySelector('span[class*="title"]');
                    const companyEl = card.querySelector('.company-name')
                        || card.querySelector('[class*="company-name"]')
                        || card.querySelector('[class*="company"]');
                    const salaryEl = card.querySelector('.salary')
                        || card.querySelector('[class*="salary"]');
                    const cityEl = card.querySelector('.job-area')
                        || card.querySelector('[class*="area"]');
                    const expEl = card.querySelector('.tag-list li:first-child')
                        || card.querySelector('[class*="experience"]');
                    const eduEl = card.querySelector('.tag-list li:nth-child(2)')
                        || card.querySelector('[class*="degree"]');

                    const title = titleEl ? titleEl.textContent.trim() : '';
                    const company = companyEl ? companyEl.textContent.trim() : '';
                    const salary = salaryEl ? salaryEl.textContent.trim() : '';
                    const city = cityEl ? cityEl.textContent.trim() : '';

                    if (!title) continue;

                    jobs.push({
                        title: title,
                        company: company,
                        salary: salary,
                        city: city,
                        experience: expEl ? expEl.textContent.trim() : '',
                        education: eduEl ? eduEl.textContent.trim() : '',
                        raw_text: '',
                        source_url: link.href || '',
                    });
                }
                return jobs;
            }
            """
            jobs = self.page.evaluate(js_code)
            logger.info(f"DOM 解析到 {len(jobs)} 个岗位卡片")

            # 对没有 raw_text 的岗位，尝试获取详情
            for job in jobs:
                if not job.get("raw_text") and job.get("source_url"):
                    try:
                        detail_text = self._fetch_job_detail_page(job["source_url"])
                        if detail_text:
                            job["raw_text"] = detail_text
                        time.sleep(1.5)
                    except Exception:
                        pass

            return jobs

        except Exception as e:
            logger.warning(f"DOM 解析异常: {e}")
            return []

    def _fetch_job_detail_page(self, url: str) -> str:
        """访问岗位详情页，提取 JD 文本"""
        if not self.page:
            return ""
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
            time.sleep(1)

            # 提取 JD 正文
            js_code = """
            () => {
                // 尝试多种选择器
                const selectors = [
                    '.job-detail-section',
                    '.job-sec-text',
                    '[class*="job-detail"]',
                    '[class*="job-description"]',
                    '.text',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.length > 100) {
                        return el.innerText;
                    }
                }
                // 兜底：提取主要区域文本
                const main = document.querySelector('main')
                    || document.querySelector('.main')
                    || document.querySelector('[class*="container"]');
                if (main) return main.innerText.substring(0, 5000);
                return document.body.innerText.substring(0, 5000);
            }
            """
            return self.page.evaluate(js_code) or ""
        except Exception as e:
            logger.warning(f"详情页获取失败 {url}: {e}")
            return ""


# 全局单例
_browser_capture: Optional[BossBrowserCapture] = None


def get_browser_capture() -> BossBrowserCapture:
    """获取全局浏览器采集器实例"""
    global _browser_capture
    if _browser_capture is None:
        _browser_capture = BossBrowserCapture()
    return _browser_capture


def start_browser(headless: bool = False) -> BrowserStatus:
    """启动浏览器"""
    return get_browser_capture().start(headless=headless)


def stop_browser() -> BrowserStatus:
    """停止浏览器"""
    return get_browser_capture().stop()


def get_browser_status() -> BrowserStatus:
    """获取浏览器状态"""
    return get_browser_capture().status()


def open_login_page() -> BrowserStatus:
    """打开 Boss 登录页"""
    return get_browser_capture().open_login_page()


def capture_with_browser(
    job_name: str,
    city: str = "",
    max_jobs: int = 10,
    filters: dict = None,
) -> list[dict]:
    """使用浏览器采集 JD"""
    return get_browser_capture().search_and_capture(
        job_name=job_name,
        city=city,
        max_jobs=max_jobs,
        filters=filters,
    )
