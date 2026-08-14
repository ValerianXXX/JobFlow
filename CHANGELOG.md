# Changelog / 变更记录

All notable JobFlow changes are recorded here. The project follows semantic versioning; a section marked "release candidate" is not a published release.

所有重要 JobFlow 改动记录于此。项目遵循语义化版本；标记为“发布候选”的版本并不代表已经发布。

## [Unreleased]

- Added the fixed-ID JobFlow Browser Companion and a real, user-present company-site assistance path. For one approved application it revalidates the exact same-origin guest form, fills only approved fields, streams approved materials through one-use localhost tokens, and stops at `AWAITING_USER_SUBMIT`; no code path can click or invoke final submit.
- 新增固定 ID 的 JobFlow 浏览器伴侣与真实、用户在场的公司官网辅助路径：每次只处理一项已批准申请，重新核验精确同源访客表单，仅填写获批字段，并通过本机一次性令牌流式挂载获批材料；随后严格停在 `AWAITING_USER_SUBMIT`，代码中不存在点击或调用最终提交的路径。
- Added trusted-user-submit observation, strong success/failure result classification, a bilingual manual “Was it submitted successfully?” fallback, startup/kill-switch recovery to non-retryable `SUBMISSION_UNKNOWN`, append-only real-action auditing, and zero automatic retries.
- 新增可信用户提交观察、强成功/失败结果判断、双语“是否提交成功？”人工兜底，以及启动恢复/急停后收敛到不可自动重试的 `SUBMISSION_UNKNOWN`；真实检查、填写与上传采用追加式审计，自动重试固定为 0。
- Added a one-click Windows Browser Companion installer, bilingual UI controls, exact extension-origin CORS, least-privilege optional HTTPS access, and synthetic real-browser E2E coverage. No public recruiting site was visited during development or QA.
- 新增 Windows 一键浏览器伴侣安装入口、双语操作界面、精确扩展来源 CORS、最小化可选 HTTPS 权限与合成真实浏览器端到端验收；开发与 QA 未访问任何公开招聘网站。
- The synthetic product tour now displays a bilingual, field-by-field redacted prefill proposal and rehearses an approved application through isolated private-value resolution, temporary material staging, a separate fake final-confirmation gate and a verified synthetic receipt. The normal workspace rejects both demo-only endpoints, and browser, network, upload and real external actions remain zero.
- 合成产品体验现在会以中英双语逐字段显示脱敏预填提案，并把已批准的虚构申请演练到隔离私人值解析、临时材料暂存、独立假最终确认门和经验证的合成回执；正常工作区会拒绝两个演示专用接口，浏览器、网络、上传与真实外部动作仍均为 0。
- The isolated execution preflight now validates the complete scoped action set before consuming anything, then records one-use authorizations and hash-only envelopes for official-job read, form inspection, optional prefill and material upload in fixed order. A missing scope leaves both the use ledger and execution-run table untouched.
- 隔离执行预检现在会在消费任何动作前先验证完整授权范围，再按固定顺序记录官网岗位读取、表单检查、可选预填与材料上传的一次性授权及仅哈希信封；若范围缺失，使用账本与执行记录均保持未修改。
- Added a synthetic-only ephemeral ATS payload broker: it resolves approved encrypted fields only in memory, stages hash-bound materials outside the project, scrubs mutable buffers, removes temporary files before returning, and keeps production activation unavailable.
- 新增仅限合成测试的 ATS 临时载荷桥：仅在内存中解析已批准的加密字段，在项目外暂存哈希绑定材料，清零可变缓冲并在返回前删除临时文件；生产启用仍不可用。
- Added a bilingual, redacted execution-status board and isolated startup reconciliation. Review approval is never shown as submission, and interrupted or unknown runs visibly prohibit automatic retry.
- 新增中英双语脱敏执行状态板与本机启动恢复；审阅批准不会被误标为已提交，中断或结果未知会明确提示禁止自动重试。
- Stabilized rejected localhost uploads on Windows by draining only a small, explicitly sized request body under a short timeout before closing; ambiguous, chunked and oversized requests remain fail-closed.
- 修复 Windows 本机上传被拒绝时偶发的连接中止：关闭前仅在短超时内清理明确声明的小请求体；长度歧义、分块与超限请求仍保持失败关闭。
- Added one provider-neutral, hash-only ATS transport contract across company pages, Greenhouse, Lever and Workday. Raw private values and file content are forbidden, action authorization types are fixed, and live transports remain unregistered.
- 为公司官网、Greenhouse、Lever 与 Workday 新增统一的仅哈希 ATS 传输契约；禁止私人原值与文件正文进入信封，固定逐动作授权类型，且实时传输仍未注册。
- Added crash-consistent isolated execution reconciliation across final-authorization, fake-transport and receipt checkpoints; only an already persisted verified receipt can recover to confirmed, while every uncertain state becomes non-retryable `SUBMISSION_UNKNOWN`.
- 新增隔离执行的崩溃一致性恢复：覆盖最终授权、假传输与回执检查点；只有已持久化的可靠回执可恢复为已确认，其余不确定状态全部进入不可自动重试的 `SUBMISSION_UNKNOWN`。
- Integrated scoped prefill/upload authorization into the isolated execution chain. The hash-only fake upload adapter rejects filenames, paths, secure references and file bodies, and reports zero files opened/uploaded and zero network actions.
- 将预填/上传的精确授权接入隔离执行链；仅哈希的上传假适配器拒绝文件名、路径、secure-ref 与正文，并报告打开/上传文件 0、网络动作 0。
- Added expiring, one-use, per-action authorization sessions for live-read, form-inspection, prefill and upload contracts, with a generation-bound global emergency stop visible in the bilingual dashboard. Production activation remains structurally disabled.
- 新增实时读取、表单检查、预填和上传的限时逐动作单次授权会话，并在双语首页显示绑定代际的总急停；生产动作仍在结构上保持不可启用。
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
