# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest (product-mvp) | ✅ |
| Older versions | ❌ |

## Reporting a Vulnerability

If you discover a security vulnerability in JobLab, please report it responsibly:

1. **Do NOT open a public Issue** with vulnerability details, leaked keys, or resume data.
2. Use [GitHub Private Vulnerability Reporting](https://github.com/5u-lzh/JobSearch/security/advisories/new) if available.
3. Alternatively, contact the maintainer via the [GitHub profile](https://github.com/5u-lzh).

We will acknowledge receipt as soon as possible. We do not guarantee a fixed response time.

## Scope and Limitations

### Current Version: Local Development Only

This version of JobLab is intended for **local development and personal evaluation**. It is NOT production-ready. Specifically:

- **No production-grade authentication** — there is no user login or session management.
- **No authorization or multi-tenant isolation** — all data is accessible to the single local user.
- **No API rate limiting** — the API endpoints have no request throttling.
- **No HTTPS enforcement** — the service runs on plain HTTP locally.

**Do not expose JobLab directly to the public internet.** If you need remote access, use a VPN, SSH tunnel, or a reverse proxy with proper authentication in front of it.

### What JobLab Does NOT Do

- It does **not** automatically submit job applications.
- It does **not** bypass CAPTCHAs or anti-bot protections.
- It does **not** store or transmit your Boss Zhipin credentials to any server.
- It does **not** upload source files to the JobLab maintainer. When AI analysis is enabled, resume text and structured profiles are sent to your configured LLM provider — review that provider's privacy and data retention policy. Without an API key, JobLab falls back to rule-based analysis.

## User Responsibilities

When running JobLab locally, you are responsible for:

1. **API Keys** — Keep your `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `ANYSEARCH_API_KEY`, and any other API keys confidential. Do not commit them to version control.
2. **Database** — Protect your local database (`data/` directory or MySQL instance) from unauthorized access.
3. **Browser Profile** — The `.boss_profile/` directory contains Boss Zhipin login state. Do not share or commit it.
4. **Resume Data** — Uploaded resumes are parsed in-memory and are not persisted as original files by JobLab. Structured profiles and reports are stored in your local database. When AI analysis is enabled, resume text is sent to your configured LLM provider. Handle all personal data in accordance with applicable privacy laws.
5. **`.env` file** — Never commit your `.env` file. Ensure it is listed in `.gitignore`.

## If a Key Is Leaked

If you accidentally commit or expose an API key:

1. **Revoke the key first** — go to the provider's dashboard and invalidate the leaked key immediately.
2. **Generate a new key** and update your local `.env`.
3. **Clean Git history** if the key was committed — use [`git-filter-repo`](https://github.com/newren/git-filter-repo) or BFG Repo-Cleaner. **Back up your repository before rewriting history.**
4. **Force-push and notify** — after cleaning, force-push the rewritten history and inform all collaborators so they can re-clone or reset.

## Data Stored Locally

JobLab stores the following data on your local machine:

| Data | Location | Description |
|------|----------|-------------|
| Structured profiles & reports | SQLite / MySQL | Candidate profiles, job profiles, fit reports |
| Browser state | `.boss_profile/` | Boss Zhipin login cookies and session |
| Runtime databases / caches | `data/` | ChromaDB vectors, temporary caches |
| Configuration | `.env` | API keys and database connection strings |
| Uploaded source files | In-memory only | Parsed during the request; JobLab does not guarantee persistent storage of original files |

None of this data is uploaded to the JobLab project or its maintainer. When AI analysis is enabled, resume text and structured profiles are sent to the user-configured LLM provider.
