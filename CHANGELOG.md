# Changelog / 变更记录

All notable JobFlow changes are recorded here. The project follows semantic versioning; a section marked "release candidate" is not a published release.

所有重要 JobFlow 改动记录于此。项目遵循语义化版本；标记为“发布候选”的版本并不代表已经发布。

## [Unreleased]

- Every review packet now contains a hash-bound six-step application execution plan covering live freshness, guest entry, safe prefill, conditional materials, protected questions, and final submission. It plainly identifies each approval gate and confirms that other jobs continue until the pending-review limit.
- 每份审阅包现在都包含哈希绑定的六步投递计划，依次覆盖实时新鲜度、访客进入、安全预填、按需材料、敏感问题与最终提交；每一步的审批门都会明确显示，当前岗位等待时其他岗位仍会继续处理到待审批上限。
- PDF numeric grounding now accepts only provably equivalent presentation changes (digit grouping, full-width digits, harmless decimal zeros) and bounded adjacent wrapped lines; calculations, scaling, rounding and unrelated-line borrowing still fail closed, while every accepted adjustment is flagged for human review.
- PDF 数字依据现在只放行可证明等价的显示差异（数字分组、全角数字、无意义小数零）及有限相邻换行；计算、单位缩放、四舍五入和借用无关行仍会失败关闭，所有放宽项都会明确要求人工复核。
- AI readiness now requires a non-private full structured entity, metric and provenance test; a simple JSON handshake no longer unlocks document intake.
- PDF intake compares logical and spatial extraction modes and fails closed with content-free OCR/editable-DOCX diagnostics when neither is reliable.
- Each application now carries a validated material plan: one immutable approved Master Resume produces the tailored copy, Cover Letters are generated only when requested, and GitHub/portfolio links or files are bound only to matching fields.
- AI 就绪现在必须通过不含私人资料的完整实体、数字与行号验证；简单 JSON 握手不再开放资料接入。
- PDF 接入会比较逻辑与空间两种本机提取结果；两者均不可靠时，以不含正文的 OCR/可编辑 DOCX 提示失败关闭。
- 每个申请现在都有经验证的材料计划：岗位简历只从同一不可变母版派生，求职信仅按需生成，GitHub/作品集链接或文件仅绑定到对应字段。
- Public GitHub upload and all real recruiting-site actions remain unperformed.
- GitHub 上传与所有真实招聘网站动作均未执行。

## [0.1.0] - release candidate / 发布候选

### Added / 新增

- Bilingual local JobFlow onboarding, DPAPI Candidate Profile and Answer Bank, editable review revisions and bounded pending queue.
- Strict AI entity reconstruction, source coverage, provenance, grounding, conflict and Claim review gates.
- Offline official-careers discovery, fail-closed ATS form analysis, synthetic Greenhouse/Lever evidence and Workday saved-step analysis.
- Manual-tick continuous intake planning with FIFO deferred promotion and user-selected approval capacity.
- Current-tree and full-history privacy scanning, deterministic source candidates, isolated startup smoke and release-readiness reporting.
- Auto-cleaned synthetic product tour with AI/Claim conflicts, a local review packet and zero-action approval decisions.
- Explicit machine-readable gates for repository metadata, private vulnerability reporting, sanitized screenshots and clean-profile testing.

### Security / 安全

- Real website access, field modification, upload, submission, account creation, email, recruiter contact and real scheduling remain closed.
- Knowledge sources are read-only; private values remain outside the repository behind DPAPI `secure-ref` references.
- AI never automatically approves a personal Claim or converts missing facts into truth.
