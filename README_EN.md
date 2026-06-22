# JobLab — AI Job Fit Analysis

> ⚠️ **Disclaimer:** This version is intended for local development and evaluation. It does not yet provide production-grade authentication, authorization, or API rate limiting. Do not expose it directly to the public internet. See [SECURITY.md](SECURITY.md) for details.

JobLab turns real job descriptions and a candidate resume into two structured profiles, then produces an evidence-based fit report with gaps and next actions.

[中文说明](README.md)

## Product flow

```text
Collect job descriptions
→ Build the job profile
→ Parse the resume
→ Build the candidate profile
→ Generate the fit report
```

The current Product MVP includes:

- A product landing page at `/`
- A job-fit workbench at `/app`
- Boss Zhipin browser-assisted JD collection and manual JD import
- Primary and supplementary job keywords
- Experience, education, company-size, HR-activity, and city filters
- Resume parsing for PDF, DOCX, and TXT
- Five-dimensional fit analysis
- Report history, reruns, evaluation feedback, and contextual advisor questions

## Documentation

- [Frontend API reference](docs/frontend-api-reference.md)
- [Landing-page product brief](docs/landing-page-product-brief.md)
- [Product MVP plan](docs/product-plan-mvp.md)
- [Project handoff](docs/project-handoff-2026-06-18.md)
- [Content strategy](docs/content-strategy.md)

## Technology

| Layer | Stack |
|---|---|
| Frontend | HTML, CSS, vanilla JavaScript |
| Backend | FastAPI, Pydantic, SQLAlchemy |
| Data | MySQL or SQLite |
| Browser collection | Playwright |
| AI | OpenAI-compatible model API with rule-based fallbacks |
| Evaluation | Pytest and Golden Set evaluation |

## Local setup

```bash
pip install -r requirements.txt
playwright install firefox
```

Create `.env` from the example template:

```bash
cp .env.example .env
```

Then edit `.env` and fill in your own API keys. You must register at the respective providers (DeepSeek, DashScope, AnySearch, etc.) and obtain your own keys. **Never commit your `.env` file.**

Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | Database connection string (default: SQLite) |
| `DEEPSEEK_API_KEY` | For AI analysis | DeepSeek API key for LLM-powered analysis |
| `MODEL_BASE_URL` | Yes | LLM API base URL |
| `MODEL_NAME` | Yes | LLM model name |
| `DASHSCOPE_API_KEY` | Optional | For semantic embeddings |
| `ANYSEARCH_API_KEY` | Optional | For web search |

Start the service:

```bash
python main.py serve
```

Then open:

- Landing page: `http://127.0.0.1:8000/`
- Product workbench: `http://127.0.0.1:8000/app`
- OpenAPI docs: `http://127.0.0.1:8000/docs`

## Validation

```bash
node --check ui/static/app.js
python -m py_compile api/fastapi_app.py
pytest tests/ -q
python eval/run_golden_eval.py
```

Use the project's actual virtual environment when running Python checks.

## Safety boundaries

- JobLab does not submit applications automatically.
- It does not bypass CAPTCHAs or recruitment-site risk controls.
- Boss login remains inside the local persistent browser profile (`.boss_profile/`).
- Uploaded source files are parsed locally and are not uploaded to the JobLab maintainer. When AI analysis is enabled, resume text and structured profiles may be sent to your configured LLM provider — review that provider's privacy and data retention policy. Without an API key, JobLab falls back to rule-based analysis.
- Runtime data, browser profiles, databases, uploaded resumes, generated output, and IDE settings are excluded from Git.

## Repository layout

```text
api/                 FastAPI routes
services/            JD collection, profile extraction, and fit analysis
ui/static/           Product MVP workbench
models/              SQLAlchemy models
agents/ and graphs/  Compatible agent workflows
eval/                Golden Set evaluation
scripts/             Maintenance and demo-data tools
tests/               Automated tests
docs/                Product and integration documentation
```

Current stable frontend milestone: `product-mvp-v0.41`.

## Contributing and Feedback

- Found a bug? [Open an Issue](https://github.com/5u-lzh/JobSearch/issues)
- Like the project? Give it a ⭐ to show support
- Security concerns? See [SECURITY.md](SECURITY.md)

## Disclaimer

- JobLab does **not** guarantee interview invitations or job offers.
- JobLab does **not** automatically submit applications on your behalf.
- JobLab does **not** bypass any platform's anti-bot protections.
- JobLab is a **personal analysis tool**, not a job application automation service.
