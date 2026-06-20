# JobLab 功能服务前端 API 接口说明

> 面向功能服务页面的前端设计与开发。
> 依据：`api/fastapi_app.py` 当前工作区代码，整理日期 2026-06-19。
> 当前 API 尚未提供正式登录鉴权；`user_id` 主要用于数据归属和查询过滤，不能视为安全凭证。

## 1. 接入约定

### 1.1 服务地址

本地开发：

```text
http://localhost:8000
```

FastAPI 自动文档：

```text
http://localhost:8000/docs
http://localhost:8000/redoc
```

当前后端根路由 `/` 返回产品落地页，功能工作台由 `/app` 返回。落地页中的“开始分析”按钮直接跳转到 `/app`；前端静态资源与 API 保持同源。

### 1.2 请求格式

- 普通接口：`application/json`
- 文件上传：`multipart/form-data`
- 中文路径参数必须使用 `encodeURIComponent`
- 当前前端与 API 同源，不需要额外配置 CORS

### 1.3 响应与错误

业务成功一般返回：

```json
{
  "code": 200
}
```

项目目前存在两种错误形式，前端需要同时处理：

1. FastAPI HTTP 错误：

```json
{
  "detail": "错误说明"
}
```

HTTP 状态通常为 `400`、`404` 或 `500`。

2. HTTP 200 中的业务错误：

```json
{
  "code": 404,
  "message": "对象不存在"
}
```

推荐封装：

```js
async function request(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.detail || data.message || `HTTP ${response.status}`);
  }
  if (data.code && data.code !== 200) {
    throw new Error(data.message || "请求失败");
  }
  return data;
}
```

### 1.4 置信度和分析模式

画像和报告可能包含：

```text
confidence: high | medium | low
analysis_mode: agent | rule_fallback
```

- `agent`：LLM 综合分析成功。
- `rule_fallback`：LLM 不可用或输出无效，使用规则结果。
- `low` 置信度必须在界面中显示“结果仅供参考”。

## 2. 推荐前端主流程

```text
1. GET  /user
       ↓ 获得 user_id
2. POST /boss/browser/start
3. POST /jd_sources/boss/capture
   或 POST /jd_sources/boss/import
       ↓ 获得 job_profile_id
4. POST /resume/parse（上传文件时）
       ↓ 获得 resume_text
5. POST /candidate_profiles/analyze
       ↓ 获得 candidate_profile_id
6. POST /fit_analysis_reports
       ↓ 获得 fit_analysis_id 和报告
7. GET  /fit_analysis_reports?user_id=...
8. POST /fit_analysis_reports/{id}/advisor
```

如果用户不采集 Boss JD，可以手动导入 JD 后重建岗位画像。

## 3. 用户接口

### 3.1 获取或创建用户

```http
GET /user?username={username}
```

`username` 可省略；省略时后端生成临时用户名。

响应：

```json
{
  "code": 200,
  "user_id": 12,
  "username": "用户_a1b2c3d4"
}
```

前端应保存 `user_id`，后续创建候选人画像、报告和查询历史时复用。

### 3.2 用户列表

```http
GET /users
```

```json
{
  "code": 200,
  "users": [
    {"id": 12, "username": "演示用户"}
  ]
}
```

### 3.3 删除用户数据

```http
DELETE /user/{user_id}
```

这是破坏性操作，前端必须二次确认。

## 4. Boss 浏览器与 JD 采集

### 4.1 启动浏览器

```http
POST /boss/browser/start
```

浏览器以可见模式启动，用户需要在弹出的窗口中手动登录 Boss。

### 4.2 获取浏览器状态

```http
GET /boss/browser/status
```

响应：

```json
{
  "code": 200,
  "running": true,
  "logged_in": false,
  "status": "需登录",
  "message": "请在浏览器中登录",
  "current_url": "https://www.zhipin.com/...",
  "page_count": 1,
  "detection_reason": ""
}
```

`status` 可能为：`未启动`、`运行中`、`需登录`、`风控`、`已停止`。

### 4.3 打开登录页

```http
POST /boss/browser/open-login
```

### 4.4 停止浏览器

```http
POST /boss/browser/stop
```

### 4.5 采集 Boss JD

```http
POST /jd_sources/boss/capture
Content-Type: application/json
```

请求：

```json
{
  "job_name": "AI 产品经理",
  "extra_job_keywords": ["大模型产品经理", "智能体产品经理"],
  "city": "杭州",
  "max_jobs": 10,
  "filters": {
    "experience": "1-3年",
    "education": "本科",
    "company_size": "20-99人",
    "hr_activity": "7d"
  }
}
```

约束：

- `max_jobs`：1 到 30，默认 10
- `extra_job_keywords`：补充岗位关键词数组；服务端会与主岗位词分别搜索、合并去重，并统一归入 `job_name` 对应的岗位画像。
- `company_size`：可选值为 `0-20人`、`20-99人`、`100-499人`、`500-999人`、`1000-9999人`、`10000人以上`。
- `hr_activity`：可选值为 `recent`、`today`、`3d`、`7d`、`30d`。
- 公司规模和 HR 活跃度依赖 Boss 返回的岗位元数据；缺少对应元数据的岗位不会通过已选择的筛选条件。

响应：

```json
{
  "code": 200,
  "captured_count": 10,
  "imported_count": 7,
  "skipped_count": 2,
  "failed_count": 1,
  "detail_missing_count": 0,
  "blocked_reason": "",
  "documents": [
    {"title": "AI 产品经理", "company": "示例公司"}
  ],
  "warnings": [],
  "failed_examples": [],
  "profile_generated": true,
  "job_profile_id": 31,
  "job_profile": {},
  "search_keywords": ["AI 产品经理", "大模型产品经理", "智能体产品经理"],
  "applied_filters": {
    "experience": "1-3年",
    "education": "本科",
    "company_size": "20-99人",
    "hr_activity": "7d"
  }
}
```

前端判断建议：

- `blocked_reason` 非空：展示阻塞原因，不进入成功态。
- `profile_generated=true`：可直接保存 `job_profile_id`。
- 已入库但画像失败：展示“重新生成画像”按钮。

### 4.6 手动导入 JD

```http
POST /jd_sources/boss/import
```

```json
{
  "job_name": "AI 产品经理",
  "jd_text": "完整岗位职责和任职要求……",
  "title": "AI 产品经理",
  "company": "示例公司"
}
```

`jd_text` 应提供完整 JD，不建议只上传岗位标题或招聘卡片摘要。

### 4.7 从已采集 JD 重建岗位画像

```http
POST /job_profiles/rebuild
```

```json
{
  "job_name": "AI 产品经理",
  "source_platform": "boss",
  "top_n": 20
}
```

成功响应：

```json
{
  "code": 200,
  "job_profile_id": 31,
  "profile": {}
}
```

## 5. 岗位画像

### 5.1 直接分析岗位画像

```http
POST /job_profiles/analyze
```

```json
{
  "job_name": "AI 产品经理",
  "top_n": 20
}
```

`top_n` 范围为 1 到 50。该接口基于数据库中已有岗位数据生成画像。

### 5.2 获取岗位画像

```http
GET /job_profiles/{profile_id}
```

画像主要字段：

```json
{
  "id": 31,
  "job_name": "AI 产品经理",
  "job_type": "正式",
  "employment_type": "全职",
  "target_audience": "社招",
  "responsibilities": [],
  "must_have_capabilities": [],
  "nice_to_have_capabilities": [],
  "experience_requirement": "",
  "education_preference": "",
  "major_preference": "",
  "business_context": [],
  "growth_context": [],
  "evidence": [],
  "confidence": "medium",
  "quality_flags": [],
  "sample_count": 7,
  "created_at": "..."
}
```

注意：当前该接口直接读取 ORM 字段，部分 JSON 列可能返回 JSON 字符串。报告详情接口中的 `job_profile` 已完成 JSON 解析，新前端优先使用报告详情中的画像数据。

### 5.3 旧版即时岗位画像

```http
GET /job_profile/{job_name}?top_n=20
```

这是旧版兼容接口，不保存画像 ID。新前端主流程不建议依赖。

## 6. 简历解析与候选人画像

### 6.1 解析简历文件

```http
POST /resume/parse
Content-Type: multipart/form-data
```

字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `file` | File | 是 | PDF、DOCX 或 TXT |

限制：

- 最大 10MB
- 仅支持 `.pdf`、`.docx`、`.txt`
- 扫描图片型 PDF 可能无法提取文本

响应：

```json
{
  "code": 200,
  "text": "解析后的简历文本",
  "file_type": "pdf",
  "char_count": 1860,
  "warnings": []
}
```

Fetch 示例：

```js
const form = new FormData();
form.append("file", file);

const result = await request("/resume/parse", {
  method: "POST",
  body: form
});
```

### 6.2 生成并保存候选人画像

```http
POST /candidate_profiles/analyze
```

```json
{
  "user_id": 12,
  "resume_text": "简历全文",
  "resume_filename": "resume.pdf",
  "conversation_text": ""
}
```

响应：

```json
{
  "code": 200,
  "candidate_profile_id": 45,
  "profile": {
    "education_background": {},
    "skill_stack": [],
    "projects": [],
    "internships": [],
    "work_experiences": [],
    "business_understanding": [],
    "achievements": [],
    "learning_signals": [],
    "transferable_strengths": [],
    "collaboration_signals": [],
    "risk_points": [],
    "evidence": [],
    "confidence": "medium",
    "sensitive_detected": [],
    "summary": ""
  },
  "analysis_mode": "agent"
}
```

### 6.3 获取候选人画像

```http
GET /candidate_profiles/{profile_id}
```

与岗位画像接口类似，部分 JSON 字段当前可能以字符串返回。

### 6.4 旧版简历画像接口

```http
POST /profile/resume_text
POST /profile/resume
POST /candidate_profile
```

这些接口不形成完整的新画像持久化链路，新前端只需把它们视为兼容能力。

## 7. 综合适配报告

### 7.1 创建报告

```http
POST /fit_analysis_reports
```

```json
{
  "user_id": 12,
  "job_profile_id": 31,
  "candidate_profile_id": 45
}
```

响应：

```json
{
  "code": 200,
  "fit_analysis_id": 82,
  "report": {
    "overall_fit_level": "moderate",
    "overall_score": 68.5,
    "fit_summary": "综合适配 moderate……",
    "capability_fit": {
      "level": "moderate",
      "score": 65,
      "summary": "",
      "evidence_refs": []
    },
    "experience_relevance": {},
    "growth_potential": {},
    "evidence_strength": {},
    "risks_and_gaps": {},
    "strengths": [],
    "gaps": [],
    "transferable_strengths": [],
    "learning_plan": [],
    "interview_strategy": [],
    "evidence_refs": [],
    "confidence": "medium"
  },
  "analysis_mode": "agent",
  "rule_score": 64.0
}
```

五个维度固定为：

1. `capability_fit`：能力匹配
2. `experience_relevance`：经历相关
3. `growth_potential`：成长潜力
4. `evidence_strength`：证据充分
5. `risks_and_gaps`：风险与短板

不要在新界面中替换为“学历匹配、文化适配”等后端不存在的维度。

### 7.2 获取报告详情

```http
GET /fit_analysis_reports/{report_id}?user_id=12
```

响应同时回填三部分：

```json
{
  "code": 200,
  "report": {},
  "job_profile": {},
  "candidate_profile": {},
  "warnings": []
}
```

`warnings` 可能包含：

```text
job_profile_missing
candidate_profile_missing
```

这是报告详情页最推荐的单一数据入口。

### 7.3 历史报告列表

```http
GET /fit_analysis_reports?user_id=12&job_name=&limit=20&offset=0
```

约束：

- `limit`：1 到 50
- `offset`：大于等于 0
- `job_name` 可省略

响应：

```json
{
  "code": 200,
  "items": [
    {
      "id": 82,
      "user_id": 12,
      "job_profile_id": 31,
      "candidate_profile_id": 45,
      "job_name": "AI 产品经理",
      "overall_fit_level": "moderate",
      "overall_score": 68.5,
      "confidence": "medium",
      "fit_summary": "",
      "created_at": "2026-06-19 10:30"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0,
  "has_more": false,
  "next_offset": 0
}
```

### 7.4 重新分析

```http
POST /fit_analysis_reports/{report_id}/rerun
```

```json
{
  "user_id": 12
}
```

重新分析会创建一份新报告，不覆盖旧报告。

### 7.5 删除报告

```http
DELETE /fit_analysis_reports/{report_id}?user_id=12
```

前端必须二次确认。

## 8. 适配顾问

```http
POST /fit_analysis_reports/{report_id}/advisor
```

```json
{
  "user_id": 12,
  "question": "我最应该优先补什么？"
}
```

响应：

```json
{
  "code": 200,
  "report_id": 82,
  "answer": "建议优先……",
  "analysis_mode": "agent",
  "evidence_refs": []
}
```

推荐预设问题：

- 为什么是这个适配等级？
- 我最应该优先补什么？
- 如何优化这份简历？
- 针对该岗位如何准备面试？

问题为空时当前接口返回 HTTP 200、业务 `code=400`。

## 9. 技能雷达和差距分析

### 9.1 已分析岗位

```http
GET /skill_rank/_jobs
```

```json
{
  "code": 200,
  "jobs": ["Python后端", "数据分析师"]
}
```

### 9.2 岗位技能排名

```http
GET /skill_rank/{job_name}?top_n=15&user_id=12
```

响应：

```json
{
  "code": 200,
  "data": [
    {
      "skill": "Python",
      "count": 20,
      "total_jds": 25,
      "confidence": "high",
      "quality_reasons": [],
      "reject_count": 0,
      "important_count": 0,
      "user_rejected": false,
      "user_marked_important": false,
      "community_rejected": false,
      "community_important": false
    }
  ],
  "total_jds": 25,
  "last_update": "2026-06-19 09:00",
  "confidence": "medium",
  "filtered_count": 2
}
```

### 9.3 技能差距分析

```http
POST /skill_gap
```

```json
{
  "job_name": "Python后端",
  "user_skills": ["Python", "Git"],
  "user_profile": [],
  "top_n": 15
}
```

返回市场技能、已匹配技能、缺口、覆盖率、优先级和摘要。它是旧版技能差距能力，不替代完整五维适配报告。

### 9.4 技能反馈

```http
POST /skill_feedback
GET /skill_feedback/summary?job_name=Python后端&user_id=12
```

提交请求：

```json
{
  "user_id": 12,
  "job_name": "Python后端",
  "skill_name": "Redis",
  "action": "important"
}
```

`action` 仅允许 `reject` 或 `important`。

## 10. 画像与报告评价

### 10.1 字段级反馈

```http
POST /profile_feedback
```

```json
{
  "user_id": 12,
  "target_type": "fit_analysis_report",
  "target_id": 82,
  "field_name": "gaps",
  "item_name": "缺少实习经历",
  "action": "wrong",
  "comment": "简历中存在实习经历"
}
```

`target_type`：

```text
job_profile | candidate_profile | fit_analysis_report
```

`action`：

```text
reject | important | correct | wrong | missing | confirm
```

### 10.2 整体质量评价

```http
POST /profile_evaluations
```

```json
{
  "user_id": 12,
  "target_type": "fit_analysis_report",
  "target_id": 82,
  "rating": 4,
  "is_correct": true,
  "error_type": "",
  "field_name": "",
  "comment": "建议比较有帮助",
  "useful_for_training": false
}
```

`rating` 范围：0 到 5。

`error_type`：

```text
missing_info | wrong_info | hallucination | weak_evidence
bad_suggestion | unfair_judgment | other
```

查询：

```http
GET /profile_evaluations
GET /profile_evaluations/summary
```

## 11. 任务与统计

### 11.1 查询异步任务

```http
GET /task/{task_id}
```

任务状态：

```text
pending | running | done | failed | cancelled
```

响应：

```json
{
  "code": 200,
  "task": {
    "task_id": "a1b2c3d4",
    "type": "chat",
    "status": "running",
    "progress": "分析意图...",
    "finished": false
  }
}
```

轮询建议：每 1 到 2 秒一次，在 `finished=true` 时停止。

取消：

```http
POST /task/{task_id}/cancel
```

### 11.2 统计信息

```http
GET /stats
```

```json
{
  "code": 200,
  "skill_count": 312,
  "jd_count": 120
}
```

## 12. Legacy 接口

以下接口仍存在，但不建议用于新的主工作台设计：

| 接口 | 原因 |
|---|---|
| `POST /chat` | 通用 Chat 已从主产品入口隐藏 |
| `GET/DELETE /conversation/*` | 旧 Chat 会话能力 |
| `GET /conversations` | 旧 Chat 历史 |
| `POST /research` | 独立深度研究入口已隐藏 |
| `POST /screening_report` | 旧版初筛报告 |
| `POST /candidate_profile` | 旧版即时候选人画像 |
| `GET /job_profile/{job_name}` | 旧版即时岗位画像 |
| `POST /analyze_job` | 旧岗位搜索分析流程，当前为同步阻塞调用 |

## 13. 页面状态设计建议

### 岗位采集页

- 浏览器未启动
- 浏览器已启动但未登录
- 采集中
- 采集成功并生成画像
- 采集成功但画像生成失败
- 风控或验证码阻塞
- 手动导入成功/失败

### 简历画像页

- 未上传
- 文件解析中
- 文件格式或大小错误
- 文本解析成功
- 画像分析中
- Agent 成功
- 规则兜底
- 低置信度或信息不足

### 适配报告页

- 缺少岗位画像
- 缺少候选人画像
- 分析中
- 报告成功
- 规则兜底
- 关联画像缺失
- 顾问回答中/失败

### 历史报告页

- 首次加载
- 空列表
- 分页加载
- 详情加载
- 重跑确认
- 删除确认

## 14. 当前后端限制

前端设计时需要预留，但不要误认为已经解决：

- 没有正式注册、登录和鉴权。
- `user_id` 来自前端参数，不能提供生产级数据隔离。
- 错误响应格式尚未统一。
- 部分获取画像接口可能返回 JSON 字符串。
- Boss 浏览器依赖运行后端机器上的可见浏览器，不适合直接部署到普通无头云服务。
- `analyze_job` 和部分 LLM 接口可能长时间阻塞。
- 没有稳定的上传进度、服务端任务持久化和断点恢复。
- 当前没有邀请码、付费、订阅或权限管理。

前端可以先按照本文主流程设计；后端适配阶段建议优先统一响应格式、画像序列化、异步任务和用户鉴权。
