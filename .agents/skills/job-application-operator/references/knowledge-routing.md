# Knowledge routing

Resolve `config/knowledge-sources.json` before any vault read. The resolver checks, in order: `JOBOPS_KNOWLEDGE_MANIFEST`, `%LOCALAPPDATA%/JobOps/knowledge-location.json`, the project and a bounded set of ancestors, then adjacent named AI workspaces. An explicit but invalid higher-priority location blocks fallback.

Use sources as follows:

- `ai_public_core`: technical concepts, AI systems and validated tooling patterns. Context only.
- `business_public_core`: company, industry, competition, environment, workflow and risk methods. Context only.
- `joint_navigation`: approved routing entry point. Context only.
- `personal_redacted`: redacted Case, Project, SOP, artifact/configuration health and personal-practice bridge records. This can propose evidence, but external wording still requires an approved Claim Registry entry.

Prefer `PAI-CASE-0033-求职品牌重构一页咨询简历.md`, the completed-work ledger, Project overview, verified SOP, current health records, the TikTok AI Strategic Analyst bridge, and the interview learning/evidence matrix when present inside the configured allowlist.

Return source ID, relative path, title, heading/paragraph, content SHA-256, historical-completion flag and current-health flag. Resolve Obsidian links only inside the selected source; block ambiguous or escaping links.

Never route into data import areas, original exports/backups, raw attachments, original session logs, browser cookies, passwords, tokens, verification codes or unredacted/private material. Do not enumerate a hard-excluded subtree before rejecting it.

