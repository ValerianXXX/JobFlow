# 找工流水线（JobFlow）

JobFlow（内部工程代号 JobOps）是本地优先、证据驱动的求职工作流。它把岗位资料处理到可审阅的 `AWAITING_APPROVAL`，然后继续处理其他岗位，直到用户设置的待审批上限。第二版支持用户在场、逐岗位授权的公司官网与已绑定 ATS 多页辅助：逐页读取表单结构、填写已批准字段、挂载已批准材料，并仅在页面校验通过后以一次性授权点击明确的非最终 Next/Continue。登录、验证与未知问题交还用户，最终 Submit 永远由用户亲自点击。账号创建、自动重试、邮件、招聘者联系、无人值守投递和系统定时任务均不可运行。 / JobFlow v0.2 adds user-present, per-application multi-page assistance for bound company and ATS routes. It fills and attaches only approved data, uses a fresh one-use authorization for one explicit non-final Next/Continue control per page, and always leaves final Submit to the user.

> AI 负责理解与归并，证据门负责约束真实性，用户逐岗位批准预填与上传并亲自提交；JobFlow 不实现最终提交或自动重试。
> AI understands and consolidates, evidence gates constrain truthfulness, and the user approves each assisted fill/upload and personally submits; JobFlow implements neither final submit nor automatic retry.

[快速开始 / Quick start](docs/quickstart.md) · [架构 / Architecture](docs/architecture.md) · [路线图 / Roadmap](docs/roadmap.md) · [发布清单 / Release checklist](docs/release-checklist.md) · [变更记录 / Changelog](CHANGELOG.md) · [安全 / Security](SECURITY.md) · [参与贡献 / Contributing](CONTRIBUTING.md) · [MIT License](LICENSE)

| 能力 / Capability | 当前证据 / Current evidence | 真实动作 / Real action |
|---|---|---|
| 简历、项目与 AI 导出 / Resume, projects and AI exports | 本机加密接入、严格 AI 提取、人工 Claim 审阅 / Encrypted local intake, strict AI extraction, human Claim review | 0 |
| Candidate Profile 与 Answer Bank | DPAPI 版本化草稿与一次性确认 / Versioned DPAPI drafts and one-time confirmation | 0 |
| 公司官网找岗 / Company-careers discovery | 仅已保存快照；候选仍需实时复验 / Saved snapshots only; candidates still require live verification | 0 |
| Greenhouse、Lever、Workday | 合成/已保存页面安全证据，不声称实时兼容 / Synthetic or saved-page safety evidence, no live claim | 0 |
| 连续处理 / Continuous processing | 人工触发批次、FIFO、待审批上限 / Manual ticks, FIFO, bounded approvals | 0 |
| 公司官网与已绑定 ATS 多页辅助 / Multi-page company and bound-ATS assist | 用户在场、逐岗位授权、逐页会话轮换 / User-present, per-application approval, per-page session rotation | 获批字段、材料与明确的非最终 Next/Continue；最终 Submit 锁定 / Approved data plus scoped non-final navigation; final Submit locked |
| 最终提交、账号、邮件、招聘者联系 / Final submit, account, email, recruiter contact | 不实现且失败关闭 / Not implemented and fail-closed | 0 |

## 找工流水线 / JobFlow

### Windows 一键安装与启动

最简单的 Windows 使用方式：第一次双击 `Install JobFlow.cmd`，以后双击 `Start JobFlow.cmd`。不需要在 WSL 中运行，也不需要输入项目路径。

若要从公司官网读取岗位并在审阅后辅助填写，每台电脑只需安装一次浏览器伴侣：双击 `Install JobFlow Browser Companion.cmd`，在打开的 Edge/Chrome 扩展页开启“开发人员模式”，选择“加载解压缩的扩展”，再选择脚本已为你打开的本机 Local AppData `BrowserCompanion` 运行目录（不要选择项目源码目录）。确认扩展版本为 `0.6.1`。安装器会生成仅存在于本机的安装级绑定证明；项目、Git 和源码 ZIP 都不包含该秘密。岗位导入阶段只读取用户主动选择的公司岗位页可见文字与申请表结构；它不读取 Cookie、Token、密码、验证码或现有输入值，不填写、不上传、不点击网页按钮。选错网址时可直接点击“取消本次读取并更换网址”，释放本机授权与浏览器伴侣绑定。只有审阅包另行批准后，才会进入获批预填与材料挂载；最终 Submit 没有实现。 / Install the Browser Companion once per computer, then load the Local AppData `BrowserCompanion` runtime folder opened by the installer—not the project source folder—and confirm version `0.6.1`. The installer creates an installation-specific local binding secret that is never included in the project, Git, or source ZIP. Guided import reads only visible role text and sanitized form structure; it never reads cookies, tokens, credentials, verification codes, or existing input values and performs no fill, upload, or page click. If the wrong URL was selected, use “Cancel this read and choose another URL” to release the local authorization and companion binding. Final Submit is not implemented.

真实使用路径：先完成资料、Claim 和具体审阅包审批，再对这一项申请启动 30 分钟的浏览器辅助。浏览器伴侣只在已绑定的公司/Greenhouse/Lever/Workday 来源内逐页工作；每一页都重新分析结构、解析可安全复用值、附加该页要求的获批材料，并在表单有效时用一次性令牌通过一个明确的非最终 Next/Continue。登录、已有账号验证、CAPTCHA、MFA、法律/签名与未知答案会进入人工接管，扩展不读取凭据。到 `AWAITING_USER_SUBMIT` 后必须由用户亲自点击最终 Submit；结果无法可靠判断时只询问“是否提交成功？”，绝不自动重试。 / After profile, Claim, packet, and one-application approval, the companion processes each page on the bound company/Greenhouse/Lever/Workday origin. Every page is reclassified and gets a rotated authorization session. Login, verification, legal/signature, and unknown-answer steps are handed to the user without credential access. Only the user can click final Submit, and unknown outcomes are never retried.

如果启动异常或页面显示 `Failed to fetch`，先双击 `Check JobFlow.cmd`。它只检查公开程序组件与安全策略，不联网、不读取私人资料，也不会显示用户路径；按第一条失败项给出的中英文提示修复即可。 / If startup fails, run `Check JobFlow.cmd` for a redacted, offline health check and follow the first failed item.

想先体验而不接触真实简历时，安装后双击 `Start JobFlow Demo.cmd`。该入口只运行虚构资料，使用自动清理的临时数据库与 DPAPI 目录；文件上传和真实 AI 连接均被服务端拒绝。虚构审阅包会逐字段显示准备预填的来源类型，批准后可运行到假的最终提交门，再通过第二次明确确认生成可靠合成回执；浏览器、网络、文件上传和真实外部动作始终为 0，关闭窗口后演示状态即删除。/ To tour JobFlow without real data, run `Start JobFlow Demo.cmd`; it shows a field-by-field redacted prefill proposal and can rehearse the approved application through a separate fake final-confirmation gate and verified synthetic receipt. The auto-cleaned runtime rejects file intake and real AI connections, and browser, network, upload, and real external actions remain zero.

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

页面支持中文和 English 即时切换，切换时不会丢失尚未保存的页面内容。它提供三个入口：简历/项目/补充材料、ChatGPT 官方导出或 AI 总结、以及一次性的 25 项必答问卷；另有 GitHub 与作品集公开链接两个可选字段，不会阻塞入职完成。资料区也可把作品集文件作为独立加密来源接入。AI 资料类型中包含普通官方导出和 >200 MB 的“雷霆大文件 / ZIPzilla Express”；大型 ZIP 最高支持 8 GB，采用本机流式接入，不会整体装入 Python 内存。上传材料会先进入本机加密的提取预览；只有用户勾选并确认的完整陈述才会成为 Claim，标题、页码、表头和断裂文本不会直接进入审阅列表。填写具体值会自动标记为已确认，也可直接选择不适用或不愿披露；草稿、Candidate Profile、Answer Bank、Claim 决定与冲突处理均进入 Windows DPAPI。ChatGPT 官方导出的原始 ZIP 不保留；大型导出的临时副本只存在于 OneDrive 外的一次性私有 staging，成功或失败都会清理。可用 `onboarding-status` 查看仅含数量与状态的脱敏进度。 / The bilingual setup has 25 required answers plus optional GitHub and portfolio links; a portfolio file can also be retained as a separate encrypted source without blocking completion when absent.

冲突区位于 Claim 长列表之前，会标出受影响字段或 Claim、冲突原因，并并列展示简历/知识证据或多个资料来源的候选值。首次加载、上传解析、问卷保存、审阅保存和最终完成时，右下角会持续显示当前阶段、动态剩余时间与进度条，操作结束或报错后才消失。

Claim 审阅支持直接改写、修改分类、删除/恢复、合并与拆分，并保留来源关系。`ONBOARDING_COMPLETE` 是不可覆盖的历史快照；页面会明确显示只读状态，并通过“建立可编辑新版本 / Create editable revision”生成新的 DPAPI 状态与 Answer Bank，旧 Profile、Answer Bank、审阅决定和完成包保持不变。建版入口是幂等的：若新版本已创建但页面尚未同步，再次点击只会刷新到现有可编辑版本，不会继续生成第三版。既有旧材料可在新版本中使用“重新提取 / Re-extract”进入新的预览流程。

JobFlow 的资料理解采用严格 AI 门：选择文件后会自动启动 AI 分析；“一键分析全部资料”可依次重新分析所有仍保留加密原件的来源。PDF 会先在本机比较逻辑阅读顺序与空间版面两种提取结果，并按乱码、私用字体字符、碎片化、重复页眉和文字密度选择更可靠的一份；两种结果都不可用时，会在任何 Claim 或原件入库前提示 OCR/可编辑 DOCX。数字依据允许可证明等价的显示差异，例如 `4,000`/`4000`、数字分组空格、全角数字和无意义的小数尾零；同一句因 PDF 物理换行而拆开的引用最多只扩展相邻两行，且不能跨空行、项目符号或完整句号。AI 已输出且原文可证实的结构问题——已知类型别名、明确的实习标签、同一实体重复键、父子类别不一致，以及被 Word/PDF 拆到相邻两行的实体标题——会被安全归并并以橙色提示保持未选中，交由用户重点确认；系统不会机械改写 Claim、数字、日期、责任或成果。无法唯一对应的实体、句子碎片、无来源数字和不合格 provenance 仍会使整次结果失败关闭。简历和项目材料由 AI 重建换行、归并唯一经历实体、区分正式工作/实习/教育/项目，再输出带原文行号的完整 Claim。官方 ChatGPT 导出会扫描完整 `conversations.json`，从全量用户消息中选择具有个人事实信号的代表性内容交给 AI；不会把提问、假设、粘贴的 JD 或助手回答自动当作个人经历。AI 不会自动批准或对外使用 Claim。未配置 AI 时，上传分析暂停且输出 0 个 Claim，不再提供规则拆分回退。历史规则结果会被隔离，不进入 Profile、审批包或后续申请。ChatGPT 官方导出的原始 ZIP 不保留，因此旧导出需要重新上传后分析。上传进度按本机实际传输速度计算；进入模型阶段后显示通常时间范围和已用时间，不会在倒计时到期后反复“续秒”。上传来源可由用户明确确认后删除，系统会级联移除该来源的建议、直接 Claim、派生 Claim 和 DPAPI 密文；经过 AI 验证的预览支持一键纳入全部 Claim。 / PDF intake keeps unsupported facts fail-closed while safely normalizing only grounded structural differences and visibly requiring user review.

若模型首轮输出因偶发的碎片、漏句号、错误行号或不受支持内容未通过验证，JobFlow 会在同一私密零工具通道内要求 AI 完整重做一次，并对替代结果重新执行全部真实性校验。只有第二遍通过才展示预览；仍不合格时本次导入保持为 0，并显示中英文可操作说明。系统不会机械补句号、擅自扩大引用行或降低门槛。

首页提供“连接 AI / Connect AI”入口，不要求用户再次填写 API Key。选择“已有 Agent”后，系统会在有限本机范围自动检测 Windows 或 WSL 中的 Hermes 与 OpenClaw，并复用 Agent 当前已经选好的模型；选择“本地大模型”后，会自动检测 Windows 或 WSL 回环地址上的 Ollama、LM Studio、LocalAI、llama.cpp 或 vLLM，并选用已加载的非嵌入模型。“AI 已连接”不再由简单握手决定：每次选择或恢复连接都必须先通过不含私人资料的完整结构化能力测试，证明模型能归并实体、保留数字并给出可验证行号；只能返回简单 JSON 的模型会显示为能力未通过，文件上传不会开放。WSL 发行版只在连接当次自动发现，不写入项目或偏好；若 Hermes 当前模型或登录失效，系统会提示用户回到 Hermes 确认，而不会代为登录。WSL Hermes 只读取公开的当前模型与提供商标识；包括 OpenAI Codex 在内的已配置提供商都通过 Hermes 自身运行时连接，不再依据其他未登录提供商的状态误判。每次私人请求仅经 stdin 进入自动清理的 WSL 临时目录，并强制空工具集、单轮模型调用、无会话库、无记忆、无上下文文件和无检查点。WSL 服务只绑定/访问 `127.0.0.1`，不探测 WSL IP、不开放局域网端口；经 WSL `curl` 传递的私人 JSON 也只进入 stdin。JobFlow 不提取、复制或保存 Agent 的 Cookie、Token、API Key 与配置路径；选择结果只以不含凭据、发行版名和可执行路径的安全偏好保存在 `%LOCALAPPDATA%\JobOps`。Agent 私人请求只经 stdin、一次性隔离工作区或本机回环传递，私人值不进入命令参数。对 Windows/WSL OpenClaw，JobFlow 只读取其只读状态命令公开的已解析模型标识，不保存状态中的路径或认证元数据；随后每次请求使用自动清理的空白工作区和最小工具配置，禁用文件、命令、网络、浏览器、消息、自动化、节点、媒体及插件工具。Hermes 与 OpenClaw 返回的工具审计只要不是 0 次，整次分析即被拒绝。使用 Agent 时，资料最终流向由该 Agent 当前配置的本地或云端模型决定；使用本地模型时，JobFlow 只接受 `127.0.0.1`、`localhost` 或 `::1`。高级企业或自定义适配器继续保留 `JOBOPS_AI_COMMAND_JSON` 兼容入口。 / A connection is shown as ready only after the selected model passes the same structured entity, metric and line-grounding contract used for documents.

原生 Windows Hermes 会从当前进程可见路径以及官方 `%LOCALAPPDATA%\hermes\hermes-agent` 安装位置独立发现，不依赖启动 JobFlow 时继承的 `PATH` 是否已经刷新。连接优先通过零工具 stdin 通道调用 Hermes 当前已选择的 provider/model；`hermes proxy` 只作为旧环境的兼容回退，不是用户必须启动的服务。WSL 自动发现同时识别 `venv` 与 `.venv`。JobFlow 不提取、不回传、不记录也不持久化 API Key、Token、Cookie 等凭据值；Hermes 仅在隔离子进程内自行解析其已配置的 provider。 / Native Windows Hermes is discovered both from the current process environment and its official Local AppData installation, even when inherited `PATH` is stale. JobFlow uses the currently selected provider/model through a zero-tool stdin channel; `hermes proxy` is compatibility fallback only. WSL discovery supports both `venv` and `.venv`. JobFlow does not extract, return, log or persist credential values; Hermes resolves its configured provider only inside the isolated child process.

每个岗位的材料都从同一份已批准、不可变的 Master Resume 生成岗位专用副本；通过入职页接入的 DOCX 简历会被明确登记为加密母版，PDF 只作为不可编辑参考。确认一条 Claim 并不等于允许它出现在申请材料中：完成资料后，用户还需在“自动投递准备度”区域一次性批准当前精确措辞用于简历、求职信和申请回答；授权与母版哈希、Profile、当前资料版本和 Claim 文字绑定，任何修改都会使旧授权失效。普通 DOCX 不要求用户手工插入占位符：JobFlow 会把 AI 已确认 Claim 映射回原简历段落，用户批准后只允许岗位副本在哈希绑定位置改写，母版始终不变。多页路线会在进入首个表单前准备同一母版派生的岗位简历及可能在后续页要求的 Cover Letter/作品集，只有出现对应控件时才实际挂载；GitHub、作品集 URL 与个人网站只从已确认 Profile 安全复用。最终 Submit 始终由用户完成。 / Every job-specific resume comes from the same immutable approved master. For multi-page routes, later-page resume, Cover Letter, portfolio, GitHub, and website requirements are anticipated, but a material/value is attached only when a matching control appears. Final Submit remains the user's action.

每份待审批申请还会生成一份不可静默更改的六步“自动投递步骤”：确认岗位与页面、以访客方式进入、安全字段预填、上传岗位材料、处理敏感/未知问题、用户最终提交。审阅批准本身不会触发网站动作；开始浏览器辅助还必须取得一张 30 分钟内有效、逐岗位、一次性且与当前页面、表单、材料和审阅包绑定的用户在场授权。浏览器伴侣完成真实预填和上传后停在 `AWAITING_USER_SUBMIT`，不包含点击最终提交的代码。用户提交后，强证据才形成确认回执；明确失败会退回审批，不确定或进程中断会进入不可自动重试的 `SUBMISSION_UNKNOWN`。某一岗位停在用户面前时，队列仍可继续准备其他岗位，直到达到用户设置的待审批数量上限。 / Every packet has a tamper-evident six-step plan. A separate short-lived, one-use, per-application user-present approval binds the exact page, form, materials and packet. Real fill/upload stops at `AWAITING_USER_SUBMIT`; only the user's trusted click can begin result observation, and uncertain or interrupted outcomes become non-retryable `SUBMISSION_UNKNOWN`.

真实表单检查、安全预填和材料上传不会共用一个模糊的总授权：每项申请都使用限时、逐动作、单次会话，并在执行前完整核验所需范围。最终提交、账号、邮件和调度不能混入这类会话。首页安全面板提供可立即使全部辅助会话失效的双语急停；急停或进程中断发生在等待用户提交期间时，只能进入结果未知，不能重新发送。 / Real form inspection, safe fill and material attachment use an expiring, one-use, per-application session with complete preflight. Final submit, accounts, messaging and scheduling cannot be included. The bilingual emergency stop revokes all assist sessions; interruption during the submit window becomes unknown and cannot resend.

首页“自动投递执行状态”只显示脱敏的岗位摘要、运行编号、最近检查点、状态与下一步。审阅批准不会被显示成已提交；`SUBMISSION_UNKNOWN` 与中断恢复会用醒目状态提示人工核验，并明确显示禁止自动重试。JobFlow 每次本机服务启动时会先检查隔离执行链是否在最终授权后中断：已有可靠回执则恢复为已确认，否则收敛为结果未知，不会重新发送。 / The home-page execution board shows only safe summaries, checkpoint state, and the next action. Approval is never mislabeled as submission; interrupted or unknown runs are visibly marked for manual verification and automatic retry is prohibited. Local startup reconciles isolated interrupted runs from persisted evidence without resending.

隔离纵向测试现在会在最终确认之前消费精确的预填/上传会话。上传假适配器只接收用途与 SHA-256，不接收文件名、路径、secure-ref 或文件正文，并明确报告打开文件 0、上传文件 0、网络动作 0；它验证的是未来传输接口和顺序，而不是伪装成真实上传。 / The isolated vertical now consumes exact prefill/upload scopes before final confirmation. Its fake upload adapter accepts purpose and SHA-256 only, opens and uploads zero files, and validates sequencing without pretending a real upload occurred.

Company、Greenhouse、Lever 与 Workday 共用提供商中立、仅哈希的动作契约；唯一的窄范围真实传输是 `browser_companion`，且必须用户在场、逐申请授权。每一页轮换动作会话，私人值仅在本机内存解析，文件以一次性字节流传给固定扩展，普通数据库和日志只保存哈希与动作结果。唯一的程序化点击被限制为经页内校验和一次性授权的明确非最终 Next/Continue；最终 Submit 没有实现。本地合成通过不等于任意实时 ATS 页面兼容。 / Company, Greenhouse, Lever, and Workday share a provider-neutral hash-only action contract. The sole narrow real transport is the user-present Browser Companion with per-page session rotation and one-use non-final navigation authorization. No arbitrary live-site compatibility or final-submit capability is claimed.

完成“自动投递准备度”后，普通流程只需粘贴公司官网岗位链接：用户明确授权一次 30 分钟只读会话，在公司岗位页与随后亲自打开的申请表页各点击一次浏览器伴侣。JobFlow 随即建立官网/ATS 路线、生成岗位简历与按需 Cover Letter、绑定匹配的 GitHub/作品集材料、运行 Word/PDF QA，并打开唯一审阅包。原来的 JD、官网页、申请表三文件入口已移入“高级诊断与离线 QA”，不再是普通使用前提。 / After readiness passes, paste a company job URL, explicitly authorize one 30-minute read-only session, and use the companion once on the company role and once on the form you open yourself. JobFlow prepares the materials and one review packet. The former three-snapshot workflow remains only under Advanced diagnostics and offline QA.

当前发布状态：

- `PHASE_0_4_HARDENED`
- `PHASE_4_5_SECURE_ONBOARDING_READY`
- `BILINGUAL_ONBOARDING_CENTER_READY`
- `PHASE_5_USER_PRESENT_COMPANY_ASSIST_READY`
- `FINAL_SUBMIT_USER_ONLY_AUTOMATIC_RETRY_DISABLED`
- `PHASE_6_UNATTENDED_AUTOMATION_NOT_OPERATIONAL`

## 安全边界

- 四个知识来源只读；每次发布复验指纹和写操作数。
- 私人资料以 `secure-ref:*` 进入 Windows DPAPI 存储，默认位于 `%LOCALAPPDATA%\JobOps\private`；项目数据库只保留不透明引用和非敏感元数据。
- 只有来源文件、标题、片段和哈希均通过 Knowledge Gateway 复验的已批准 Claim 能进入材料。
- 招聘入口必须来自公司 HTTPS 官网。官网导航到 Workday、Greenhouse 或 Lever 时，还要验证公司、租户、board、岗位、官方页面和 JD 快照的绑定。
- 主界面的“解析已保存的公司招聘页 / Parse a saved company careers page”可读取用户选择的本地 HTML、保存页面 JSON，以及 Greenhouse/Lever 岗位 JSON；快照不会写入项目、数据库或待审批队列，页面代码也不会执行。`discover-official-jobs` 可提出公司官网、Workday、Greenhouse 或 Lever 岗位候选，但不联网、不确认岗位仍开放，也不把候选视为已验证路线；每条结果都必须在以后取得单独授权后重新做实时新鲜度与路线验证。 / The main UI can parse user-selected local HTML, saved-page JSON, and Greenhouse/Lever job JSON without retaining it, executing page code, or mutating the application queue. Every candidate remains pending a separately authorized live freshness and route check.
- `analyze-ats-form` 继续提供不执行页面代码的本地快照分析；真实浏览器辅助则由固定 ID 的 Browser Companion 在用户当前打开的精确页面上重新收集脱敏结构并与已批准表单语义复核。它不会读取既有输入值、Cookie、Token 或页面正文；CAPTCHA、MFA、登录、跨域表单/iframe、未知控件与最终提交均失败关闭。验证通过后只写入获批字段并挂载获批文件。 / Saved-form analysis remains non-executing. For real assistance, the fixed-ID companion re-collects a sanitized structure from the exact user-open page and semantically matches it to the approved form; it never reads existing values, cookies, tokens or the page body and fails closed on CAPTCHA, MFA, login, cross-origin forms/iframes, unsupported controls or final submit.
- `application_execution` 把路线哈希、表单哈希、浏览器计划哈希、材料计划哈希、字段类别计数和待审批上限绑定为一份完整执行计划。计划内容被修改后会因哈希不一致而拒绝打开；普通报告不包含私人值、选择器、真实文件正文或实时会话。 / The execution plan binds route, form, browser-plan, material-plan and queue evidence and fails closed on tampering without exposing private values.
- Greenhouse 已有一条完整的合成离线纵向验收链：公司官网快照 → 官方到 Greenhouse 的租户/岗位路线 → JD 与 HTML 表单 → opaque/哈希化字段绑定 → 材料渲染 QA → 岗位专属答案加密确认 → 审阅批准 → 项目外临时载荷 → 限定动作会话 → 另一张新鲜最终确认 → 假提交与合成回执。表单快照、浏览器计划、公开链接对应值和上传引用保存在带随机 nonce 的 DPAPI 加密执行包中；普通数据库只保存 secure-ref 与哈希。整条验收链的网络、浏览器修改、文件上传和真实外部动作均为 0。这是离线工程证据，不代表已连接或兼容真实 Greenhouse 网站。 / The synthetic Greenhouse vertical now covers the complete bound lifecycle through encrypted per-job answers, an ephemeral payload lease, scoped fake actions, a separate fresh final authorization and a synthetic receipt. The exact form, browser plan, public-value bindings and material references live in a nonce-protected DPAPI execution bundle; the ordinary database keeps only its secure reference and hashes. It remains zero-network evidence, not a claim of live-site compatibility.
- Workday 保留离线多步骤保存页分析，并新增三页合成浏览器纵向链：首屏安全复用字段、登录/CAPTCHA 人工接管、后续页人工问题、GitHub 与三类材料、逐页一次性 Next/Continue，以及用户最终 Submit 后的回执判断。账号创建、凭据读取、验证绕过、跨域表单和自动重试仍不可运行；这仍是合成证据，不是实时站点兼容声明。 / Workday now has a three-page synthetic browser vertical covering safe reuse, human login/CAPTCHA handoff, later-page materials, scoped Next/Continue, and result observation after the user's final Submit. It remains synthetic evidence rather than live-site certification.
- Lever 已通过相同的单页本地快照完整合成闭环，从材料与加密执行包一直到独立最终确认和合成回执；没有真实传输。`ats-capabilities` 会精确列出 company/Greenhouse/Lever/Workday 当前仅由合成证据支持到哪一层，并对每份能力声明做内容哈希；所有条目固定 `live_site_verified=false`，避免把离线测试误写成真实站点兼容。 / Lever now passes the same complete synthetic lifecycle from saved single-page evidence through the encrypted bundle and synthetic receipt, while `ats-capabilities` continues to distinguish this evidence from unverified live support.

- Workday 已通过两类互补的本地证据：三页保存序列的顺序、重复字段与导航门分析，以及一份无 CAPTCHA/登录/跨域阻挡的代表性保存表单完整合成闭环。岗位专属的工作授权、薪资和自愿披露分别验证必答、可选及“不披露”语义；若页面出现 CAPTCHA、MFA、登录、危险 form action 或跨域 iframe，执行计划会失败关闭。该证据仍不代表实时 Workday 兼容。 / Workday has complementary local evidence: ordered three-page saved-sequence analysis and a complete synthetic lifecycle for a representative saved form with no CAPTCHA, login or cross-origin blocker. Required work authorization, optional compensation and voluntary non-disclosure are handled separately; unsafe live-page signals fail the execution plan closed. This is not live Workday compatibility.
- 待审批上限默认 10，可设 1—1000；容量检查与 reservation 在同一事务中完成。
- 连续接入当前采用 `MANUAL_TICK_ONLY`：`plan-continuous-intake` 只计算本次本地批次与剩余容量，首次 `run-queue` 必须由用户明确运行。批次既可使用合成测试件，也可使用已经完成入职的真实 Candidate Profile 与逐岗位明确选择的本地 JD、官网页、申请表和研究快照；真实资料模式缺少任一证据就整项失败关闭。一个岗位的本地材料错误不会阻止后续岗位继续准备；达到待审批上限后，剩余项按 FIFO 延后。项目内批次只保存哈希绑定的 secure-ref 与相对路径；网页上传在延后时把三份证据打包后仅以 DPAPI `secure-ref` 暂存，不在普通 SQLite、日志或项目文件中保留正文，并在补位生成开始前删除该密文。用户审批、退回或拒绝一项释放名额时，JobFlow 会在同一本机进程中自动准备下一项。结果只返回序号、状态、申请 ID 和错误代码，不返回 secure-ref、文件路径或私人正文。它不会注册 Windows 任务、Codex 自动化、后台服务、浏览器动作或网络动作。 / Continuous intake starts only from an explicit local tick. Hash-bound descriptors and one-use DPAPI evidence bundles let a review decision fill the newly freed slot in the same local process, while redacted outcomes, FIFO fairness and zero background, browser, network or scheduler actions remain enforced.
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
- 私人资料与演示：`demo`、`onboarding-center`、`onboarding-status`、`secure-onboard`、`secure-onboard-resume`、`finalize-resume-onboarding`、`review-onboarding`、`secure-import-master-resume`、`secure-import-answer-bank`、`secure-store-status`、`check-private-store`、`purge-synthetic-private-data`
- Claim：`propose-claims`、`list-claim-proposals`、`approve-claim`、`reject-claim`、`revoke-claim`、`revalidate-claims`
- 岗位与队列：`ats-capabilities`、`discover-official-jobs`、`verify-route`、`analyze-ats-form`、`analyze-ats-sequence`、`import-jd`、`analyze-job`、`run-to-awaiting-approval`、`plan-continuous-intake`、`run-queue`、`queue`、`list-pending`
- 审阅与恢复：`show-review-packet`、`revise-application`、`approve-review-packet`、`reject-review-packet`、`resume-blocked`、`retry-safe-step`、`explain`

## 真实使用前仍需的最小输入

用户可通过 `secure-onboard-resume` 从经授权的 Downloads 范围安全接入简历，并由系统建立 Candidate Profile、Answer Bank 和 Claim 审阅草稿。只有 PDF 时会明确保留 `EDITABLE_MASTER_DOCX_MISSING`，仍需补充可编辑 DOCX 才能做版式保持的母版修改。所有个人 Claim 都要逐条审阅。使用 Browser Companion 前，还必须准备并批准具体申请、逐项授予用户在场辅助授权，并自行确认目标页面符合站点条款。当前真实范围包含已绑定公司/Greenhouse/Lever/Workday 来源上的逐页检查、获批预填、获批材料挂载和受限非最终 Next/Continue；登录、账号创建、验证绕过、最终 Submit、自动重试和无人值守运行不在范围内。

架构、审批绑定、敏感字段、恢复、私人接入和发布证据详见现有 Skill 的一层 `references/` 与 `reports/`。

## 本地发布候选 / Local release candidate

公开发布前先在干净提交上运行：

```powershell
.\.venv\Scripts\python.exe -m jobops.release_candidate
```

该命令只在本地生成完整源码 ZIP，并从同一提交构建两次验证哈希一致；它会逐项扫描归档路径、文本、DOCX/PDF、私人运行目录与所需启动文件。结果写入被 Git 忽略的 `dist` 与 `reports`，不会联网或上传。Python wheel 仅用于 CI 的代码打包烟雾测试；当前 Windows 桌面应用应使用完整源码候选，因为运行还需要项目级 Schema、Skill、配置与启动脚本。真正上传前仍需确认 Git 作者身份策略并完成冻结源码上的新一轮独立 QA。

`python -m jobops.release_readiness` 会同时报告代码门与人工发布门。`config/github-release.json` 只是公开发布决定模板；仓库资料、私密漏洞入口、脱敏截图和全新 Windows 用户测试未真实完成前，对应值必须保持 `false`。该检查不会创建仓库、标签、网络连接或上传任何内容。 / The readiness command reports both code and human release gates; its decision template must remain unconfirmed until the named evidence actually exists, and it never creates or uploads a repository.
