# 找工流水线（JobFlow）

JobFlow（内部工程代号 JobOps）是本地优先、证据驱动的求职工作流。它把离线岗位资料处理到可审阅的 `AWAITING_APPROVAL`，然后继续处理其他岗位，直到用户设置的待审批上限。真实网站预填、上传、提交、账号创建、邮件、招聘者联系和系统定时任务均未授权、未注册、不可运行。

## 找工流水线 / JobFlow

### Windows 一键安装与启动

最简单的 Windows 使用方式：第一次双击 `Install JobFlow.cmd`，以后双击 `Start JobFlow.cmd`。不需要在 WSL 中运行，也不需要输入项目路径。

如果希望使用 Windows PowerShell，则在项目根目录运行：

```powershell
.\scripts\install-jobflow.ps1
.\scripts\start-jobflow.ps1
```

以后只需运行第二条命令。安装内容位于项目内被 Git 忽略的 `.venv`，私人资料仍只进入 `%LOCALAPPDATA%\JobOps\private`。运行状态、数据库、审阅包与本机发布报告全部被公开仓库边界排除。同一 Windows 用户只能启动一个交互服务，避免两个页面同时改写加密草稿。

开发者也可以直接运行以下兼容命令；它会在 `127.0.0.1` 打开仅限本机的入职页面：

```powershell
python .agents/skills/job-application-operator/scripts/jobops.py onboarding-center
```

页面支持中文和 English 即时切换，切换时不会丢失尚未保存的页面内容。它提供三个入口：简历/项目/补充材料、ChatGPT 官方导出或 AI 总结、以及一次性的 25 项完整问卷。AI 资料类型中包含普通官方导出和 >200 MB 的“雷霆大文件 / ZIPzilla Express”；大型 ZIP 最高支持 8 GB，采用本机流式接入，不会整体装入 Python 内存。上传材料会先进入本机加密的提取预览；只有用户勾选并确认的完整陈述才会成为 Claim，标题、页码、表头和断裂文本不会直接进入审阅列表。填写具体值会自动标记为已确认，也可直接选择不适用或不愿披露；草稿、Candidate Profile、Answer Bank、Claim 决定与冲突处理均进入 Windows DPAPI。ChatGPT 官方导出的原始 ZIP 不保留；大型导出的临时副本只存在于 OneDrive 外的一次性私有 staging，成功或失败都会清理。可用 `onboarding-status` 查看仅含数量与状态的脱敏进度。

冲突区位于 Claim 长列表之前，会标出受影响字段或 Claim、冲突原因，并并列展示简历/知识证据或多个资料来源的候选值。首次加载、上传解析、问卷保存、审阅保存和最终完成时，右下角会持续显示当前阶段、动态剩余时间与进度条，操作结束或报错后才消失。

Claim 审阅支持直接改写、修改分类、删除/恢复、合并与拆分，并保留来源关系。`ONBOARDING_COMPLETE` 是不可覆盖的历史快照；页面会明确显示只读状态，并通过“建立可编辑新版本 / Create editable revision”生成新的 DPAPI 状态与 Answer Bank，旧 Profile、Answer Bank、审阅决定和完成包保持不变。建版入口是幂等的：若新版本已创建但页面尚未同步，再次点击只会刷新到现有可编辑版本，不会继续生成第三版。既有旧材料可在新版本中使用“重新提取 / Re-extract”进入新的预览流程。

JobFlow 的资料理解采用严格 AI 门：选择文件后会自动启动 AI 分析；“一键分析全部资料”可依次重新分析所有仍保留加密原件的来源。简历和项目材料先由 AI 重建换行、归并唯一经历实体、区分正式工作/实习/教育/项目，再输出带原文行号的完整 Claim。官方 ChatGPT 导出会扫描完整 `conversations.json`，从全量用户消息中选择具有个人事实信号的代表性内容交给 AI；不会把提问、假设、粘贴的 JD 或助手回答自动当作个人经历。重复实体、句子碎片、无来源数字和不合格 provenance 会使整次 AI 结果失败关闭；AI 不会自动批准或对外使用 Claim。未配置 AI 时，上传分析暂停且输出 0 个 Claim，不再提供规则拆分回退。历史规则结果会被隔离，不进入 Profile、审批包或后续申请。ChatGPT 官方导出的原始 ZIP 不保留，因此旧导出需要重新上传后分析。上传进度按本机实际传输速度计算；进入模型阶段后显示通常时间范围和已用时间，不会在倒计时到期后反复“续秒”。上传来源可由用户明确确认后删除，系统会级联移除该来源的建议、直接 Claim、派生 Claim 和 DPAPI 密文；经过 AI 验证的预览支持一键纳入全部 Claim。

若模型首轮输出因偶发的碎片、漏句号、错误行号或不受支持内容未通过验证，JobFlow 会在同一私密零工具通道内要求 AI 完整重做一次，并对替代结果重新执行全部真实性校验。只有第二遍通过才展示预览；仍不合格时本次导入保持为 0，并显示中英文可操作说明。系统不会机械补句号、擅自扩大引用行或降低门槛。

首页提供“连接 AI / Connect AI”入口，不要求用户再次填写 API Key。选择“已有 Agent”后，系统会在有限本机范围自动检测 Windows 或 WSL 中的 Hermes 与 OpenClaw，并复用 Agent 当前已经选好的模型；选择“本地大模型”后，会自动检测 Windows 或 WSL 回环地址上的 Ollama、LM Studio、LocalAI、llama.cpp 或 vLLM，并选用已加载的非嵌入模型。WSL 发行版只在连接当次自动发现，不写入项目或偏好；若 Hermes 当前模型或登录失效，系统会提示用户回到 Hermes 确认，而不会代为登录。WSL Hermes 只读取公开的当前模型与提供商标识；包括 OpenAI Codex 在内的已配置提供商都通过 Hermes 自身运行时连接，不再依据其他未登录提供商的状态误判。每次私人请求仅经 stdin 进入自动清理的 WSL 临时目录，并强制空工具集、单轮模型调用、无会话库、无记忆、无上下文文件和无检查点。WSL 服务只绑定/访问 `127.0.0.1`，不探测 WSL IP、不开放局域网端口；经 WSL `curl` 传递的私人 JSON 也只进入 stdin。JobFlow 不提取、复制或保存 Agent 的 Cookie、Token、API Key 与配置路径；选择结果只以不含凭据、发行版名和可执行路径的安全偏好保存在 `%LOCALAPPDATA%\JobOps`。Agent 私人请求只经 stdin、一次性隔离工作区或本机回环传递，私人值不进入命令参数。对 Windows/WSL OpenClaw，JobFlow 只读取其只读状态命令公开的已解析模型标识，不保存状态中的路径或认证元数据；随后每次请求使用自动清理的空白工作区和最小工具配置，禁用文件、命令、网络、浏览器、消息、自动化、节点、媒体及插件工具。Hermes 与 OpenClaw 返回的工具审计只要不是 0 次，整次分析即被拒绝。使用 Agent 时，资料最终流向由该 Agent 当前配置的本地或云端模型决定；使用本地模型时，JobFlow 只接受 `127.0.0.1`、`localhost` 或 `::1`。高级企业或自定义适配器继续保留 `JOBOPS_AI_COMMAND_JSON` 兼容入口。

当前发布状态：

- `PHASE_0_4_HARDENED`
- `PHASE_4_5_SECURE_ONBOARDING_READY`
- `BILINGUAL_ONBOARDING_CENTER_READY`
- `PHASE_5_6_OFFLINE_ENGINEERING_COMPLETE_NOT_AUTHORIZED_NOT_OPERATIONAL`
- `PHASE_5_6_AUTHORIZATION=ABSENT`

## 安全边界

- 四个知识来源只读；每次发布复验指纹和写操作数。
- 私人资料以 `secure-ref:*` 进入 Windows DPAPI 存储，默认位于 `%LOCALAPPDATA%\JobOps\private`；项目数据库只保留不透明引用和非敏感元数据。
- 只有来源文件、标题、片段和哈希均通过 Knowledge Gateway 复验的已批准 Claim 能进入材料。
- 招聘入口必须来自公司 HTTPS 官网。官网导航到 Workday、Greenhouse 或 Lever 时，还要验证公司、租户、board、岗位、官方页面和 JD 快照的绑定。
- `discover-official-jobs` 只分析用户保存到项目内的公司招聘页 HTML/JSON 快照（local snapshot only）。它可提出公司官网、Workday、Greenhouse 或 Lever 岗位候选，但不联网、不确认岗位仍开放，也不把候选视为已验证路线；每条结果都必须在以后取得单独授权后重新做实时新鲜度与路线验证。 / It parses only a project-local saved careers-page snapshot, performs zero network actions, and leaves every candidate pending a separately authorized live freshness and route check.
- `analyze-ats-form` 只读取项目内的本地 HTML 表单快照，并要求已经验证且哈希完整的官网→ATS 路线。它丢弃页面中已有的所有输入值，只输出字段/问题哈希、分类、停止原因和 opaque control reference；CAPTCHA、MFA、登录、账号、上传、跨域表单/iframe、敏感题、未知字段及最终提交全部保持关闭。由此生成的 browser action plan 只允许普通固定字段或 `secure-ref` 私人字段成为待审阅的 prefill proposal，当前适配器不会真正修改任何网页。 / The form analyzer is local-snapshot-only, value-redacting and fail-closed; its current adapter validates plans but performs zero browser actions.
- Greenhouse 已有一条完整的合成离线纵向验收链：公司官网快照 → 官方到 Greenhouse 的租户/岗位路线 → JD 与 HTML 表单 → opaque/哈希化字段绑定 → 零修改的本机浏览器计划验证 → 材料渲染 QA → `AWAITING_APPROVAL` 审阅队列。路线、表单、计划、材料与审阅包由同一上下文哈希绑定；上传与提交仍为关闭能力。这是离线工程证据，不代表已连接或兼容真实 Greenhouse 网站。 / A synthetic Greenhouse vertical reaches the local review queue with one content-bound context and zero browser or external actions; it is not a claim of live-site compatibility.
- Workday 已支持离线的多步骤保存页序列分析。`analyze-ats-sequence` 会把 1—20 个项目内 HTML 快照按顺序绑定到同一路线，识别个人信息、经历/教育、申请问题、自愿披露、Review 与账号/登录页面；动态 DOM ID 可以变化，但相同逻辑问题用独立逻辑哈希去重。Next/Continue、账号、登录、CAPTCHA、MFA、上传和 Submit 都仍是 STOP，报告明确 `navigation_performed=false`。 / Saved Workday steps can be analyzed and deduplicated offline, but JobFlow performs no navigation and makes no live-compatibility claim.
- Lever 已通过相同的单页本地快照、opaque 字段计划和零修改适配器契约测试。`ats-capabilities` 会精确列出 company/Greenhouse/Lever/Workday 当前仅由合成证据支持到哪一层，并对每份能力声明做内容哈希；所有条目固定 `live_site_verified=false`，避免把离线测试误写成真实站点兼容。 / Lever reuses the same safe offline contract, while `ats-capabilities` distinguishes synthetic evidence from unverified live support.
- 待审批上限默认 10，可设 1—1000；容量检查与 reservation 在同一事务中完成。
- `ExternalActionGateway` 是受保护状态的唯一入口。生产策略始终先返回 `PHASE_NOT_AUTHORIZED`。
- `SUBMISSION_UNKNOWN` 不自动重试；CAPTCHA、MFA、验证码、登录和账号创建均停给用户。

## 合成端到端快速开始

在项目根目录运行：

```powershell
python .agents/skills/job-application-operator/scripts/jobops.py migrate-db
python .agents/skills/job-application-operator/scripts/jobops.py secure-onboard --synthetic
python .agents/skills/job-application-operator/scripts/jobops.py secure-store-status
```

把 `secure-store-status` 输出的三个引用代入：

```powershell
python .agents/skills/job-application-operator/scripts/jobops.py run-to-awaiting-approval `
  --input tests/fixtures/synthetic-forward-jd.txt `
  --profile-ref secure-ref:PROFILE_REFERENCE `
  --master-resume-ref secure-ref:MASTER_REFERENCE `
  --answer-bank-ref secure-ref:ANSWER_REFERENCE `
  --synthetic
python .agents/skills/job-application-operator/scripts/jobops.py list-pending
```

该链会实际完成本地导入、快照、去重、Schema、JD、硬条件、Fit、离线研究、Claim 复验、母版副本、DOCX/PDF、渲染 QA、模拟表单、Review Packet、队列和数据库事件，但不会联网或执行外部动作。验收后清理合成私人数据：

```powershell
python .agents/skills/job-application-operator/scripts/jobops.py purge-synthetic-private-data
```

## 公开 CLI

运行 `python .agents/skills/job-application-operator/scripts/jobops.py --help` 查看命令。核心分组包括：

- 环境与状态：`audit`、`locate`、`status`、`init-db`、`migrate-db`、`verify-release`
- 私人资料：`onboarding-center`、`onboarding-status`、`secure-onboard`、`secure-onboard-resume`、`finalize-resume-onboarding`、`review-onboarding`、`secure-import-master-resume`、`secure-import-answer-bank`、`secure-store-status`、`purge-synthetic-private-data`
- Claim：`propose-claims`、`list-claim-proposals`、`approve-claim`、`reject-claim`、`revoke-claim`、`revalidate-claims`
- 岗位与队列：`ats-capabilities`、`discover-official-jobs`、`verify-route`、`analyze-ats-form`、`analyze-ats-sequence`、`import-jd`、`analyze-job`、`run-to-awaiting-approval`、`run-queue`、`queue`、`list-pending`
- 审阅与恢复：`show-review-packet`、`revise-application`、`approve-review-packet`、`reject-review-packet`、`resume-blocked`、`retry-safe-step`、`explain`

## 真实使用前仍需的最小输入

用户可通过 `secure-onboard-resume` 从经授权的 Downloads 范围安全接入简历，并由系统建立 Candidate Profile、Answer Bank 和 Claim 审阅草稿。只有 PDF 时会明确保留 `EDITABLE_MASTER_DOCX_MISSING`，仍需补充可编辑 DOCX 才能做版式保持的母版修改。所有个人 Claim 都要逐条审阅。即使以后另行授权 Phase 5—6，仍应逐岗位检查站点条款，并在最终提交前要求新鲜的精确批准；当前版本没有真实传输适配器。

架构、审批绑定、敏感字段、恢复、私人接入和发布证据详见现有 Skill 的一层 `references/` 与 `reports/`。
