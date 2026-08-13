# Changelog / 变更记录

All notable JobFlow changes are recorded here. The project follows semantic versioning; a section marked "release candidate" is not a published release.

所有重要 JobFlow 改动记录于此。项目遵循语义化版本；标记为“发布候选”的版本并不代表已经发布。

## [Unreleased]

- Added an append-only isolated application execution controller that proves the complete dual-approval and receipt lifecycle without a browser, network call, upload, email, account, scheduler or real external side effect.
- 新增仅含哈希的追加式隔离投递控制器；完整验证双重批准与回执链，且不调用浏览器、网络、上传、邮件、账号、调度或任何真实外部动作。
- Review approval and final submission authorization are now cryptographically separate. The final gate expires within 1–30 minutes, binds the current execution plan, packet, route, form, uploads and freshness evidence, rejects stale/replayed consent, and is consumed atomically with the review approval in isolated tests; production transport remains disabled.
- 审阅批准与最终提交授权现在在技术上完全分离。最终门禁仅在 1—30 分钟内有效，绑定当前执行计划、审阅包、路线、表单、材料与新鲜度证据，拒绝过期、内容变化或重放，并在隔离测试中与审阅批准原子消费；生产传输仍关闭。
- The bilingual home page can now prepare one real-profile offline application from three explicitly selected saved files. It constructs the company/ATS route locally, retains no input snapshot, opens the encrypted review packet, and rolls back both queue capacity and newly generated ciphertext if preparation fails; network and real external actions remain zero.
- 中英双语首页现在可以从三份用户明确选择的本机文件准备一项真实资料离线申请：本机自动建立公司官网/ATS 路线，不保留输入快照，生成后直接打开加密审阅包；如中途失败，会同时释放队列容量并删除本轮新建密文，网络与真实外部动作仍为 0。
- A completed non-synthetic onboarding profile can now drive the same local application pipeline as the demo: approved Claims are selected against a saved JD, only applicant-approved Master Resume positions are changed in a copy, Word renders the result for structural/visual QA, and the encrypted packet enters `AWAITING_APPROVAL`. Live transport remains absent.
- 已完成的真实用户资料现在可以进入与演示相同的本机投递流水线：系统按已保存 JD 选择获批 Claim，只在用户批准的母版位置生成副本改写，经 Word 实际渲染与结构/视觉检查后，将加密审阅包放入 `AWAITING_APPROVAL`；实时传输仍不存在。
- Ordinary DOCX Master Resumes now receive AI-to-paragraph mapping and a bilingual one-time approval screen. Only applicant-approved, hash-bound positions can be changed in a copy; the encrypted manifest persists no resume paragraph text and any onboarding revision invalidates it.
- 普通 DOCX 母版现在支持 AI 到原段落的映射与中英双语一次性确认；只有用户批准且哈希绑定的位置可在副本中改写，加密清单不保存简历段落正文，资料建新版后旧映射自动失效。
- A resume uploaded through onboarding can now be designated as the encrypted Master Resume, while application-material use of confirmed Claims requires a separate, hash-bound applicant approval. The bilingual readiness board shows every remaining local blocker and automatically invalidates that approval after a revision.
- 通过入职页上传的简历现在可以成为加密 Master Resume；已确认 Claim 用于简历、求职信或申请回答前，还必须取得一份与当前文字哈希绑定的单独申请人授权。双语准备度面板会列明所有本地阻挡项，建新版后旧授权自动失效。
- Offline official-careers discovery now auto-detects saved Greenhouse job JSON and Lever posting JSON, while preserving the same no-network, no-queue-mutation and live-freshness-required boundary.
- 离线官网找岗现在可自动识别已保存的 Greenhouse 岗位 JSON 与 Lever 职位 JSON，同时继续保持不联网、不修改队列且必须实时复验的边界。
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
