# JobLab — AI 求职适配分析平台

基于 Boss JD 采集、简历解析和画像匹配的求职适配分析工具。

## 产品与落地页文档

- [产品落地页开发说明](docs/landing-page-product-brief.md) — 产品定位、目标用户、功能边界、页面结构、文案与验收清单
- [功能服务前端 API 接口说明](docs/frontend-api-reference.md) — 主流程、请求响应、页面状态和后端限制
- [MVP 产品规划](docs/product-plan-mvp.md)
- [内容策略](docs/content-strategy.md)

## 核心流程

```
① 采集岗位 JD → ② 生成岗位画像 → ③ 上传简历 → ④ 生成候选人画像 → ⑤ 适配分析报告
```

1. **岗位采集**：通过 Playwright 浏览器采集 Boss 直聘 JD，支持手动粘贴
2. **岗位画像**：从多条 JD 中提取职责、技能、学历、业务场景等结构化画像
3. **简历画像**：解析简历文本，提取教育背景、技能栈、项目经历、成果证据
4. **适配分析**：五维评分（能力匹配、经历相关、成长潜力、证据充分、风险短板）
5. **历史报告**：查看、对比、重新分析历史适配报告

## 技术栈

| 层级 | 技术 |
|------|------|
| **前端** | 原生 HTML/CSS/JS，暗色主题 |
| **后端** | FastAPI + SQLAlchemy |
| **数据库** | MySQL（可选 SQLite） |
| **浏览器采集** | Playwright（Firefox/Chromium） |
| **LLM** | DeepSeek / OpenAI 兼容 API（可选） |
| **评测** | Golden Set 自动评测框架 |

## 本地运行

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 安装 Playwright 浏览器

```bash
playwright install firefox
# 或
playwright install chromium
```

### 3. 配置环境变量

创建 `.env` 文件：

```env
DATABASE_URL=mysql+pymysql://用户名:密码@localhost:3306/joblab
# 或使用 SQLite
# DATABASE_URL=sqlite:///data/joblab.db

# LLM（可选，不配置则使用规则兜底）
DEEPSEEK_API_KEY=your_key
MODEL_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-pro
```

### 4. 启动服务

```bash
python main.py serve
```

浏览器打开：

- 产品落地页：`http://127.0.0.1:8000/`
- 功能工作台：`http://127.0.0.1:8000/app`
- OpenAPI 文档：`http://127.0.0.1:8000/docs`

### 5. 使用流程

1. 从落地页点击“开始分析”，进入 `/app`
2. **准备岗位**：展开采集面板 → 启动浏览器 → 登录 Boss → 设置岗位关键词与筛选条件 → 开始采集
3. **解析简历**：粘贴简历或上传文件 → 识别候选人画像
4. **差距分析**：生成五维适配报告与行动建议
5. **历史报告**：查看或重新分析历史报告

### 6. 运行评测

**重要**：请确保使用项目实际安装依赖的 Python 环境，不要使用系统默认 Python。

```bash
# 推荐测试命令（按顺序执行）

# 1. 语法检查
node --check ui/static/app.js
python -c "import py_compile; py_compile.compile('api/fastapi_app.py', doraise=True)"

# 2. 运行测试（静默模式）
pytest tests/ -q

# 3. Golden Set 评测
python eval/run_golden_eval.py

# 4. 数据清理工具（预览模式）
python scripts/clean_product_data.py --all-demo --dry-run

# 5. 生成演示数据
python scripts/seed_demo_data.py
```

**环境说明**：
- 如果 `python` 指向系统默认路径（如 WindowsApps），请使用项目虚拟环境的 Python
- 推荐使用 `venv` 或 `conda` 环境
- 确保已安装 `requirements.txt` 中的依赖

## 数据说明

### 数据库

- 默认使用 MySQL，可通过 `DATABASE_URL` 切换 SQLite
- 表结构自动创建，无需手动建表
- 本地数据库文件不要提交到 Git

### 浏览器 Profile

- Playwright 浏览器 profile 保存在 `.boss_profile/` 目录
- 包含登录状态，不要提交到 Git
- 删除 `.boss_profile/` 可重置登录状态

### 敏感文件

以下文件不应提交到 Git：
- `.env` — 环境变量和 API 密钥
- `data/` — 运行时数据（ChromaDB、缓存等）
- `.boss_profile/` — 浏览器 profile
- `*.db` / `*.sqlite` — 数据库文件
- 上传的简历文件
- `output/` — 本地生成文件
- `.idea/` / `*.iml` — IDE 配置

## 安全边界

- **不自动投递**：系统只采集和分析，不自动投递简历
- **不绕过验证码**：遇到验证码/风控时暂停，提示用户手动处理
- **不保存账号密码**：Boss 登录状态保存在本地浏览器 profile，不上传服务器
- **仅用于主动分析**：用户主动触发采集和分析，不自动运行

## 数据清理

```bash
# 预览将清理的数据
python scripts/clean_product_data.py --job-name "Python后端" --dry-run

# 确认清理
python scripts/clean_product_data.py --job-name "Python后端" --confirm

# 清理所有演示数据
python scripts/clean_product_data.py --all-demo --confirm
```

## 演示数据

```bash
# 生成演示数据（可重复运行，不会重复插入）
python scripts/seed_demo_data.py

# 先清理再生成
python scripts/seed_demo_data.py --clean
```

生成内容：
- 1 个演示用户
- 2 条演示 JD
- 1 个岗位画像
- 1 个候选人画像
- 1 个适配分析报告

## 常见问题

### 浏览器未登录

点击"启动浏览器"后，在弹出的浏览器中手动登录 Boss 直聘，然后点击"我已完成登录，重试采集"。

### Boss 风控

Boss 直聘反爬严格，遇到风控时：
1. 在浏览器中手动完成验证
2. 等待几分钟后重试
3. 或使用手动粘贴 JD 文本的方式

### 采集到 JD 但画像为空

可能是 JD 质量不达标被过滤。尝试：
1. 增加采集数量
2. 手动粘贴更完整的 JD 文本
3. 检查 JD 是否包含"岗位职责"和"任职要求"段落

### 历史报告刷新丢失

历史报告保存在服务器数据库中，刷新页面后需要重新加载。切换到"历史报告"Tab 会自动加载。

### 旧数据污染如何清理

```bash
# 查看将清理的数据
python scripts/clean_product_data.py --job-name "旧岗位名" --dry-run

# 确认清理
python scripts/clean_product_data.py --job-name "旧岗位名" --confirm
```

## 项目结构

```
JobSearch/
├── main.py                     # CLI 入口
├── api/fastapi_app.py          # FastAPI 服务
├── ui/static/                  # 前端静态文件
│   ├── index.html
│   ├── app.js
│   └── style.css
├── services/                   # 业务服务
│   ├── boss_browser_capture.py # Playwright 浏览器采集
│   ├── boss_capture_service.py # Boss 采集服务
│   ├── job_profile_service.py  # 岗位画像提取
│   ├── candidate_profile_service.py # 候选人画像提取
│   ├── fit_analysis_service.py # 适配分析
│   └── profile_schemas.py      # Pydantic schemas
├── models/                     # SQLAlchemy 模型
├── agents/                     # LLM 智能体
├── tools/                      # 工具函数
├── eval/                       # 评测框架
│   ├── golden_set_v1.json      # Golden Set 标注
│   └── run_golden_eval.py      # 评测脚本
├── scripts/                    # 运维脚本
│   ├── clean_product_data.py   # 数据清理
│   └── seed_demo_data.py       # 演示数据
└── tests/                      # 测试
```

## 版本历史

- v0.41 — 落地页与功能工作台接入、Product MVP UI、组合岗位采集筛选
- v0.40 — 岗位采集桌面 Web 排版优化
- v0.39 — 五维差距报告与功能工作台视觉重构
- v0.34 — 上线前工程化整理、演示数据、数据清理工具
- v0.33 — learning_plan 校准
- v0.32 — 适配分析质量校准
- v0.31 — 适配分析质量评测
- v0.30 — 候选人画像质量评测
- v0.29 — nice_to_have 提取修复
- v0.28 — 岗位画像质量可观测化
- v0.27 — 岗位画像质量提升
- v0.26 — tab 式工作台 + 采集自动生成画像
- v0.25 — Boss 浏览器采集
