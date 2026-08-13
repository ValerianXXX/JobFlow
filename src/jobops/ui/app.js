"use strict";

const STRINGS = {
  zh: {
    brandSubtitle: "找工流水线", localOnly: "仅限本机 · DPAPI 加密", eyebrow: "JOBFLOW SETUP", pageTitle: "JobFlow · 找工流水线",
    heroTitle: "一次填写，连续投递", heroBody: "从简历与项目材料、AI 资料和你的直接回答建立完整资料。所有私人内容只在本机解密，不写入普通项目文件。",
    progressLabel: "问卷完成度", draftSaved: "草稿保存时直接加密", stepSources: "资料来源", stepQuestions: "完整问卷", stepReview: "资料与 Claim 审阅", stepFinish: "确认完成",
    pipelineEyebrow: "JOBFLOW CONTROL", pipelineTitle: "本地投递控制台", pipelineBody: "统一查看资料准备度、待审批容量和安全边界。这里不会打开招聘网站或执行外部动作。", refreshDashboard: "刷新状态", dashboardRefreshed: "本地控制台已刷新", profileReadiness: "资料准备", awaitingApproval: "待你审批", approvalQueueOnly: "只生成本地审阅包", availableSlots: "剩余容量", deferredJobs: "排队等待", continuesUntilLimit: "达到上限前继续处理其他岗位", pendingReviewTitle: "待审批申请", pendingReviewBody: "只显示安全岗位摘要；私人答案和材料正文不会出现在此处。", safetyBoardTitle: "当前安全边界", realSites: "真实网站访问", externalActions: "真实外部动作", knowledgeWrites: "知识库写入", networkMode: "运行模式", pipelineReady: "已完成", pipelineNeedsSetup: "待完成", aiReadyShort: "AI 已连接", aiMissingShort: "AI 未连接", queueLimit: "上限 {limit}", pendingEmpty: "目前没有等待你审批的申请。离线处理完成的岗位会出现在这里。", packetHash: "审阅包 {hash}", awaitingApprovalStatus: "等待你的决定", safetyGuardOn: "外部动作锁定", offlineMode: "仅限本地离线", refreshingDashboard: "刷新本地控制台…", deferredListTitle: "等待处理的岗位", deferredListBody: "达到上限后进入这里；释放位置时按顺序继续。", deferredEmpty: "目前没有因容量而等待的岗位。", recentDecisionsTitle: "最近的队列决定", recentDecisionsBody: "显示本地状态变化；不会把批准当成已提交。", recentEmpty: "还没有已处理的队列决定。", safeQueueId: "安全队列编号", queuedAt: "进入时间", viewRecord: "查看记录", approvalExpiry: "本地批准有效至 {time}", statusApproved: "本地已批准", statusClosed: "已关闭", statusRevision: "等待修改", statusDeferred: "等待容量", statusOther: "本地状态：{status}",
    queuePreferences: "待审批数量上限", queuePreferencesBody: "JobFlow 达到这个数量后暂停接收新岗位；你处理一项后会继续。", pendingLimitLabel: "最多等待", saveLimit: "保存上限", limitSaved: "待审批上限已保存", viewPacket: "查看审阅包", closePacket: "关闭审阅包", reviewPacketEyebrow: "LOCAL REVIEW PACKET", loadingReviewPacket: "解密并核验本地审阅包…", packetJob: "岗位", packetFit: "匹配分析", packetGaps: "硬性缺口", packetBullets: "简历表述与证据", packetQuestions: "申请问题", packetSensitive: "必须停下确认的敏感字段", packetUploads: "待上传材料", packetActions: "审批将绑定的动作", packetRoute: "官网与 ATS 路径", packetNone: "无", packetOverall: "总体匹配", packetStatus: "状态", packetCreated: "生成时间", packetClaims: "Claim", packetEvidence: "证据", pendingLimitInvalid: "请输入 1 到 1000 之间的整数。", pendingLimitBelowActive: "上限不能低于当前已占用的审批位置。请先处理一项申请。", reviewPacketUnavailable: "无法安全打开这个审阅包；内容未被显示，请刷新后重试。", packetDecisionTitle: "你对这份审阅包的决定", packetDecisionBody: "决定只保存在本机。当前不会打开网站、上传材料或提交申请。", decisionApprove: "批准这份审阅包", decisionApproveHelp: "保存与当前哈希绑定的一次性本地批准；真实外部动作仍锁定。", decisionRevise: "退回修改", decisionReviseHelp: "标记材料需要修订；任何旧批准都会失效。", decisionReject: "不申请这个岗位", decisionRejectHelp: "关闭这项申请并释放一个待审批位置。", decisionConfirm: "我确认这是我对当前审阅包的决定，并理解本轮真实外部动作仍为 0。", confirmDecision: "确认这个决定", chooseDecision: "请先选择批准、退回修改或不申请。", confirmDecisionFirst: "请勾选确认框后再保存决定。", decisionApproved: "审阅包已批准；真实外部动作仍为 0。", decisionRevised: "已退回修改；旧批准已失效。", decisionRejected: "已关闭这项申请并释放队列位置。", savingQueueDecision: "正在核验并保存队列决定…", reviewPacketStale: "这份审阅包已经变化。为防止误批，请重新打开并审阅最新版本。", reviewDecisionUnavailable: "当前申请已不在待审批状态，请刷新队列。",
    sourceTitle: "把已有信息交给 JobFlow", sourceBody: "资料必须先通过 AI 的实体归并、分类和完整性检查，才会进入 Claim；规则拆分结果不再显示。",
    docsTitle: "简历与项目材料", docsBody: "DOCX、PDF、TXT、MD 或 JSON。适用于简历、案例、证书与补充材料。", materialType: "材料类型", resume: "简历", projectCase: "项目案例", supporting: "补充材料",
    aiTitle: "AI 资料", aiBody: "支持 ChatGPT 官方导出 ZIP 或你整理的 AI 总结。超过 200 MB 的官方导出请选择“雷霆大文件”；原始 ZIP 不会被保留。", aiType: "AI 资料类型", chatgptExport: "ChatGPT 官方导出（不超过 200 MB）", chatgptExportLarge: "雷霆大文件（超过 200 MB 请选择）", aiSummary: "AI 总结", chooseFile: "选择本地文件", uploadAndAnalyze: "选择并由 AI 分析",
    directTitle: "一次性完整问卷", directBody: "直接确认资格、偏好、法律策略和自愿披露，避免后续重复打断。", startQuestions: "开始填写", importedSources: "已安全接入", noSources: "尚未通过此页面添加资料；已有安全简历仍然保留。",
    suggestions: "待确认的自动预填建议", continueQuestions: "继续完整问卷", questionsTitle: "一次性回答全部基础问题", questionsBody: "可以明确回答、不适用或选择不披露。资格硬条件必须明确，法律与签名始终逐次确认。", saveHint: "内容不会进入命令行或明文 JSON", saveEncrypted: "加密保存并继续",
    reviewTitle: "集中处理资料与 Claim", reviewBody: "只显示经过 AI 归并和完整性检查的 Claim。工作、实习、教育和项目分别归类；确认不等于自动对外提交。", claimsReviewed: "已审阅 Claim", conflictsResolved: "已解决冲突", conflicts: "需要单独解决的冲突", conflictsHelp: "只有两个相关来源对同一事实给出不同答案时，才算冲突；系统会直接说明哪个字段、两边各写了什么。",
    profileConfirmTitle: "我已审阅 Candidate Profile", profileConfirmBody: "确认简历、项目、技能、教育及成果栏目；未解决冲突除外。", saveReview: "保存审阅结果",
    finishTitle: "完成 JobFlow 设置", finishBody: "完成后，JobFlow 可连续处理离线岗位并生成待审批队列。真实网站、上传、邮件和最终提交仍保持关闭。", finalConsent: "我确认以上答案和审阅决定准确，并同意将其加密保存为当前 Candidate Profile 与 Answer Bank。", completeButton: "完成 JobFlow 设置",
    unknown: "尚未回答", confirmed: "已确认", preferNot: "不愿回答", notApplicable: "不适用", reuse: "后续复用", confirmEach: "每次申请确认", preferPolicy: "始终不愿披露", doNotStore: "不保存具体值", policy: "使用策略",
    accept: "采用", sourceImported: "资料已安全接入", uploadFailed: "资料接入失败", aiRepairApplied: "AI 已自动纠正首轮不合格输出", aiRepairFailed: "AI 已自动纠正一次，但仍有内容无法由所标注的原文行支持；本次没有导入任何内容。请重试，若持续出现请换用文字更清晰的 DOCX 或 PDF。", aiExportRepairFailed: "ZIP 已完成本机扫描，但 AI 在自动纠正后仍产生了一条无法由原文支持的内容，所以本次按真实性规则没有导入。你可以重试；若仍失败，可上传一份整理后的 AI 总结。", selectLightning: "这个 ZIP 超过 200 MB，请在“AI 资料类型”选择“雷霆大文件”后重新选择。", lightningZipOnly: "雷霆大文件只接受 ChatGPT 官方导出 ZIP。", lightningTooLarge: "此 ZIP 超过 8 GB 的本机安全上限。", uploadInterrupted: "本机文件传送被中断，请确认页面仍打开后重试。", saved: "已加密保存", reviewSaved: "审阅结果已保存", completeSuccess: "JobFlow 设置完成。真实外部动作仍为 0。", answerFirst: "请先完成所有必需信息和审阅。",
    confirmAll: "整组确认", rejectAll: "整组排除", pending: "待审阅", rejected: "排除", evidence: "证据", noConflicts: "目前没有真正需要处理的冲突", conflictLabel: "冲突", conflictPending: "需要决定", conflictResolved: "已解决", conflictLocation: "具体冲突字段", affectedClaim: "所属 Claim", affectedField: "字段", conflictReason: "直白说明", resumeSide: "简历写的是", evidenceSide: "知识证据写的是", sourceCandidate: "来源", numericMismatch: "同一个事实的数值不同，两边不能同时成立", multipleValues: "同一个问卷字段出现了不同答案，请直接选择正确值", noEvidencePreview: "没有可比较的相关证据", chooseResolution: "哪一边正确？", reviewThisConflict: "查看并处理冲突", answerThisField: "前往问卷直接确认这个字段", useResume: "简历正确", useEvidence: "知识证据正确", useDirect: "我来直接回答", exclude: "这不是有效 Claim / 冲突",
    readyAnswers: "基础问题", readyClaims: "Claim 审阅", readyConflicts: "冲突处理", profile: "Profile 确认", complete: "完成", incomplete: "未完成", loadingInitial: "正在加载加密资料…", importing: "正在接入并由 AI 分析…", savingAnswers: "正在加密保存问卷…", savingReview: "正在保存审阅决定…", completingOnboarding: "正在生成完成记录…", savingSuggestion: "正在采用建议…", savingLanguage: "正在保存语言设置…", elapsedWithEstimate: "已进行 {elapsed} 秒 · 预计还需约 {remaining} 秒", estimatingTime: "已进行 {elapsed} 秒 · 正在动态估算剩余时间", stillWorking: "仍在处理中，请不要关闭页面", longRunningNoCountdown: "已超过初始估计，任务仍在运行；为避免误导，不会重新开始倒数。", uploadStage: "正在传送到仅限本机的安全处理区 · {percent}%", uploadEta: "已传送 {loaded} / {total} · 按当前速度约还需 {remaining} 秒", uploadMeasuring: "已传送 {loaded} / {total} · 正在测量本机传送速度", aiAnalysisStage: "本机传送完成 · 正在解析并由 AI 进行真实性分析", lightningAnalysisStage: "本机传送完成 · 正在流式扫描大型 ZIP 并由 AI 分析", aiAnalysisRange: "本阶段已进行 {elapsed} 秒 · 通常需要 {min}–{max} 分钟，取决于当前模型", aiAnalysisOverdue: "本阶段已进行 {elapsed} 秒 · 已超过通常用时，但仍在运行且不会重新倒数", close: "关闭", batchProgress: "第 {current}/{total} 份资料 · 已完成 {completed} 份", reprocessingAll: "正在一键分析全部资料…",
    completedSnapshotTitle: "当前是已完成的只读快照", completedSnapshotBody: "历史版本不会被覆盖。若要修改资料，请建立一个新的可编辑版本。", startRevision: "建立可编辑新版本", revisionStarted: "新的可编辑版本已建立", revisionReady: "当前可编辑版本已同步", revisionSyncFailed: "新版本可能已建立，但页面同步失败。请刷新页面后继续。", invalidLocalResponse: "本机服务返回了无法识别的响应，请刷新页面或重启 JobFlow。",
    attentionRequired: "这里还需要处理", serviceRestartRequired: "页面代码已更新，但当前后台还是旧版本。请关闭这个页面，在启动窗口按 Ctrl+C，然后重新启动 JobFlow。", profileReviewRequired: "请先在“资料与 Claim 审阅”底部勾选 Candidate Profile 确认框并保存。", answersIncomplete: "问卷仍有未处理的问题。请逐项回答，或明确选择“不适用 / 不愿回答”。", hardConditionsUnresolved: "求职资格硬条件仍不明确。请回到问卷，把标出的字段改为明确答案。", sourcePreviewPending: "还有 AI 分析结果尚未确认或丢弃。请先处理标出的资料预览。", sourceAiReanalysisRequired: "仍有资料没有通过当前 AI 的重新分析。请重新分析或删除对应资料。", claimReviewIncomplete: "还有 Claim 未选择“确认”或“排除”。请处理标出的第一项。", conflictReviewIncomplete: "还有冲突没有选择处理方式。请处理标出的第一项。", onboardingConfirmationRequired: "请先勾选最后的确认框。", onboardingAlreadyComplete: "这个版本已经完成；如需修改，请建立新的可编辑版本。", onboardingRevisionRequired: "当前版本是只读快照；请先建立新的可编辑版本。", invalidAnswer: "有一项问卷答案格式不完整，请检查标出的字段。", invalidClaim: "有一项 Claim 编辑不完整或分类不适用，请检查标出的内容。", sourceTypeUnsupported: "当前资料类型或文件格式不受支持，请重新选择。", sourceSizeInvalid: "文件为空或超过本地安全上限，请检查文件后重试。", localRequestFailed: "本机操作没有完成。页面没有写入不完整结果；请按标出的位置检查后重试。", reviewSavedButIncomplete: "审阅草稿已加密保存；完成标出的项目后才能进入最后一步。",
    previewTitle: "审阅 AI 归并结果", previewBody: "每条都是 AI 重建后的完整 Claim，并绑定到唯一工作、实习、教育或项目实体；不会再按页面换行拆分。", confirmSource: "确认所选内容", includeAllClaims: "一键纳入全部 Claim", selectAllClaims: "全选", clearAllClaims: "清空选择", discardPreview: "放弃本次导入", previewEmpty: "AI 没有找到满足完整性与证据要求的 Claim。不会用规则结果补位。", selectedByDefault: "AI 已过滤（仍需你确认）", needsReview: "未通过严格 AI 门", includeAsClaim: "纳入 Claim", reprocess: "用 AI 重新分析", previewReady: "AI 归并预览已准备，请先审阅", analyzeAllSources: "一键分析全部资料", bulkAnalysisHint: "一键重新分析全部已保留加密原件的资料；ChatGPT 官方导出需要重新上传原 ZIP。", bulkAnalysisComplete: "全部可分析资料已生成新的 AI 审阅预览", bulkAnalysisPartial: "已完成 {completed} 份，{failed} 份未通过 AI 校验", noReprocessableSources: "没有可直接重新分析的资料；ChatGPT 官方导出请重新上传原 ZIP。", allSourcesAlreadyPending: "全部可分析资料都已经有待审阅的 AI 结果", reuploadAndAnalyze: "重新上传原 ZIP 并分析", reuploadRequired: "需重新上传后分析", analysisPassed: "AI 分析完成", analysisMissing: "尚未通过 AI 分析", claimCandidates: "AI Claim",
    claimEditTitle: "Claim 可编辑审阅", claimEditHelp: "左侧勾选框只用于把同一实体中的多条 Claim 合并，不代表确认或采用；确认状态仍由右侧下拉框决定。", selectForMerge: "选择用于合并", mergeSelected: "合并已勾选项", editText: "可编辑 Claim 表述", category: "经历类型", deleteClaim: "删除", restoreClaim: "恢复", splitClaim: "拆分", applySplit: "应用拆分", splitHelp: "每行填写一条完整 Claim（至少两条）", mergedTextPrompt: "请编辑合并后的完整 Claim：", chooseTwoClaims: "请至少勾选两条 Claim", claimChanged: "Claim 已更新", transformingClaims: "正在更新 Claim 结构…", reprocessing: "正在由 AI 重新归并，并在需要时自动纠正…", committingSource: "正在确认 AI 结果…", includingAll: "正在纳入全部 AI Claim…", discardingSource: "正在放弃本次导入…", deletingSource: "正在删除材料及其关联内容…", deleteSource: "删除材料", deleteSourceConfirm: "确定删除这份材料，以及由它生成的所有 Claim 和建议吗？本机加密副本也会删除，此操作不能撤销。", sourceDeleted: "材料及其关联内容已删除", startingRevision: "正在建立新的加密版本…", readonly: "只读", aiEngineReady: "AI 核心已连接", aiEngineReadyBody: "资料会先重建完整句、归并同一经历并区分工作、实习、教育与项目；任何 Claim 仍需你确认。", aiEngineMissing: "必须先连接 AI", aiEngineMissingBody: "当前没有可用 AI，因此上传和重新分析已暂停，也不会显示任何规则拆分候选。连接 AI 后再分析现有材料。", aiMode: "分析模式", legacyQuarantined: "已隔离 {count} 条旧规则结果；它们不会进入 Claim、Profile 或后续申请。", invalidConflictsSuppressed: "已排除 {count} 条无关或不可比较的旧证据映射；它们不算冲突。", work: "正式工作", internship: "实习", education: "教育", project: "项目", skill: "技能", certification: "证书", language: "语言", summary: "职业总结", entityClaims: "条 Claim", entityUnknown: "未命名实体", valueDifference: "同一个{dimension}：简历为 {left}，知识证据为 {right}。", resumeMetrics: "简历数值", evidenceMetrics: "证据数值",
    connectAi: "连接 AI", aiConnectedButton: "AI 已连接", aiConnectionEyebrow: "AI CONNECTION", aiConnectionTitle: "连接已经准备好的 AI", aiConnectionBody: "JobFlow 不要求再次填写模型密钥。它会自动检查 Windows 与 WSL，并通过仅限本机的通道建立连接。", existingAgentTitle: "使用已有 Agent", existingAgentBody: "自动检测 Windows 或 WSL 中的 Hermes / OpenClaw，并复用 Agent 已经配置好的模型。", localModelTitle: "使用本地大模型", localModelBody: "自动检测 Windows 或 WSL 中的 Ollama、LM Studio、LocalAI、llama.cpp 或 vLLM。", customApiTitle: "自定义 API / 适配器", customApiBody: "保留给企业模型、私有网关和其他 Agent。普通用户无需配置。", detectAndConnect: "自动检测并连接", reserved: "接口已预留", aiPrivacyNote: "JobFlow 不读取或保存 Agent 的 API Key、Cookie 或登录令牌。WSL 连接不会暴露局域网端口，私人请求只经 stdin 或回环地址传递。Hermes 与 OpenClaw 都只以零工具分析模式连接：动作工具被禁用，任何工具调用都会使结果作废。使用 Agent 时，资料去向取决于该 Agent 当前选择的本地或云端模型。", aiNotConnectedStatus: "尚未连接 AI。请选择已有 Agent 或本地大模型。", aiConnectedStatus: "已连接：{name}", aiConnectedModel: "模型：{model} · 数据路径：{route}", detectingAgent: "正在检查 Windows 与 WSL 中的 Hermes / OpenClaw，并建立安全连接…", detectingLocalModel: "正在检查 Windows 与 WSL 中的本地大模型服务…", aiConnectionSucceeded: "AI 已自动检测并连接", aiConnectionFailed: "Windows 与 WSL 中都没有找到已就绪的 AI。请先启动 Agent 或本地模型服务后重试。", aiWslHermesAuthRequired: "已在 WSL 找到 Hermes，但它当前选择的模型或登录状态不可用。请在 Hermes 中确认模型后重试。", aiWslProxyStartFailed: "已在 WSL 找到 Hermes，但本机安全连接没有成功启动。请确认 Hermes 模型可用后重试。", aiWslBridgeMissing: "已检测到 WSL，但其中缺少安全连接所需的 curl。请在该 WSL 环境安装 curl 后重试。", aiAgentSafetyRejected: "Agent 尝试调用工具或未提供可验证的零工具审计，JobFlow 已拒绝该连接。"
  },
  en: {
    brandSubtitle: "Job application pipeline", localOnly: "Local only · DPAPI encrypted", eyebrow: "JOBFLOW SETUP", pageTitle: "JobFlow · Job pipeline",
    heroTitle: "Set it up once. Apply continuously.", heroBody: "Build a complete profile from resumes and project materials, AI sources, and your direct answers. Private content is decrypted only on this computer and never written to ordinary project files.",
    progressLabel: "Questionnaire progress", draftSaved: "Drafts are encrypted when saved", stepSources: "Sources", stepQuestions: "Full questionnaire", stepReview: "Profile & Claim review", stepFinish: "Finish",
    pipelineEyebrow: "JOBFLOW CONTROL", pipelineTitle: "Local application control center", pipelineBody: "See profile readiness, approval capacity, and safety boundaries in one place. This screen never opens recruiting sites or performs external actions.", refreshDashboard: "Refresh status", dashboardRefreshed: "Local control center refreshed", profileReadiness: "Profile readiness", awaitingApproval: "Awaiting you", approvalQueueOnly: "Local review packets only", availableSlots: "Available capacity", deferredJobs: "Waiting in line", continuesUntilLimit: "Other jobs continue until the limit", pendingReviewTitle: "Applications awaiting approval", pendingReviewBody: "Only safe job summaries appear here; private answers and document bodies never do.", safetyBoardTitle: "Active safety boundary", realSites: "Real-site visits", externalActions: "Real external actions", knowledgeWrites: "Knowledge writes", networkMode: "Run mode", pipelineReady: "Complete", pipelineNeedsSetup: "Needs setup", aiReadyShort: "AI connected", aiMissingShort: "AI not connected", queueLimit: "limit {limit}", pendingEmpty: "No application currently needs your approval. Offline-processed roles will appear here.", packetHash: "packet {hash}", awaitingApprovalStatus: "Awaiting your decision", safetyGuardOn: "External actions locked", offlineMode: "Local offline only", refreshingDashboard: "Refreshing local control center…", deferredListTitle: "Waiting roles", deferredListBody: "Roles wait here at capacity and resume in order when a slot opens.", deferredEmpty: "No role is currently waiting for queue capacity.", recentDecisionsTitle: "Recent queue decisions", recentDecisionsBody: "Shows local state changes and never labels approval as submission.", recentEmpty: "No queue decision has been completed yet.", safeQueueId: "Safe queue ID", queuedAt: "Queued", viewRecord: "View record", approvalExpiry: "Local approval valid until {time}", statusApproved: "Locally approved", statusClosed: "Closed", statusRevision: "Revision needed", statusDeferred: "Waiting for capacity", statusOther: "Local state: {status}",
    queuePreferences: "Pending-approval limit", queuePreferencesBody: "JobFlow pauses new intake at this number and continues after you resolve one item.", pendingLimitLabel: "Maximum waiting", saveLimit: "Save limit", limitSaved: "Pending-approval limit saved", viewPacket: "View review packet", closePacket: "Close packet", reviewPacketEyebrow: "LOCAL REVIEW PACKET", loadingReviewPacket: "Decrypting and validating local review packet…", packetJob: "Job", packetFit: "Fit analysis", packetGaps: "Hard gaps", packetBullets: "Resume wording and evidence", packetQuestions: "Application questions", packetSensitive: "Sensitive fields requiring a stop", packetUploads: "Pending uploads", packetActions: "Actions bound by approval", packetRoute: "Official-site and ATS route", packetNone: "None", packetOverall: "Overall fit", packetStatus: "Status", packetCreated: "Created", packetClaims: "Claim", packetEvidence: "Evidence", pendingLimitInvalid: "Enter a whole number from 1 to 1000.", pendingLimitBelowActive: "The limit cannot be below the number of occupied approval slots. Resolve one application first.", reviewPacketUnavailable: "This review packet could not be opened safely. No content was shown; refresh and retry.", packetDecisionTitle: "Your decision on this review packet", packetDecisionBody: "The decision is saved locally only. No site opens, material uploads, or application submission occurs now.", decisionApprove: "Approve this review packet", decisionApproveHelp: "Save a one-time local approval bound to this exact hash; real external actions remain locked.", decisionRevise: "Return for revision", decisionReviseHelp: "Mark materials for revision and invalidate any earlier approval.", decisionReject: "Do not apply", decisionRejectHelp: "Close this application and release one pending-review slot.", decisionConfirm: "I confirm this is my decision on the current packet and understand real external actions remain 0 in this run.", confirmDecision: "Confirm this decision", chooseDecision: "Choose approve, revise, or do not apply first.", confirmDecisionFirst: "Check the confirmation box before saving the decision.", decisionApproved: "Review packet approved; real external actions remain 0.", decisionRevised: "Returned for revision; prior approval invalidated.", decisionRejected: "Application closed and queue capacity released.", savingQueueDecision: "Validating and saving queue decision…", reviewPacketStale: "This packet changed. To prevent stale approval, reopen and review the current version.", reviewDecisionUnavailable: "This application is no longer awaiting a decision. Refresh the queue.",
    sourceTitle: "Bring your existing information into JobFlow", sourceBody: "A source must pass AI entity consolidation, classification, and completeness checks before anything can enter Claim review. Rule-split output is no longer shown.",
    docsTitle: "Resume & project materials", docsBody: "DOCX, PDF, TXT, MD, or JSON for resumes, cases, certificates, and supporting material.", materialType: "Material type", resume: "Resume", projectCase: "Project case", supporting: "Supporting material",
    aiTitle: "AI sources", aiBody: "Use an official ChatGPT export ZIP or a curated AI summary. For exports over 200 MB, pick ZIPzilla Express. The raw ZIP is never retained.", aiType: "AI source type", chatgptExport: "Official ChatGPT export (up to 200 MB)", chatgptExportLarge: "ZIPzilla Express (over 200 MB — unleash the beast)", aiSummary: "AI summary", chooseFile: "Choose local file", uploadAndAnalyze: "Choose & analyze with AI",
    directTitle: "One-time full questionnaire", directBody: "Confirm eligibility, preferences, legal policies, and voluntary disclosures once to prevent repeated interruptions.", startQuestions: "Start questionnaire", importedSources: "Securely imported", noSources: "No source added from this page yet; the existing secure resume remains available.",
    suggestions: "Autofill suggestions awaiting confirmation", continueQuestions: "Continue to full questionnaire", questionsTitle: "Answer every foundational question once", questionsBody: "Answer, mark not applicable, or choose not to disclose. Eligibility gates must be explicit; legal and signature items always require per-application confirmation.", saveHint: "Answers never enter CLI arguments or plaintext JSON", saveEncrypted: "Encrypt, save & continue",
    reviewTitle: "Resolve Profile and Claims together", reviewBody: "Only AI-consolidated, completeness-checked Claims are shown. Work, internships, education, and projects remain separate. Confirmation never submits anything externally.", claimsReviewed: "Claims reviewed", conflictsResolved: "Conflicts resolved", conflicts: "Conflicts requiring an individual decision", conflictsHelp: "A conflict exists only when two relevant sources disagree about the same fact. Each item names the exact field and states both values plainly.",
    profileConfirmTitle: "I reviewed the Candidate Profile", profileConfirmBody: "I checked resume, project, skill, education, and outcome sections, excluding unresolved conflicts.", saveReview: "Save review",
    finishTitle: "Complete JobFlow setup", finishBody: "After completion, JobFlow can process offline jobs continuously and build a review queue. Real websites, uploads, email, and final submission remain disabled.", finalConsent: "I confirm these answers and review decisions are accurate and authorize encrypted storage as my current Candidate Profile and Answer Bank.", completeButton: "Complete JobFlow setup",
    unknown: "Unanswered", confirmed: "Confirmed", preferNot: "Prefer not to answer", notApplicable: "Not applicable", reuse: "Reuse later", confirmEach: "Confirm each application", preferPolicy: "Always prefer not to disclose", doNotStore: "Do not store a value", policy: "Use policy",
    accept: "Accept", sourceImported: "Source securely imported", uploadFailed: "Source import failed", aiRepairApplied: "AI automatically corrected an invalid first-pass result", aiRepairFailed: "AI made one automatic correction, but some content still was not supported by its cited source lines. Nothing from this attempt was imported. Retry, or use a clearer DOCX or PDF if it persists.", aiExportRepairFailed: "The ZIP finished its local scan, but after one automatic correction the AI still produced a statement unsupported by the source. The truth gate imported nothing. Retry, or upload a curated AI summary if it persists.", selectLightning: "This ZIP is over 200 MB. Select ZIPzilla Express under AI source type, then choose it again.", lightningZipOnly: "ZIPzilla Express accepts official ChatGPT export ZIP files only.", lightningTooLarge: "This ZIP exceeds the 8 GB local safety limit.", uploadInterrupted: "The local file transfer was interrupted. Keep this page open and retry.", saved: "Encrypted draft saved", reviewSaved: "Review saved", completeSuccess: "JobFlow setup is complete. Real external actions remain 0.", answerFirst: "Complete the required answers and reviews first.",
    confirmAll: "Confirm group", rejectAll: "Exclude group", pending: "Pending", rejected: "Excluded", evidence: "evidence", noConflicts: "No genuine conflicts currently require review", conflictLabel: "Conflict", conflictPending: "Decision required", conflictResolved: "Resolved", conflictLocation: "Exact conflicting field", affectedClaim: "Claim", affectedField: "Field", conflictReason: "Plain explanation", resumeSide: "Resume says", evidenceSide: "Knowledge evidence says", sourceCandidate: "Source", numericMismatch: "The same fact has different values; both cannot be true", multipleValues: "The same questionnaire field has different answers; choose the correct value", noEvidencePreview: "No comparable relevant evidence", chooseResolution: "Which side is correct?", reviewThisConflict: "Review this conflict", answerThisField: "Confirm this field in the questionnaire", useResume: "Resume is correct", useEvidence: "Knowledge evidence is correct", useDirect: "I will answer directly", exclude: "Not a valid Claim/conflict",
    readyAnswers: "Core answers", readyClaims: "Claim review", readyConflicts: "Conflict review", profile: "Profile confirmation", complete: "Complete", incomplete: "Incomplete", loadingInitial: "Loading encrypted profile…", importing: "Importing and analyzing with AI…", savingAnswers: "Encrypting questionnaire draft…", savingReview: "Saving review decisions…", completingOnboarding: "Creating completion records…", savingSuggestion: "Accepting suggestion…", savingLanguage: "Saving language preference…", elapsedWithEstimate: "{elapsed}s elapsed · about {remaining}s remaining", estimatingTime: "{elapsed}s elapsed · estimating time remaining", stillWorking: "Still working — keep this page open", longRunningNoCountdown: "The initial estimate has passed. Work is continuing; the countdown will not restart and pretend otherwise.", uploadStage: "Moving into the local-only secure workspace · {percent}%", uploadEta: "{loaded} / {total} transferred · about {remaining}s at the current speed", uploadMeasuring: "{loaded} / {total} transferred · measuring local transfer speed", aiAnalysisStage: "Local transfer complete · parsing and running AI truth checks", lightningAnalysisStage: "Local transfer complete · streaming the giant ZIP and running AI analysis", aiAnalysisRange: "This stage has run for {elapsed}s · usually {min}–{max} min, depending on the model", aiAnalysisOverdue: "This stage has run for {elapsed}s · longer than usual, still active, and no fake countdown reset", close: "Close", batchProgress: "Source {current}/{total} · {completed} completed", reprocessingAll: "Analyzing all sources in one click…",
    completedSnapshotTitle: "This is a completed read-only snapshot", completedSnapshotBody: "Historical versions are never overwritten. Create a new editable revision to change information.", startRevision: "Create editable revision", revisionStarted: "A new editable revision was created", revisionReady: "The editable revision is now in sync", revisionSyncFailed: "The revision may have been created, but this page could not synchronize. Refresh the page to continue.", invalidLocalResponse: "The local service returned an unrecognized response. Refresh the page or restart JobFlow.",
    attentionRequired: "This still needs attention", serviceRestartRequired: "The page was updated, but the running local service is an older version. Close this page, press Ctrl+C in the launch window, and start JobFlow again.", profileReviewRequired: "At the bottom of Profile & Claim review, check the Candidate Profile confirmation and save the review.", answersIncomplete: "Some questionnaire items still need a decision. Answer each one or explicitly choose not applicable / prefer not to answer.", hardConditionsUnresolved: "A required eligibility condition is still ambiguous. Return to the questionnaire and give the highlighted field a definite answer.", sourcePreviewPending: "An AI result is still awaiting confirmation or discard. Resolve the highlighted source preview first.", sourceAiReanalysisRequired: "At least one source has not passed analysis by the current AI. Re-analyze or delete that source.", claimReviewIncomplete: "At least one Claim still needs Confirm or Exclude. Resolve the highlighted item.", conflictReviewIncomplete: "At least one conflict still needs a resolution. Resolve the highlighted item.", onboardingConfirmationRequired: "Check the final confirmation box first.", onboardingAlreadyComplete: "This revision is already complete. Create an editable revision to make changes.", onboardingRevisionRequired: "This revision is a read-only snapshot. Create an editable revision first.", invalidAnswer: "A questionnaire answer is incomplete or invalid. Check the highlighted field.", invalidClaim: "A Claim edit is incomplete or has an invalid category. Check the highlighted content.", sourceTypeUnsupported: "This source type or file format is not supported. Choose a different file.", sourceSizeInvalid: "The file is empty or exceeds the local safety limit. Check it and retry.", localRequestFailed: "The local operation did not finish. No partial result was committed; check the highlighted area and retry.", reviewSavedButIncomplete: "The encrypted review draft was saved. Finish the highlighted item before moving to the final step.",
    previewTitle: "Review AI-consolidated results", previewBody: "Each item is a complete AI-reconstructed Claim tied to one work, internship, education, or project entity. Page wrapping no longer creates fragments.", confirmSource: "Confirm selected content", includeAllClaims: "Include all Claims in one click", selectAllClaims: "Select all", clearAllClaims: "Clear selection", discardPreview: "Discard this import", previewEmpty: "AI found no Claims that passed completeness and evidence checks. Rules will not fill the gap.", selectedByDefault: "AI filtered (still needs confirmation)", needsReview: "Did not pass strict AI gate", includeAsClaim: "Include as Claim", reprocess: "Re-analyze with AI", previewReady: "AI-consolidated preview is ready", analyzeAllSources: "Analyze every source", bulkAnalysisHint: "Re-analyze every source whose encrypted original is retained. Official ChatGPT exports must be uploaded again.", bulkAnalysisComplete: "Every eligible source now has a fresh AI review preview", bulkAnalysisPartial: "Completed {completed}; {failed} did not pass AI validation", noReprocessableSources: "No source can be re-analyzed directly. Upload the original ChatGPT export ZIP again.", allSourcesAlreadyPending: "Every eligible source already has an AI result awaiting review", reuploadAndAnalyze: "Upload original ZIP & analyze", reuploadRequired: "Re-upload to analyze", analysisPassed: "AI analysis complete", analysisMissing: "AI analysis not completed", claimCandidates: "AI Claims",
    claimEditTitle: "Editable Claim review", claimEditHelp: "The left checkbox only merges Claims inside the same entity; it does not confirm or accept them. Use the dropdown on the right for each decision.", selectForMerge: "Select for merge", mergeSelected: "Merge selected", editText: "Editable Claim statement", category: "Experience type", deleteClaim: "Delete", restoreClaim: "Restore", splitClaim: "Split", applySplit: "Apply split", splitHelp: "Enter one complete Claim per line (at least two)", mergedTextPrompt: "Edit the merged Claim statement:", chooseTwoClaims: "Select at least two Claims", claimChanged: "Claim updated", transformingClaims: "Updating Claim structure…", reprocessing: "Re-analyzing, consolidating, and correcting with AI if needed…", committingSource: "Confirming AI results…", includingAll: "Including every AI Claim…", discardingSource: "Discarding import…", deletingSource: "Deleting the source and linked content…", deleteSource: "Delete source", deleteSourceConfirm: "Delete this source and every Claim and suggestion derived from it? Its local encrypted copy will also be deleted. This cannot be undone.", sourceDeleted: "Source and linked content deleted", startingRevision: "Creating encrypted revision…", readonly: "Read only", aiEngineReady: "AI core connected", aiEngineReadyBody: "AI reconstructs complete sentences, consolidates repeated experience, and separates work, internships, education, and projects. Every Claim still needs your confirmation.", aiEngineMissing: "AI connection required", aiEngineMissingBody: "No AI is available, so uploads and re-analysis are paused and no rule-split candidates will be shown. Connect AI before analyzing retained sources.", aiMode: "Analysis mode", legacyQuarantined: "{count} legacy rule-derived items are quarantined. They cannot enter Claims, the Profile, or applications.", invalidConflictsSuppressed: "{count} unrelated or non-comparable legacy evidence mappings were excluded. They are not conflicts.", work: "Work", internship: "Internship", education: "Education", project: "Project", skill: "Skill", certification: "Certification", language: "Language", summary: "Professional summary", entityClaims: "Claims", entityUnknown: "Unnamed entity", valueDifference: "Same {dimension}: resume says {left}; knowledge evidence says {right}.", resumeMetrics: "Resume values", evidenceMetrics: "Evidence values",
    connectAi: "Connect AI", aiConnectedButton: "AI connected", aiConnectionEyebrow: "AI CONNECTION", aiConnectionTitle: "Connect an AI you already prepared", aiConnectionBody: "JobFlow does not ask for another model key. It checks Windows and WSL automatically, then connects through a local-only route.", existingAgentTitle: "Use an existing Agent", existingAgentBody: "Automatically detect Hermes / OpenClaw on Windows or WSL and reuse the model already configured by that Agent.", localModelTitle: "Use a local model", localModelBody: "Automatically detect Ollama, LM Studio, LocalAI, llama.cpp, or vLLM on Windows or WSL.", customApiTitle: "Custom API / adapter", customApiBody: "Reserved for enterprise models, private gateways, and other Agents. Most users do not need it.", detectAndConnect: "Detect and connect", reserved: "Interface reserved", aiPrivacyNote: "JobFlow never reads or stores an Agent API key, cookie, or login token. WSL connections never expose a LAN port; private requests use stdin or loopback only. Hermes and OpenClaw both connect in zero-tool analysis mode: action tools are disabled and any tool call invalidates the result. With an Agent, data routing follows that Agent's current local or cloud model configuration.", aiNotConnectedStatus: "No AI is connected yet. Choose an existing Agent or a local model.", aiConnectedStatus: "Connected: {name}", aiConnectedModel: "Model: {model} · Data route: {route}", detectingAgent: "Checking Windows and WSL for Hermes / OpenClaw and establishing a safe connection…", detectingLocalModel: "Checking Windows and WSL for a prepared local model service…", aiConnectionSucceeded: "AI detected and connected", aiConnectionFailed: "No ready AI was found on Windows or WSL. Start an Agent or local model service and try again.", aiWslHermesAuthRequired: "Hermes was found in WSL, but its selected model or sign-in is not currently usable. Confirm the model in Hermes, then retry.", aiWslProxyStartFailed: "Hermes was found in WSL, but the local-only bridge did not start. Confirm its model is ready, then retry.", aiWslBridgeMissing: "WSL was detected, but curl is missing there. Install curl in that WSL environment to enable the safe local bridge.", aiAgentSafetyRejected: "The Agent attempted a tool call or did not provide a verifiable zero-tool audit, so JobFlow rejected the connection."
  }
};

const UI_PROTOCOL_VERSION = 6;
const state = { locale: "zh", data: null, serviceCompatible: false, lastBlockingError: null, reviewPacket: null, reviewDecision: "", reviewDecisionConfirmed: false, answerDraft: {}, claimDraft: {}, claimEditDraft: {}, conflictDraft: {}, selectedClaims: new Set(), activities: [] };
const sessionToken = location.pathname.split("/")[2];
const base = `/session/${sessionToken}/`;
let activitySequence = 0;
let activityTimer = null;
let toastTimer = null;
const ACTIVITY_ESTIMATES = {
  loadingInitial: 6, importing: 45, savingAnswers: 8, savingReview: 8,
  completingOnboarding: 10, savingSuggestion: 5, savingLanguage: 4,
  detectingAgent: 22, detectingLocalModel: 12, startingRevision: 8,
  committingSource: 7, includingAll: 7, discardingSource: 5,
  deletingSource: 7, transformingClaims: 8, reprocessing: 45,
  reprocessingAll: 45, refreshingDashboard: 5, loadingReviewPacket: 5, savingQueueDecision: 7
};
const STANDARD_CHATGPT_EXPORT_BYTES = 200 * 1024 * 1024;
const MAX_LIGHTNING_EXPORT_BYTES = 8 * 1024 * 1024 * 1024;

function t(key) { return (STRINGS[state.locale] || STRINGS.zh)[key] || key; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c])); }
function isReadonly() { return state.data?.status === "ONBOARDING_COMPLETE"; }
function disabledAttr(condition=true) { return condition ? " disabled" : ""; }
function showToast(message, error=false, duration=4200) {
  const el=document.querySelector("#toast");
  clearTimeout(toastTimer); el.textContent=message; el.className=error?"show error":"show";
  toastTimer=setTimeout(()=>el.className="",duration);
}

function makeUiError(code, details={}) { const error=new Error(code); error.code=code; error.details=details; return error; }

function assertUiCompatibility(payload) {
  if(payload?.build?.product!=="JobFlow" || payload?.build?.ui_protocol!==UI_PROTOCOL_VERSION) {
    state.serviceCompatible=false;
    throw makeUiError("SERVICE_RESTART_REQUIRED");
  }
  state.serviceCompatible=true;
}

const LOCAL_ERROR_KEYS = {
  SERVICE_RESTART_REQUIRED:"serviceRestartRequired", LOCAL_RESPONSE_INVALID:"invalidLocalResponse",
  PENDING_LIMIT_INVALID:"pendingLimitInvalid", PENDING_LIMIT_BELOW_ACTIVE:"pendingLimitBelowActive",
  REVIEW_PACKET_NOT_FOUND:"reviewPacketUnavailable", REVIEW_PACKET_SIZE_INVALID:"reviewPacketUnavailable",
  REVIEW_PACKET_INVALID:"reviewPacketUnavailable", REVIEW_PACKET_BINDING_INVALID:"reviewPacketUnavailable",
  REVIEW_PACKET_HASH_INVALID:"reviewPacketUnavailable", SECURE_REFERENCE_MISSING:"reviewPacketUnavailable",
  SECURE_REFERENCE_REVOKED:"reviewPacketUnavailable", SECURE_REFERENCE_HASH_MISMATCH:"reviewPacketUnavailable",
  REVIEW_PACKET_STALE:"reviewPacketStale", REVIEW_DECISION_INVALID:"chooseDecision",
  EXPLICIT_CONFIRMATION_REQUIRED:"confirmDecisionFirst", APPLICATION_NOT_AWAITING_APPROVAL:"reviewDecisionUnavailable",
  APPLICATION_NOT_REVISABLE:"reviewDecisionUnavailable", APPLICATION_BINDING_MISSING:"reviewPacketUnavailable",
  PROFILE_REVIEW_REQUIRED:"profileReviewRequired", ONBOARDING_ANSWERS_INCOMPLETE:"answersIncomplete",
  ONBOARDING_HARD_CONDITIONS_UNRESOLVED:"hardConditionsUnresolved", SOURCE_PREVIEW_PENDING:"sourcePreviewPending",
  SOURCE_AI_REANALYSIS_REQUIRED:"sourceAiReanalysisRequired", CLAIM_REVIEW_INCOMPLETE:"claimReviewIncomplete",
  CONFLICT_REVIEW_INCOMPLETE:"conflictReviewIncomplete", ONBOARDING_CONFIRMATION_REQUIRED:"onboardingConfirmationRequired",
  ONBOARDING_ALREADY_COMPLETE:"onboardingAlreadyComplete", ONBOARDING_REVISION_REQUIRED:"onboardingRevisionRequired",
  ONBOARDING_ANSWER_REQUIRED:"invalidAnswer", ONBOARDING_ANSWER_INVALID:"invalidAnswer", ONBOARDING_ANSWERS_INVALID:"invalidAnswer",
  CLAIM_EDIT_INVALID:"invalidClaim", CLAIM_REVIEW_INVALID:"invalidClaim", CLAIM_TRANSFORM_INVALID:"invalidClaim",
  CONFLICT_REVIEW_INVALID:"conflictReviewIncomplete", ONBOARDING_SOURCE_TYPE_INVALID:"sourceTypeUnsupported",
  ONBOARDING_SOURCE_EXTENSION_INVALID:"sourceTypeUnsupported", CHATGPT_EXPORT_FORMAT_INVALID:"sourceTypeUnsupported",
  ONBOARDING_SOURCE_SIZE_INVALID:"sourceSizeInvalid", REQUEST_SIZE_INVALID:"sourceSizeInvalid"
};
const BLOCKING_CODES=new Set([
  "SERVICE_RESTART_REQUIRED","PROFILE_REVIEW_REQUIRED","ONBOARDING_ANSWERS_INCOMPLETE",
  "ONBOARDING_HARD_CONDITIONS_UNRESOLVED","SOURCE_PREVIEW_PENDING","SOURCE_AI_REANALYSIS_REQUIRED",
  "CLAIM_REVIEW_INCOMPLETE","CONFLICT_REVIEW_INCOMPLETE","ONBOARDING_CONFIRMATION_REQUIRED",
  "ONBOARDING_ALREADY_COMPLETE","ONBOARDING_REVISION_REQUIRED","ONBOARDING_ANSWER_REQUIRED",
  "ONBOARDING_ANSWER_INVALID","CLAIM_EDIT_INVALID","CLAIM_REVIEW_INVALID","CONFLICT_REVIEW_INVALID"
]);

function localizedErrorMessage(error) { return t(LOCAL_ERROR_KEYS[error?.code] || "localRequestFailed"); }
function clearAttention(){document.querySelectorAll(".needs-attention").forEach(el=>el.classList.remove("needs-attention"));}
function hideBlockingNotice(){const notice=document.querySelector("#blockingNotice");state.lastBlockingError=null;notice.classList.add("hidden");document.querySelector("#blockingNoticeTitle").textContent="";document.querySelector("#blockingNoticeBody").textContent="";}
function showBlockingNotice(message){const notice=document.querySelector("#blockingNotice");document.querySelector("#blockingNoticeTitle").textContent=t("attentionRequired");document.querySelector("#blockingNoticeBody").textContent=message;notice.classList.remove("hidden");notice.scrollIntoView({behavior:"smooth",block:"center"});}

function focusBlockingError(error) {
  clearAttention();
  const code=error?.code, details=error?.details||{};
  let target=null, element=null;
  if(["ONBOARDING_ANSWERS_INCOMPLETE","ONBOARDING_HARD_CONDITIONS_UNRESOLVED","ONBOARDING_ANSWER_REQUIRED","ONBOARDING_ANSWER_INVALID"].includes(code)){
    target="questionnaire";
    const ids=Array.isArray(details.fields)?details.fields:(details.field_id?[details.field_id]:[]);
    if(ids.length)element=document.querySelector(`[data-question="${CSS.escape(String(ids[0]))}"]`);
  }else if(code==="PROFILE_REVIEW_REQUIRED"){target="review";element=document.querySelector(".profile-confirm");}
  else if(code==="CLAIM_REVIEW_INCOMPLETE"||code==="CLAIM_REVIEW_INVALID"||code==="CLAIM_EDIT_INVALID"){target="review";element=[...document.querySelectorAll(".claim-decision")].find(item=>item.value==="PENDING")?.closest(".claim-row")||document.querySelector(".claim-row");}
  else if(code==="CONFLICT_REVIEW_INCOMPLETE"||code==="CONFLICT_REVIEW_INVALID"){target="review";element=[...document.querySelectorAll(".conflict-resolution")].find(item=>!item.value)?.closest(".conflict-card");}
  else if(code==="SOURCE_PREVIEW_PENDING"){target="sources";element=document.querySelector("#sourcePreviewBox");}
  else if(code==="SOURCE_AI_REANALYSIS_REQUIRED"){target="sources";element=document.querySelector("#sourceList");}
  else if(code==="ONBOARDING_CONFIRMATION_REQUIRED"){target="finish";element=document.querySelector(".final-confirm");}
  else if(code==="ONBOARDING_REVISION_REQUIRED"||code==="ONBOARDING_ALREADY_COMPLETE"){element=document.querySelector("#stateBanner");}
  if(target&&state.data)navigate(target);
  if(element)setTimeout(()=>{element.classList.add("needs-attention");element.scrollIntoView({behavior:"smooth",block:"center"});element.querySelector?.("input,select,textarea,button")?.focus({preventScroll:true});},80);
}

function handleUiError(error) {
  const message=localizedErrorMessage(error);
  showToast(message,true,9000);
  if(BLOCKING_CODES.has(error?.code)){state.lastBlockingError=error;showBlockingNotice(message);focusBlockingError(error);}
  return message;
}

function formatBytes(value) {
  const bytes=Math.max(0,Number(value)||0), units=["B","KB","MB","GB"];
  let amount=bytes,index=0;
  while(amount>=1024&&index<units.length-1){amount/=1024;index+=1;}
  return `${amount>=100||index===0?Math.round(amount):amount.toFixed(1)} ${units[index]}`;
}

function renderActivity() {
  const indicator=document.querySelector("#activityIndicator"), main=document.querySelector("main");
  const activity=state.activities[state.activities.length-1];
  if(!activity){indicator.classList.add("hidden");document.body.classList.remove("is-busy");main?.removeAttribute("aria-busy");return;}
  const seconds=Math.max(0,Math.floor((Date.now()-activity.started)/1000));
  const progressBar=document.querySelector("#activityProgress");
  indicator.classList.remove("hidden"); document.body.classList.add("is-busy"); main?.setAttribute("aria-busy","true");
  document.querySelector("#activityTitle").textContent=t(activity.key);
  if(activity.phase==="uploading"){
    const loaded=Math.max(0,Number(activity.loadedBytes)||0),total=Math.max(1,Number(activity.totalBytes)||1);
    const percent=Math.max(0,Math.min(100,Math.round(loaded/total*100)));
    const preciseElapsed=Math.max(.25,(Date.now()-(activity.uploadStarted||activity.started))/1000);
    const speed=loaded/preciseElapsed;
    const remaining=speed>0&&loaded<total?Math.max(1,Math.ceil((total-loaded)/speed)):0;
    document.querySelector("#activityStage").textContent=t("uploadStage").replace("{percent}",percent);
    document.querySelector("#activityElapsed").textContent=loaded>0&&remaining
      ? t("uploadEta").replace("{loaded}",formatBytes(loaded)).replace("{total}",formatBytes(total)).replace("{remaining}",remaining)
      : t("uploadMeasuring").replace("{loaded}",formatBytes(loaded)).replace("{total}",formatBytes(total));
    progressBar.classList.remove("indeterminate");
    progressBar.style.width=`${Math.max(4,percent)}%`;
    return;
  }
  if(activity.phase==="processing"){
    const phaseSeconds=Math.max(0,Math.floor((Date.now()-(activity.phaseStarted||activity.started))/1000));
    const minMinutes=Math.max(1,Number(activity.estimateMinMinutes)||1);
    const maxMinutes=Math.max(minMinutes,Number(activity.estimateMaxMinutes)||8);
    document.querySelector("#activityStage").textContent=t(activity.largeMode?"lightningAnalysisStage":"aiAnalysisStage");
    document.querySelector("#activityElapsed").textContent=phaseSeconds>maxMinutes*60
      ? t("aiAnalysisOverdue").replace("{elapsed}",phaseSeconds)
      : t("aiAnalysisRange").replace("{elapsed}",phaseSeconds).replace("{min}",minMinutes).replace("{max}",maxMinutes);
    progressBar.classList.add("indeterminate");
    progressBar.style.width="38%";
    return;
  }
  const baseEstimate=Math.max(1,Number(activity.estimatedSeconds)||0);
  const remaining=baseEstimate&&seconds<baseEstimate?Math.max(1,Math.ceil(baseEstimate-seconds)):null;
  const progress=activity.total
    ? Math.min(96,((activity.completed||0)+Math.min(.9,seconds/Math.max(1,activity.currentEstimate||baseEstimate)))/activity.total*100)
    : (baseEstimate?Math.min(94,seconds/baseEstimate*94):18);
  document.querySelector("#activityStage").textContent=activity.total
    ? t("batchProgress").replace("{current}",Math.min(activity.total,(activity.completed||0)+1)).replace("{total}",activity.total).replace("{completed}",activity.completed||0)
    : (seconds>=15?t("stillWorking"):"");
  document.querySelector("#activityElapsed").textContent=remaining===null
    ? t("longRunningNoCountdown")
    : t("elapsedWithEstimate").replace("{elapsed}",seconds).replace("{remaining}",remaining);
  progressBar.classList.remove("indeterminate");
  progressBar.style.width=`${Math.max(4,progress)}%`;
}
function beginActivity(key,options={}){const id=++activitySequence;state.activities.push({id,key,started:Date.now(),estimatedSeconds:options.estimatedSeconds??ACTIVITY_ESTIMATES[key]??0,total:options.total??0,completed:options.completed??0,currentEstimate:options.currentEstimate??0});if(!activityTimer)activityTimer=setInterval(renderActivity,1000);renderActivity();return id;}
function updateActivity(id,patch){const activity=state.activities.find(item=>item.id===id);if(activity)Object.assign(activity,patch);renderActivity();}
function endActivity(id){state.activities=state.activities.filter(item=>item.id!==id);if(!state.activities.length&&activityTimer){clearInterval(activityTimer);activityTimer=null;}renderActivity();}
async function withActivity(key,operation,options={}){const id=beginActivity(key,options);try{return await operation(id);}finally{endActivity(id);}}

async function api(path, options={}) {
  if(path.split("?",1)[0]!=="bootstrap"&&!state.serviceCompatible) throw makeUiError("SERVICE_RESTART_REQUIRED");
  const headers = Object.assign({"X-JobOps-Session": sessionToken}, options.headers || {});
  const response = await fetch(base + "api/" + path, Object.assign({cache:"no-store", credentials:"same-origin"}, options, {headers}));
  const raw = await response.text();
  let body;
  try { body = raw ? JSON.parse(raw) : {}; }
  catch (_) {
    const error = new Error(t("invalidLocalResponse"));
    error.code = "LOCAL_RESPONSE_INVALID";
    throw error;
  }
  if (!response.ok) {
    const error = new Error(body.message || body.code || "Request failed");
    error.code = body.code;
    error.details = body.details || {};
    throw error;
  }
  return body;
}

function uploadApi(path,file,{onProgress,onUploaded}={}){
  return new Promise((resolve,reject)=>{
    if(!state.serviceCompatible){reject(makeUiError("SERVICE_RESTART_REQUIRED"));return;}
    const xhr=new XMLHttpRequest();
    xhr.open("POST",base+"api/"+path,true);
    xhr.setRequestHeader("X-JobOps-Session",sessionToken);
    xhr.setRequestHeader("Content-Type","application/octet-stream");
    xhr.upload.addEventListener("progress",event=>onProgress?.(event.loaded,event.lengthComputable?event.total:file.size));
    xhr.upload.addEventListener("load",()=>onUploaded?.());
    xhr.addEventListener("error",()=>{const error=new Error(t("uploadInterrupted"));error.code="ONBOARDING_UPLOAD_INTERRUPTED";reject(error);});
    xhr.addEventListener("abort",()=>{const error=new Error(t("uploadInterrupted"));error.code="ONBOARDING_UPLOAD_INTERRUPTED";reject(error);});
    xhr.addEventListener("load",()=>{
      let body={};
      try{body=xhr.responseText?JSON.parse(xhr.responseText):{};}
      catch(_){const error=new Error(t("invalidLocalResponse"));error.code="LOCAL_RESPONSE_INVALID";reject(error);return;}
      if(xhr.status<200||xhr.status>=300){const error=new Error(body.message||body.code||"Request failed");error.code=body.code;error.details=body.details;reject(error);return;}
      resolve(body);
    });
    xhr.send(file);
  });
}

function aiConnectionErrorMessage(error) {
  if(error?.code==="SERVICE_RESTART_REQUIRED")return localizedErrorMessage(error);
  const byCode = {
    AI_WSL_HERMES_AUTH_REQUIRED: "aiWslHermesAuthRequired",
    AI_WSL_PROXY_START_FAILED: "aiWslProxyStartFailed",
    AI_WSL_LOCAL_BRIDGE_MISSING: "aiWslBridgeMissing",
    AI_AGENT_TOOL_AUDIT_MISSING: "aiAgentSafetyRejected",
    AI_AGENT_TOOL_CALL_BLOCKED: "aiAgentSafetyRejected"
  };
  return t(byCode[error?.code] || "aiConnectionFailed");
}

function sourceAnalysisErrorMessage(error, sourceType="") {
  if(error?.code==="SERVICE_RESTART_REQUIRED")return localizedErrorMessage(error);
  if (["AI_RESPONSE_INVALID", "AI_RESPONSE_REPAIR_FAILED"].includes(error?.code)) return t(sourceType.startsWith("chatgpt_export")?"aiExportRepairFailed":"aiRepairFailed");
  if(error?.code==="AI_ENGINE_REQUIRED")return t("aiEngineMissingBody");
  if(error?.code==="CHATGPT_EXPORT_LIGHTNING_REQUIRED")return t("selectLightning");
  if(error?.code==="CHATGPT_EXPORT_FORMAT_INVALID")return t("lightningZipOnly");
  if(error?.code==="ONBOARDING_UPLOAD_INTERRUPTED")return t("uploadInterrupted");
  return LOCAL_ERROR_KEYS[error?.code]?localizedErrorMessage(error):t("uploadFailed");
}

function applyLocale() {
  document.documentElement.lang = state.locale === "zh" ? "zh" : "en";
  document.title=t("pageTitle");
  document.querySelectorAll("[data-i18n]").forEach(el => el.textContent = t(el.dataset.i18n));
  document.querySelectorAll("[data-locale]").forEach(el => el.classList.toggle("active", el.dataset.locale === state.locale));
  if(state.lastBlockingError&&!document.querySelector("#blockingNotice").classList.contains("hidden")){
    document.querySelector("#blockingNoticeTitle").textContent=t("attentionRequired");
    document.querySelector("#blockingNoticeBody").textContent=localizedErrorMessage(state.lastBlockingError);
  }
  if(state.data)renderDashboard();
  if(state.reviewPacket)renderReviewPacket();
  renderActivity();
}

function queueStatusLabel(status){
  const keys={APPROVED:"statusApproved",CLOSED:"statusClosed",MATERIALS_NEEDS_CORRECTION:"statusRevision",DEFERRED:"statusDeferred"};
  return keys[status]?t(keys[status]):t("statusOther").replace("{status}",status||"—");
}

function renderDashboard(){
  const dashboard=state.data?.dashboard;
  if(!dashboard)return;
  const queue=dashboard.queue||{}, safety=dashboard.safety||{}, pending=dashboard.pending_applications||[], deferred=dashboard.deferred_intake||[], recent=dashboard.recent_applications||[];
  document.querySelector("#metricOnboarding").textContent=dashboard.onboarding_status==="ONBOARDING_COMPLETE"?t("pipelineReady"):t("pipelineNeedsSetup");
  document.querySelector("#metricAi").textContent=state.data.ai_engine?.status==="READY"?t("aiReadyShort"):t("aiMissingShort");
  document.querySelector("#metricAwaiting").textContent=String(queue.awaiting_approval||0);
  document.querySelector("#metricSlots").textContent=String(queue.slots_available||0);
  document.querySelector("#metricLimit").textContent=t("queueLimit").replace("{limit}",queue.pending_limit||0);
  document.querySelector("#metricDeferred").textContent=String(queue.deferred_intake||0);
  const limitInput=document.querySelector("#pendingLimitInput");if(document.activeElement!==limitInput)limitInput.value=String(queue.pending_limit||10);
  document.querySelector("#pendingDashboardCount").textContent=String(pending.length);
  const safe=safety.real_website_accesses===0&&safety.real_external_actions===0&&safety.knowledge_write_operations===0;
  const guard=document.querySelector("#pipelineGuard");guard.textContent=t("safetyGuardOn");guard.classList.toggle("unsafe",!safe);
  document.querySelector("#safetySites").textContent=String(safety.real_website_accesses||0);
  document.querySelector("#safetyActions").textContent=String(safety.real_external_actions||0);
  document.querySelector("#safetyKnowledge").textContent=String(safety.knowledge_write_operations||0);
  document.querySelector("#safetyMode").textContent=t("offlineMode");
  const list=document.querySelector("#pendingDashboardList");
  list.classList.toggle("empty",!pending.length);
  list.innerHTML=pending.length?pending.map(item=>`<article class="pending-dashboard-item" data-application="${escapeHtml(item.application_id)}"><div><strong>${escapeHtml(item.title)} · ${escapeHtml(item.company)}</strong><small>${escapeHtml([item.location,item.application_id].filter(Boolean).join(" · "))}</small></div><div class="pending-dashboard-item-meta"><b>${escapeHtml(t("awaitingApprovalStatus"))}</b><small>${escapeHtml(t("packetHash").replace("{hash}",item.packet_hash_prefix||"—"))}</small><button class="secondary compact open-review-packet" type="button" data-id="${escapeHtml(item.application_id)}">${escapeHtml(t("viewPacket"))}</button></div></article>`).join(""):`<p>${escapeHtml(t("pendingEmpty"))}</p>`;
  document.querySelector("#deferredDashboardCount").textContent=String(deferred.length);
  const deferredList=document.querySelector("#deferredDashboardList");deferredList.classList.toggle("empty",!deferred.length);
  deferredList.innerHTML=deferred.length?deferred.map(item=>`<article class="compact-dashboard-item"><div><strong>${escapeHtml(t("safeQueueId"))}: ${escapeHtml(item.safe_intake_id)}</strong><small>${escapeHtml(item.source_type)} · ${escapeHtml(t("queuedAt"))}: ${escapeHtml(item.created_at)}</small></div><aside><b>${escapeHtml(queueStatusLabel(item.status))}</b></aside></article>`).join(""):`<p>${escapeHtml(t("deferredEmpty"))}</p>`;
  document.querySelector("#recentDashboardCount").textContent=String(recent.length);
  const recentList=document.querySelector("#recentDashboardList");recentList.classList.toggle("empty",!recent.length);
  recentList.innerHTML=recent.length?recent.map(item=>`<article class="compact-dashboard-item"><div><strong>${escapeHtml(item.title)} · ${escapeHtml(item.company)}</strong><small>${escapeHtml([item.location,item.application_id,item.updated_at].filter(Boolean).join(" · "))}</small>${item.approval_expires_at?`<small>${escapeHtml(t("approvalExpiry").replace("{time}",item.approval_expires_at))}</small>`:""}</div><aside><b>${escapeHtml(queueStatusLabel(item.status))}</b>${item.packet_id?`<button class="secondary compact open-review-packet" type="button" data-id="${escapeHtml(item.application_id)}">${escapeHtml(t("viewRecord"))}</button>`:""}</aside></article>`).join(""):`<p>${escapeHtml(t("recentEmpty"))}</p>`;
}

function packetValue(value){
  if(value===null||value===undefined||value==="")return t("packetNone");
  if(Array.isArray(value))return value.map(packetValue).join(", ");
  if(typeof value==="object")return String(value.label||value.name||value.id||value.status||JSON.stringify(value)).slice(0,500);
  return String(value).slice(0,500);
}
function packetList(items,renderer){
  return Array.isArray(items)&&items.length?`<ul>${items.map(item=>`<li>${renderer(item)}</li>`).join("")}</ul>`:`<p>${escapeHtml(t("packetNone"))}</p>`;
}
function renderReviewPacket(){
  const result=state.reviewPacket;if(!result)return;
  const packet=result.packet||{},job=packet.job||result.job_summary||{},fit=packet.fit||{},route=packet.source_route||{};
  document.querySelector("#reviewPacketTitle").textContent=`${packetValue(job.title)} · ${packetValue(job.company)}`;
  document.querySelector("#reviewPacketMeta").textContent=`${result.application_id} · ${t("packetStatus")}: ${result.status} · ${t("packetCreated")}: ${result.created_at}`;
  const sections=[
    ["packetJob",`<dl class="packet-kv"><dt>ID</dt><dd>${escapeHtml(packetValue(job.job_id))}</dd><dt>${escapeHtml(t("packetStatus"))}</dt><dd>${escapeHtml(result.application_status)}</dd><dt>URL</dt><dd>${escapeHtml(packetValue(job.official_url))}</dd></dl>`,false],
    ["packetFit",`<dl class="packet-kv"><dt>${escapeHtml(t("packetOverall"))}</dt><dd>${escapeHtml(packetValue(fit.overall_score))}</dd><dt>${escapeHtml(t("packetStatus"))}</dt><dd>${escapeHtml(packetValue(fit.recommendation||fit.eligibility_status))}</dd></dl>${packetList(fit.explanation,item=>escapeHtml(packetValue(item)))}`,false],
    ["packetGaps",packetList(packet.hard_gaps,item=>escapeHtml(packetValue(item))),false],
    ["packetBullets",packetList(packet.resume_bullets,item=>`<strong>${escapeHtml(packetValue(item.text))}</strong><br><small>${escapeHtml(t("packetClaims"))}: ${escapeHtml(packetValue(item.claim_id))} · ${escapeHtml(t("packetEvidence"))}: ${escapeHtml(packetValue(item.evidence))}</small>`),true],
    ["packetQuestions",packetList(packet.form_questions,item=>`${escapeHtml(packetValue(item.label||item.id))} · ${escapeHtml(packetValue(item.classification||item.action||item.status))}`),false],
    ["packetSensitive",packetList(packet.sensitive_fields,item=>`${escapeHtml(packetValue(item.label||item.id))} · ${escapeHtml(packetValue(item.classification||item.action||item.status))}`),false],
    ["packetUploads",packetList(packet.uploads,item=>`${escapeHtml(packetValue(item.filename))} · ${escapeHtml(packetValue(item.purpose))} · ${escapeHtml(packetValue(item.sha256).slice(0,15))}`),false],
    ["packetActions",packetList(packet.external_actions,item=>escapeHtml(packetValue(item))),false],
    ["packetRoute",`<dl class="packet-kv"><dt>Route</dt><dd>${escapeHtml(packetValue(route.route_kind))}</dd><dt>Guest</dt><dd>${escapeHtml(packetValue(route.guest_mode))}</dd><dt>Account</dt><dd>${escapeHtml(packetValue(route.account_action))}</dd></dl>`,false]
  ];
  document.querySelector("#reviewPacketBody").innerHTML=sections.map(([key,body,wide])=>`<section class="packet-section${wide?" wide":""}"><h4>${escapeHtml(t(key))}</h4>${body}</section>`).join("");
  document.querySelectorAll('input[name="packetDecision"]').forEach(input=>{input.checked=input.value===state.reviewDecision;});
  const confirmation=document.querySelector("#packetDecisionConfirm");confirmation.checked=state.reviewDecisionConfirmed;
  document.querySelector("#confirmPacketDecision").disabled=!state.reviewDecision||!state.reviewDecisionConfirmed;
  const canDecide=result.application_status==="AWAITING_APPROVAL"&&result.status==="AWAITING_APPROVAL";
  document.querySelector("#packetDecisionPanel").classList.toggle("hidden",!canDecide);
  if(!canDecide){state.reviewDecision="";state.reviewDecisionConfirmed=false;}
  const panel=document.querySelector("#reviewPacketPanel");panel.classList.remove("hidden");
}

function navigate(target) {
  document.querySelectorAll(".panel").forEach(el => el.classList.toggle("active-panel", el.id === target));
  document.querySelectorAll(".step").forEach(el => el.classList.toggle("active", el.dataset.target === target));
  document.querySelector(`#${target}`).scrollIntoView({behavior:"smooth", block:"start"});
  if (target === "finish") renderReadiness();
}

function statusOptions(selected) {
  const items = [["UNKNOWN",t("unknown")],["CONFIRMED",t("confirmed")],["PREFER_NOT_TO_ANSWER",t("preferNot")],["NOT_APPLICABLE",t("notApplicable")]];
  return items.map(([v,l])=>`<option value="${v}" ${v===selected?"selected":""}>${escapeHtml(l)}</option>`).join("");
}
function policyOptions(selected) {
  const items = [["reuse",t("reuse")],["confirm_each_application",t("confirmEach")],["prefer_not_to_answer",t("preferPolicy")],["do_not_store",t("doNotStore")]];
  return items.map(([v,l])=>`<option value="${v}" ${v===selected?"selected":""}>${escapeHtml(l)}</option>`).join("");
}

const CLAIM_CATEGORIES=["work","internship","education","project","skill","certification","language","summary"];
const ENTITY_CATEGORIES=new Set(["work","internship","education","project"]);
function categoryLabel(category){return t(CLAIM_CATEGORIES.includes(category)?category:"summary");}
function categoryOptions(selected, entityBound=false){
  const allowed=entityBound?CLAIM_CATEGORIES.filter(item=>ENTITY_CATEGORIES.has(item)):CLAIM_CATEGORIES;
  return allowed.map(item=>`<option value="${item}" ${item===selected?"selected":""}>${escapeHtml(categoryLabel(item))}</option>`).join("");
}
function entityLabel(item){
  const entity=item?.entity||{};
  return [entity.organization,entity.role,[entity.start_date,entity.end_date].filter(Boolean).join(" – ")].filter(Boolean).join(" · ") || t("entityUnknown");
}

function inputControl(field, answer) {
  const value = Array.isArray(answer.value) ? answer.value.join(", ") : (answer.value || "");
  const disabled=disabledAttr(isReadonly());
  if (field.input_type === "select") {
    const opts = [`<option value=""></option>`, ...field.options.map(item=>`<option value="${escapeHtml(item.value)}" ${item.value===value?"selected":""}>${escapeHtml(item.label[state.locale])}</option>`)].join("");
    return `<select class="answer-input" data-field="${field.id}"${disabled}>${opts}</select>`;
  }
  if (field.input_type === "textarea") return `<textarea class="answer-input" data-field="${field.id}"${disabled}>${escapeHtml(value)}</textarea>`;
  return `<input type="text" class="answer-input" data-field="${field.id}" value="${escapeHtml(value)}"${disabled}>`;
}

function collectAnswerDraft() {
  if (!state.data) return {};
  const answers={};
  document.querySelectorAll("[data-question]").forEach(row=>{
    const id=row.dataset.question, field=state.data.catalog.fields.find(item=>item.id===id);
    let value=row.querySelector(".answer-input").value;
    if(field.input_type==="tags") value=value.split(/[,，;；|\n]/).map(x=>x.trim()).filter(Boolean);
    let status=row.querySelector(".status-select").value;
    if(status==="UNKNOWN" && (Array.isArray(value)?value.length>0:String(value||"").trim().length>0)) status="CONFIRMED";
    answers[id]={value,status,use_policy:row.querySelector(".policy-select")?.value || state.data.answers[id].use_policy};
  });
  return answers;
}

function renderQuestions() {
  const catalog = state.data.catalog;
  const groups = catalog.groups.map(group => {
    const rows = catalog.fields.filter(f=>f.group===group.id).map(field => {
      const answer = state.data.answers[field.id];
      return `<div class="question-row" data-question="${field.id}">
        <div class="question-copy"><label>${escapeHtml(field.label[state.locale])}</label><small>${escapeHtml(field.help[state.locale] || "")}</small></div>
        <div>${inputControl(field, answer)}${field.sensitive?`<div class="policy-row"><span>${escapeHtml(t("policy"))}</span><select class="policy-select" data-field="${field.id}"${disabledAttr(isReadonly())}>${policyOptions(answer.use_policy)}</select></div>`:""}</div>
        <select class="status-select" data-field="${field.id}"${disabledAttr(isReadonly())}>${statusOptions(answer.status)}</select>
      </div>`;
    }).join("");
    return `<section class="question-group"><h3>${escapeHtml(group.label[state.locale])}</h3>${rows}</section>`;
  }).join("");
  document.querySelector("#questionGroups").innerHTML = groups;
}

function renderAiConnection() {
  const engine=state.data?.ai_engine||{}, ready=engine.status==="READY";
  const selected=state.data?.ai_connection?.selected||{};
  const button=document.querySelector("#aiConnectButton"), status=document.querySelector("#aiConnectionStatus");
  button.classList.toggle("connected",ready);
  button.textContent=t(ready?"aiConnectedButton":"connectAi");
  status.classList.toggle("ready",ready);
  if(!ready){status.textContent=t("aiNotConnectedStatus");return;}
  const name=selected.display_name||engine.display_name||engine.provider||"AI";
  const model=selected.model||engine.model||"configured";
  const route=selected.data_route||engine.data_route||engine.private_transport||"configured";
  status.innerHTML=`<strong>${escapeHtml(t("aiConnectedStatus").replace("{name}",name))}</strong><small>${escapeHtml(t("aiConnectedModel").replace("{model}",model).replace("{route}",route))}</small>`;
}

function renderSources() {
  const ai=state.data.ai_engine||{};
  const aiReady=ai.status==="READY";
  const readonly=isReadonly();
  renderAiConnection();
  const aiBanner=document.querySelector("#aiEngineBanner");
  aiBanner.classList.toggle("ready",aiReady);
  document.querySelector("#aiEngineTitle").textContent=t(aiReady?"aiEngineReady":"aiEngineMissing");
  document.querySelector("#aiEngineBody").textContent=t(aiReady?"aiEngineReadyBody":"aiEngineMissingBody");
  const list = document.querySelector("#sourceList");
  document.querySelector("#sourceCount").textContent = state.data.sources.length;
  const bulkButton=document.querySelector("#analyzeAllSources");
  const retainedCount=state.data.sources.filter(item=>item.raw_retained).length;
  bulkButton.disabled=readonly||!aiReady||retainedCount===0;
  if (!state.data.sources.length) { list.className="source-list empty"; list.textContent=t("noSources"); }
  else {
    list.className="source-list";
    list.innerHTML=state.data.sources.map(item=>{
      const passed=item.analysis_mode==="AI_CORE_ENTITY_ANALYSIS";
      const analysisLabel=passed?t("analysisPassed"):(item.raw_retained?t("analysisMissing"):t("reuploadRequired"));
      const countLabel=passed?`${item.fact_count} ${t("claimCandidates")}`:analysisLabel;
      const reupload=item.category==="chatgpt_export"&&!item.raw_retained&&!readonly;
      return `<div class="source-entry"><div><strong>${escapeHtml(item.safe_display_name)}</strong><small>${escapeHtml(item.category)} · ${escapeHtml(countLabel)}${passed?` · ${escapeHtml(analysisLabel)}`:""}</small></div><div class="source-actions"><span class="status-chip ${passed?"analysis-passed":"analysis-needed"}">${escapeHtml(analysisLabel)}</span>${item.raw_retained&&!readonly?`<button class="text-action reprocess-source" data-id="${escapeHtml(item.source_id)}"${disabledAttr(!aiReady)}>${escapeHtml(t("reprocess"))}</button>`:""}${reupload?`<button class="text-action reupload-source" data-id="${escapeHtml(item.source_id)}"${disabledAttr(!aiReady)}>${escapeHtml(t("reuploadAndAnalyze"))}</button>`:""}${!readonly?`<button class="text-action danger remove-source" data-id="${escapeHtml(item.source_id)}">${escapeHtml(t("deleteSource"))}</button>`:""}</div></div>`;
    }).join("");
  }
  renderSourcePreviews();
  const pending=state.data.suggestions.filter(item=>!item.accepted);
  document.querySelector("#suggestionBox").classList.toggle("hidden", !pending.length);
  document.querySelector("#suggestionCount").textContent=pending.length;
  document.querySelector("#suggestionList").innerHTML=pending.map(item=>`<div class="suggestion-entry"><div><strong>${escapeHtml(item.field_id)}</strong><small>${escapeHtml(Array.isArray(item.value)?item.value.join(", "):item.value)} · ${escapeHtml(item.source_status)}</small></div><button class="secondary accept-suggestion" data-id="${escapeHtml(item.suggestion_id)}"${disabledAttr(isReadonly())}>${escapeHtml(t("accept"))}</button></div>`).join("");
}

function renderSourcePreviews(){
  const box=document.querySelector("#sourcePreviewBox"), target=document.querySelector("#sourcePreviewList");
  const previews=state.data.pending_sources||[];
  box.classList.toggle("hidden",!previews.length); document.querySelector("#previewCount").textContent=previews.length;
  target.innerHTML=previews.map(preview=>{
    const candidates=preview.candidates||[], meta=preview.metadata||{};
    const summary=preview.extraction_summary||{};
    const aiPassed=summary.analysis_mode==="AI_CORE_ENTITY_ANALYSIS";
    return `<article class="source-preview" data-preview-source="${escapeHtml(preview.source_id)}">
      <header><div><strong>${escapeHtml(meta.safe_display_name||preview.source_id)}</strong><small>${summary.raw_lines||0} lines → ${summary.reconstructed_blocks||0} blocks → ${candidates.length} candidates · ${escapeHtml(summary.analysis_mode||"NOT_APPLICABLE")}${summary.ai_repair_attempted?` · ${escapeHtml(t("aiRepairApplied"))}`:""}</small></div><span class="status-chip">${escapeHtml(meta.source_status||"")}</span></header>
      ${candidates.length?`<div class="preview-bulk-actions"><button class="text-action select-all-preview" data-id="${escapeHtml(preview.source_id)}">${escapeHtml(t("selectAllClaims"))}</button><button class="text-action clear-all-preview" data-id="${escapeHtml(preview.source_id)}">${escapeHtml(t("clearAllClaims"))}</button></div>`:""}
      <div class="preview-candidates">${candidates.length?candidates.map(item=>`<label class="preview-candidate"><span class="preview-choice"><input class="preview-select" type="checkbox" data-candidate="${escapeHtml(item.candidate_id)}" ${item.selected?"checked":""}><small>${escapeHtml(t("includeAsClaim"))}</small></span><span>${item.entity?`<strong class="entity-label">${escapeHtml(categoryLabel(item.category))} · ${escapeHtml(entityLabel(item))}</strong>`:""}<textarea class="preview-statement" aria-label="${escapeHtml(t("editText"))}">${escapeHtml(item.statement)}</textarea><span class="preview-meta"><select class="preview-category" aria-label="${escapeHtml(t("category"))}">${categoryOptions(item.category,Boolean(item.entity))}</select><small>${escapeHtml(item.selection_reason==="AI_DERIVED_REQUIRES_CONFIRMATION"?t("selectedByDefault"):t("needsReview"))} · lines ${item.provenance?.line_start||"—"}–${item.provenance?.line_end||"—"}</small></span></span></label>`).join(""):`<p class="preview-empty">${escapeHtml(t("previewEmpty"))}</p>`}</div>
      <footer><button class="secondary discard-preview" data-id="${escapeHtml(preview.source_id)}">${escapeHtml(t("discardPreview"))}</button><button class="secondary commit-preview" data-id="${escapeHtml(preview.source_id)}"${disabledAttr(!aiPassed)}>${escapeHtml(t("confirmSource"))}</button><button class="primary include-all-preview" data-id="${escapeHtml(preview.source_id)}"${disabledAttr(!aiPassed||!candidates.length)}>${escapeHtml(t("includeAllClaims"))}</button></footer>
    </article>`;
  }).join("");
}

function renderClaims() {
  const quality=state.data.claim_quality||{};
  const qualityBanner=document.querySelector("#claimQualityBanner");
  if((quality.quarantined_legacy_claims||0)>0){qualityBanner.classList.remove("hidden");qualityBanner.textContent=t("legacyQuarantined").replace("{count}",quality.quarantined_legacy_claims);}else{qualityBanner.classList.add("hidden");qualityBanner.textContent="";}
  const byCategory={};
  state.data.claims.forEach(item => { (byCategory[item.category] ||= []).push(item); state.claimDraft[item.claim_id]=item.decision; });
  document.querySelector("#claimGroups").innerHTML=Object.entries(byCategory).map(([category, claims])=>`<section class="claim-group" data-category="${escapeHtml(category)}">
    <div class="claim-group-header"><div><h3>${escapeHtml(categoryLabel(category))} · ${claims.filter(item=>!item.deleted).length} ${escapeHtml(t("entityClaims"))}</h3><small>${escapeHtml(t("claimEditHelp"))}</small></div><div class="claim-group-actions"><button data-batch="CONFIRMED"${disabledAttr(isReadonly())}>${escapeHtml(t("confirmAll"))}</button><button data-batch="REJECTED"${disabledAttr(isReadonly())}>${escapeHtml(t("rejectAll"))}</button></div></div>
    ${claims.map(item=>`<div class="claim-row ${item.conflict?"claim-row-conflict":""} ${item.deleted?"claim-row-deleted":""}" data-claim-row="${escapeHtml(item.claim_id)}">
      <label class="claim-merge-picker"><input class="claim-merge-select" type="checkbox" data-claim="${escapeHtml(item.claim_id)}" aria-label="${escapeHtml(t("selectForMerge"))}"${disabledAttr(isReadonly()||item.deleted)}><span>${escapeHtml(t("selectForMerge"))}</span></label>
      <div class="claim-edit-area">${item.entity?`<strong class="entity-label">${escapeHtml(entityLabel(item))}</strong>`:""}<textarea class="claim-statement" data-claim="${escapeHtml(item.claim_id)}" aria-label="${escapeHtml(t("editText"))}"${disabledAttr(isReadonly()||item.deleted)}>${escapeHtml(item.statement || "")}</textarea>
        <div class="claim-meta-row"><select class="claim-category" data-claim="${escapeHtml(item.claim_id)}" aria-label="${escapeHtml(t("category"))}"${disabledAttr(isReadonly()||item.deleted)}>${categoryOptions(item.category,Boolean(item.entity))}</select><small>${escapeHtml(item.lifecycle_status)} · ${item.evidence_count} ${escapeHtml(t("evidence"))}${item.deleted?` · ${escapeHtml(t("rejected"))}`:""}</small></div>
        ${item.conflict?`<div class="claim-conflict-notice"><span>!</span><strong>${escapeHtml(t("conflictLabel"))}</strong><button type="button" class="conflict-jump" data-conflict-target="${escapeHtml(item.conflict_id)}">${escapeHtml(t("reviewThisConflict"))}</button></div>`:""}
        <div class="claim-inline-actions"><button type="button" class="text-action toggle-split" data-id="${escapeHtml(item.claim_id)}"${disabledAttr(isReadonly()||item.deleted)}>${escapeHtml(t("splitClaim"))}</button><button type="button" class="text-action toggle-delete" data-id="${escapeHtml(item.claim_id)}"${disabledAttr(isReadonly())}>${escapeHtml(item.deleted?t("restoreClaim"):t("deleteClaim"))}</button></div>
        <div class="claim-split-editor hidden"><small>${escapeHtml(t("splitHelp"))}</small><textarea class="split-statements"></textarea><button type="button" class="secondary apply-split" data-id="${escapeHtml(item.claim_id)}">${escapeHtml(t("applySplit"))}</button></div>
      </div>
      <select class="claim-decision" data-claim="${escapeHtml(item.claim_id)}"${disabledAttr(isReadonly()||item.deleted)}><option value="PENDING" ${item.decision==="PENDING"?"selected":""}>${escapeHtml(t("pending"))}</option><option value="CONFIRMED" ${item.decision==="CONFIRMED"?"selected":""}>${escapeHtml(t("confirmed"))}</option><option value="REJECTED" ${item.decision==="REJECTED"?"selected":""}>${escapeHtml(t("rejected"))}</option></select>
    </div>`).join("")}
  </section>`).join("");
  renderConflicts(); updateReviewProgress();
}

function fieldLabel(fieldId) {
  const field=state.data.catalog.fields.find(item=>item.id===fieldId);
  return field?.label?.[state.locale] || fieldId || "—";
}
function groupLabel(groupId) {
  const group=state.data.catalog.groups.find(item=>item.id===groupId);
  return group?.label?.[state.locale] || groupId || "—";
}
function conflictValue(value) { return Array.isArray(value)?value.join(", "):String(value ?? "—"); }

function renderConflicts() {
  const target=document.querySelector("#conflictList");
  const section=document.querySelector("#conflictSection");
  const qualityNote=document.querySelector("#conflictQualityNote"), suppressed=state.data.claim_quality?.suppressed_invalid_conflicts||0;
  qualityNote.classList.toggle("hidden",!suppressed);
  qualityNote.textContent=suppressed?t("invalidConflictsSuppressed").replace("{count}",suppressed):"";
  section.classList.toggle("all-clear",!state.data.conflicts.length);
  if (!state.data.conflicts.length) { target.innerHTML=`<p class="conflict-empty">${escapeHtml(t("noConflicts"))}</p>`; return; }
  target.innerHTML=state.data.conflicts.map((item,index)=>{
    const resolved=item.status==="RESOLVED" || Boolean(item.resolution);
    const difference=item.difference||{};
    const location=item.kind==="FIELD_VALUE_CONFLICT"
      ? `${t("affectedField")}: ${fieldLabel(item.field_id)} · ${groupLabel(item.group)}`
      : `${t("affectedClaim")}: ${categoryLabel(item.category)}${item.entity?` · ${entityLabel(item)}`:""} · ${difference.dimension||item.claim_kind||"value"}`;
    const reason=item.reason==="MULTIPLE_SOURCE_VALUES"?t("multipleValues"):t("valueDifference").replace("{dimension}",difference.dimension||"数值").replace("{left}",difference.resume_value||"—").replace("{right}",difference.evidence_value||"—");
    let comparison="";
    if(item.kind==="FIELD_VALUE_CONFLICT"){
      comparison=`<div class="conflict-comparison single-column">${(item.candidates||[]).map((candidate,candidateIndex)=>`<article><span>${escapeHtml(t("sourceCandidate"))} ${candidateIndex+1}</span><strong>${escapeHtml(conflictValue(candidate.value))}</strong><small>${escapeHtml(candidate.safe_source_name || candidate.source_id || "encrypted-source")} · ${escapeHtml(candidate.source_status || "")}</small></article>`).join("")}</div><button type="button" class="text-action conflict-field-jump" data-field-target="${escapeHtml(item.field_id)}">${escapeHtml(t("answerThisField"))}</button>`;
    }else{
      const evidence=(item.evidence_candidates||[]);
      comparison=`<div class="conflict-comparison"><article><span>${escapeHtml(t("resumeSide"))}</span><strong>${escapeHtml(difference.resume_value||"—")}</strong><small>${escapeHtml(item.resume_statement || "—")}</small></article><article><span>${escapeHtml(t("evidenceSide"))}</span><strong>${escapeHtml(difference.evidence_value||"—")}</strong>${evidence.length?evidence.map(candidate=>`<div class="evidence-preview"><small>${escapeHtml(candidate.summary||"")}</small><small>${escapeHtml(candidate.source_id || "personal-redacted")}${candidate.heading?` · ${escapeHtml(candidate.heading)}`:""}</small></div>`).join(""):`<small>${escapeHtml(t("noEvidencePreview"))}</small>`}</article></div>`;
    }
    return `<article id="conflict-${escapeHtml(item.conflict_id)}" class="conflict-card ${resolved?"resolved":"pending"}">
      <header><div><span class="conflict-index">${index+1}</span><strong>${escapeHtml(t("conflictLabel"))} ${index+1}</strong></div><span class="conflict-status">${escapeHtml(resolved?t("conflictResolved"):t("conflictPending"))}</span></header>
      <div class="conflict-meta"><p><b>${escapeHtml(t("conflictLocation"))}</b>${escapeHtml(location)}</p><p><b>${escapeHtml(t("conflictReason"))}</b>${escapeHtml(reason)}</p></div>
      ${comparison}
      <label class="conflict-decision"><span>${escapeHtml(t("chooseResolution"))}</span><select class="conflict-resolution" data-conflict="${escapeHtml(item.conflict_id)}"${disabledAttr(isReadonly())}><option value=""></option><option value="USE_RESUME" ${item.resolution==="USE_RESUME"?"selected":""}>${escapeHtml(t("useResume"))}</option><option value="USE_EVIDENCE" ${item.resolution==="USE_EVIDENCE"?"selected":""}>${escapeHtml(t("useEvidence"))}</option><option value="USE_DIRECT_ANSWER" ${item.resolution==="USE_DIRECT_ANSWER"?"selected":""}>${escapeHtml(t("useDirect"))}</option><option value="EXCLUDE" ${item.resolution==="EXCLUDE"?"selected":""}>${escapeHtml(t("exclude"))}</option></select></label>
    </article>`;
  }).join("");
}

function updateProgress() {
  const c=state.data.completion;
  document.querySelector("#progressText").textContent=`${c.resolved} / ${c.total}`;
  document.querySelector("#progressBar").style.width=`${c.percent}%`;
}
function updateReviewProgress() {
  const decisions=Object.values(state.claimDraft), reviewed=decisions.filter(v=>v!=="PENDING").length;
  const conflictValues=state.data.conflicts.map(item=>state.conflictDraft[item.conflict_id] || item.resolution).filter(Boolean);
  document.querySelector("#claimProgress").textContent=`${reviewed} / ${decisions.length}`;
  document.querySelector("#conflictProgress").textContent=`${conflictValues.length} / ${state.data.conflicts.length}`;
}
function currentReviewBlocker(){
  if(state.data.profile_review!=="CONFIRMED")return makeUiError("PROFILE_REVIEW_REQUIRED");
  if((state.data.pending_sources||[]).length)return makeUiError("SOURCE_PREVIEW_PENDING");
  if((state.data.sources||[]).some(item=>item.analysis_mode!=="AI_CORE_ENTITY_ANALYSIS"))return makeUiError("SOURCE_AI_REANALYSIS_REQUIRED");
  if(Object.values(state.claimDraft).some(value=>value==="PENDING"))return makeUiError("CLAIM_REVIEW_INCOMPLETE");
  if(state.data.conflicts.some(item=>!item.resolution))return makeUiError("CONFLICT_REVIEW_INCOMPLETE");
  return null;
}
function renderReadiness() {
  if (!state.data) return;
  const decisions=Object.values(state.claimDraft), reviewed=decisions.filter(v=>v!=="PENDING").length;
  const conflicts=state.data.conflicts.filter(item=>(state.conflictDraft[item.conflict_id]||item.resolution)).length;
  const profile=document.querySelector("#profileReview").checked || state.data.profile_review==="CONFIRMED";
  const cards=[[t("readyAnswers"),`${state.data.completion.resolved}/${state.data.completion.total}`],[t("readyClaims"),`${reviewed}/${decisions.length}`],[t("readyConflicts"),`${conflicts}/${state.data.conflicts.length}`],[t("profile"),profile?t("complete"):t("incomplete")]];
  document.querySelector("#readiness").innerHTML=cards.map(([l,v])=>`<div><span>${escapeHtml(l)}</span><strong>${escapeHtml(v)}</strong></div>`).join("");
}

function renderStateMode(){
  const readonly=isReadonly(), banner=document.querySelector("#stateBanner");
  const aiReady=state.data?.ai_engine?.status==="READY";
  banner.classList.toggle("hidden",!readonly);
  document.querySelector("#documentFile").disabled=readonly||!aiReady;
  document.querySelector("#aiFile").disabled=readonly||!aiReady;
  document.querySelector("#documentType").disabled=readonly||!aiReady;
  document.querySelector("#aiType").disabled=readonly||!aiReady;
  document.querySelector("#saveAnswers").disabled=readonly;
  document.querySelector("#saveReview").disabled=readonly;
  document.querySelector("#profileReview").disabled=readonly;
  document.querySelector("#finalConfirm").disabled=readonly;
  document.querySelector("#completeOnboarding").disabled=readonly;
  document.querySelector("#mergeClaims").disabled=readonly;
  document.querySelector("#statusText").textContent=readonly?`${t("readonly")} · v${state.data.revision_number}`:`v${state.data.revision_number} · ${t("draftSaved")}`;
}

async function refresh(cacheBust=false) {
  const next=await api(`bootstrap${cacheBust?`?refresh=${Date.now()}`:""}`);
  if(["zh","en"].includes(next?.locale)){state.locale=next.locale;applyLocale();}
  assertUiCompatibility(next);
  state.data=next;
  state.answerDraft=JSON.parse(JSON.stringify(state.data.answers));
  state.claimDraft={}; state.claimEditDraft={}; state.conflictDraft={}; state.selectedClaims=new Set();
  applyLocale(); renderDashboard(); renderSources(); renderQuestions(); renderClaims(); updateProgress(); renderStateMode();
  document.querySelector("#profileReview").checked=state.data.profile_review==="CONFIRMED";
  clearAttention(); hideBlockingNotice();
}

async function refreshLatest() {
  let lastError;
  for (let attempt=0;attempt<3;attempt+=1) {
    try { await refresh(true); return; }
    catch (error) {
      lastError=error;
      if(attempt<2) await new Promise(resolve=>setTimeout(resolve,250*(attempt+1)));
    }
  }
  throw lastError;
}

async function upload(file, requestedType) {
  if (!file) return;
  const dot=file.name.lastIndexOf("."), extension=dot>=0?file.name.slice(dot).toLowerCase():"";
  let sourceType=requestedType;
  if(sourceType==="chatgpt_export"&&file.size>STANDARD_CHATGPT_EXPORT_BYTES){
    sourceType="chatgpt_export_large";
    document.querySelector("#aiType").value=sourceType;
  }
  const largeMode=sourceType==="chatgpt_export_large";
  if(largeMode&&extension!==".zip"){showToast(t("lightningZipOnly"),true);return;}
  if(largeMode&&file.size>MAX_LIGHTNING_EXPORT_BYTES){showToast(t("lightningTooLarge"),true);return;}
  const estimateRange=largeMode?[3,15]:sourceType==="chatgpt_export"?[2,10]:[1,8];
  const activityId=beginActivity("importing",{
    phase:"uploading",uploadStarted:Date.now(),loadedBytes:0,totalBytes:file.size,largeMode,
    estimateMinMinutes:estimateRange[0],estimateMaxMinutes:estimateRange[1]
  });
  let result;
  try {
    result=await uploadApi(`import?source_type=${encodeURIComponent(sourceType)}&extension=${encodeURIComponent(extension)}`,file,{
      onProgress:(loaded,total)=>updateActivity(activityId,{loadedBytes:loaded,totalBytes:total||file.size}),
      onUploaded:()=>updateActivity(activityId,{phase:"processing",phaseStarted:Date.now(),loadedBytes:file.size,totalBytes:file.size})
    });
    await refresh();
    showToast(t(result?.extraction_summary?.ai_repair_attempted?"aiRepairApplied":"previewReady"));
  } catch(e) { showToast(`${t("uploadFailed")}: ${sourceAnalysisErrorMessage(e,sourceType)}`,true); }
  finally{endActivity(activityId);}
}

async function analyzeAllSources() {
  const pendingIds=new Set((state.data.pending_sources||[]).map(item=>item.source_id));
  const retained=(state.data.sources||[]).filter(item=>item.raw_retained);
  const eligible=retained.filter(item=>!pendingIds.has(item.source_id));
  if(!retained.length){showToast(t("noReprocessableSources"),true);return;}
  if(!eligible.length){showToast(t("allSourcesAlreadyPending"));return;}
  const baseline=ACTIVITY_ESTIMATES.reprocessing;
  const activityId=beginActivity("reprocessingAll",{estimatedSeconds:baseline*eligible.length,total:eligible.length,completed:0,currentEstimate:baseline});
  const durations=[];
  let succeeded=0,failed=0,processed=0;
  try {
    for(const source of eligible){
      const itemStarted=Date.now();
      try {
        await api("reprocess-source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_id:source.source_id})});
        succeeded+=1;
      }catch(error){
        if(error?.code==="SERVICE_RESTART_REQUIRED")throw error;
        failed+=1;
      }
      processed+=1;
      durations.push(Math.max(3,(Date.now()-itemStarted)/1000));
      const average=durations.reduce((sum,value)=>sum+value,0)/durations.length;
      const elapsed=Math.max(1,(Date.now()-(state.activities.find(item=>item.id===activityId)?.started||Date.now()))/1000);
      updateActivity(activityId,{completed:processed,currentEstimate:average,estimatedSeconds:elapsed+average*(eligible.length-processed)});
    }
    await refresh();
  }finally{
    endActivity(activityId);
  }
  if(failed){showToast(t("bulkAnalysisPartial").replace("{completed}",succeeded).replace("{failed}",failed),true);}
  else{showToast(t("bulkAnalysisComplete"));}
}

function collectClaimEdits(){
  document.querySelectorAll("[data-claim-row]").forEach(row=>{
    const id=row.dataset.claimRow, item=state.data.claims.find(claim=>claim.claim_id===id);
    if(!item)return;
    const statement=row.querySelector(".claim-statement")?.value??item.statement??"";
    const category=row.querySelector(".claim-category")?.value??item.category;
    if(statement!==String(item.statement||"")||category!==String(item.category||"")||Boolean(item.deleted)!==Boolean(state.claimEditDraft[id]?.deleted)){
      state.claimEditDraft[id]={statement,category,deleted:state.claimEditDraft[id]?.deleted??Boolean(item.deleted)};
    }
    if(state.claimEditDraft[id]){item.statement=state.claimEditDraft[id].statement;item.category=state.claimEditDraft[id].category;item.deleted=state.claimEditDraft[id].deleted;}
  });
  return state.claimEditDraft;
}

function previewSelections(sourceId, forceSelected=null){
  const root=document.querySelector(`[data-preview-source="${CSS.escape(sourceId)}"]`);
  return [...root.querySelectorAll(".preview-candidate")].map(row=>({
    candidate_id:row.querySelector(".preview-select").dataset.candidate,
    selected:forceSelected===null?row.querySelector(".preview-select").checked:Boolean(forceSelected),
    statement:row.querySelector(".preview-statement").value,
    category:row.querySelector(".preview-category").value
  }));
}

document.addEventListener("click", async event => {
  const dashboardRefresh=event.target.closest("#refreshDashboard");
  if(dashboardRefresh){try{await withActivity("refreshingDashboard",()=>refreshLatest());showToast(t("dashboardRefreshed"));}catch(error){handleUiError(error);}return;}
  const saveQueueLimit=event.target.closest("#saveQueueLimit");
  if(saveQueueLimit){
    const input=document.querySelector("#pendingLimitInput"),limit=Number(input.value);
    if(!Number.isInteger(limit)||limit<1||limit>1000){showToast(t("pendingLimitInvalid"),true);input.focus();return;}
    try{
      await withActivity("refreshingDashboard",async()=>{
        await api("queue-limit",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({limit})});
        await refreshLatest();
      });
      showToast(t("limitSaved"));
    }catch(error){handleUiError(error);}
    return;
  }
  const openPacket=event.target.closest(".open-review-packet");
  if(openPacket){
    try{
      let result=null;
      await withActivity("loadingReviewPacket",async()=>{
        result=await api("review-packet",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({application_id:openPacket.dataset.id})});
      });
      state.reviewPacket=result;
      state.reviewDecision="";state.reviewDecisionConfirmed=false;
      renderReviewPacket();
      document.querySelector("#reviewPacketPanel").scrollIntoView({behavior:"smooth",block:"start"});
    }catch(error){state.reviewPacket=null;document.querySelector("#reviewPacketPanel").classList.add("hidden");handleUiError(error);}
    return;
  }
  const closePacket=event.target.closest("#closeReviewPacket");
  if(closePacket){state.reviewPacket=null;state.reviewDecision="";state.reviewDecisionConfirmed=false;document.querySelector("#reviewPacketBody").replaceChildren();document.querySelector("#reviewPacketPanel").classList.add("hidden");return;}
  const confirmPacketDecision=event.target.closest("#confirmPacketDecision");
  if(confirmPacketDecision){
    if(!state.reviewDecision){showToast(t("chooseDecision"),true);return;}
    if(!state.reviewDecisionConfirmed){showToast(t("confirmDecisionFirst"),true);return;}
    const decision=state.reviewDecision,packet=state.reviewPacket?.packet;
    if(!packet){showToast(t("reviewPacketUnavailable"),true);return;}
    try{
      await withActivity("savingQueueDecision",async()=>{
        await api("queue-decision",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
          application_id:state.reviewPacket.application_id,decision,expected_packet_hash:packet.content_hash,user_confirmed:true
        })});
        state.reviewPacket=null;state.reviewDecision="";state.reviewDecisionConfirmed=false;
        document.querySelector("#reviewPacketBody").replaceChildren();document.querySelector("#reviewPacketPanel").classList.add("hidden");
        await refreshLatest();
      });
      showToast(t(decision==="APPROVE"?"decisionApproved":decision==="REVISE"?"decisionRevised":"decisionRejected"));
    }catch(error){handleUiError(error);}
    return;
  }
  const aiToggle=event.target.closest("#aiConnectButton");
  if(aiToggle){
    const panel=document.querySelector("#aiConnectionPanel"), opening=panel.classList.contains("hidden");
    panel.classList.toggle("hidden",!opening);aiToggle.setAttribute("aria-expanded",String(opening));
    if(opening)panel.scrollIntoView({behavior:"smooth",block:"start"});
    return;
  }
  const aiClose=event.target.closest("#closeAiConnection");
  if(aiClose){document.querySelector("#aiConnectionPanel").classList.add("hidden");document.querySelector("#aiConnectButton").setAttribute("aria-expanded","false");return;}
  const aiChoice=event.target.closest("[data-ai-mode]");
  if(aiChoice){
    const mode=aiChoice.dataset.aiMode, activity=mode==="agent"?"detectingAgent":"detectingLocalModel";
    try{
      await withActivity(activity,async()=>{
        await api("connect-ai",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode})});
        await refresh(true);
      });
      showToast(t("aiConnectionSucceeded"));
    }catch(error){showToast(aiConnectionErrorMessage(error),true);}
    return;
  }
  const locale=event.target.closest("[data-locale]");
  if (locale) {
    const draft=collectAnswerDraft();
    collectClaimEdits();
    Object.assign(state.data.answers,draft);
    state.locale=locale.dataset.locale; applyLocale(); renderSources(); renderQuestions(); renderClaims(); renderReadiness(); renderStateMode();
    if(!isReadonly()){try { await withActivity("savingLanguage",()=>api("save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({locale:state.locale,answers:{}})})); } catch(e) { handleUiError(e); }}
    return;
  }
  const revision=event.target.closest("#startRevision");
  if(revision){
    let revisionResult=null, revisionError=null;
    try{
      await withActivity("startingRevision",async()=>{
        try{revisionResult=await api("start-revision",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});}
        catch(error){revisionError=error;}
        await refreshLatest();
      });
      if(!isReadonly()){
        showToast(t(revisionResult?.changed===false?"revisionReady":"revisionStarted"));
        navigate("sources");
      }else if(revisionError){throw revisionError;}
      else{throw new Error(t("revisionSyncFailed"));}
    }catch(error){
      if(error?.code==="ONBOARDING_REVISION_NOT_REQUIRED")showToast(t("revisionReady"));
      else handleUiError(error);
    }
    return;
  }
  const analyzeAll=event.target.closest("#analyzeAllSources");
  if(analyzeAll){try{await analyzeAllSources();}catch(e){showToast(sourceAnalysisErrorMessage(e),true);}return;}
  const commitPreview=event.target.closest(".commit-preview");
  if(commitPreview){try{await withActivity("committingSource",async()=>{await api("commit-source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_id:commitPreview.dataset.id,selections:previewSelections(commitPreview.dataset.id)})});await refresh();});showToast(t("sourceImported"));}catch(e){handleUiError(e);}return;}
  const includeAll=event.target.closest(".include-all-preview");
  if(includeAll){try{await withActivity("includingAll",async()=>{await api("commit-source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_id:includeAll.dataset.id,selections:previewSelections(includeAll.dataset.id,true)})});await refresh();});showToast(t("sourceImported"));}catch(e){handleUiError(e);}return;}
  const selectAll=event.target.closest(".select-all-preview");
  if(selectAll){document.querySelector(`[data-preview-source="${CSS.escape(selectAll.dataset.id)}"]`)?.querySelectorAll(".preview-select").forEach(item=>{item.checked=true;});return;}
  const clearAll=event.target.closest(".clear-all-preview");
  if(clearAll){document.querySelector(`[data-preview-source="${CSS.escape(clearAll.dataset.id)}"]`)?.querySelectorAll(".preview-select").forEach(item=>{item.checked=false;});return;}
  const discardPreview=event.target.closest(".discard-preview");
  if(discardPreview){try{await withActivity("discardingSource",async()=>{await api("discard-source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_id:discardPreview.dataset.id})});await refresh();});}catch(e){handleUiError(e);}return;}
  const reprocess=event.target.closest(".reprocess-source");
  if(reprocess){let result;try{await withActivity("reprocessing",async()=>{result=await api("reprocess-source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_id:reprocess.dataset.id})});await refresh();});showToast(t(result?.ai_repair_attempted?"aiRepairApplied":"previewReady"));}catch(e){showToast(sourceAnalysisErrorMessage(e),true);}return;}
  const reupload=event.target.closest(".reupload-source");
  if(reupload){document.querySelector("#aiType").value="chatgpt_export_large";syncAiFileType();document.querySelector("#aiFile").click();return;}
  const removeSource=event.target.closest(".remove-source");
  if(removeSource){if(!window.confirm(t("deleteSourceConfirm")))return;try{await withActivity("deletingSource",async()=>{await api("delete-source",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_id:removeSource.dataset.id,user_confirmed:true})});await refresh();});showToast(t("sourceDeleted"));}catch(e){handleUiError(e);}return;}
  const conflictJump=event.target.closest(".conflict-jump");
  if(conflictJump){navigate("review");setTimeout(()=>document.getElementById(`conflict-${conflictJump.dataset.conflictTarget}`)?.scrollIntoView({behavior:"smooth",block:"center"}),0);return;}
  const fieldJump=event.target.closest(".conflict-field-jump");
  if(fieldJump){navigate("questionnaire");setTimeout(()=>document.querySelector(`[data-question="${CSS.escape(fieldJump.dataset.fieldTarget)}"]`)?.scrollIntoView({behavior:"smooth",block:"center"}),0);return;}
  const jump=event.target.closest("[data-target]"); if (jump) { navigate(jump.dataset.target); return; }
  const accept=event.target.closest(".accept-suggestion");
  if (accept) { try{await withActivity("savingSuggestion",async()=>{await api("accept-suggestion",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({suggestion_id:accept.dataset.id})});await refresh();});showToast(t("saved"));}catch(e){handleUiError(e);} return; }
  const batch=event.target.closest("[data-batch]");
  if (batch) { batch.closest(".claim-group").querySelectorAll(".claim-decision:not(:disabled)").forEach(el=>{el.value=batch.dataset.batch;state.claimDraft[el.dataset.claim]=el.value;}); updateReviewProgress(); return; }
  const toggleDelete=event.target.closest(".toggle-delete");
  if(toggleDelete){collectClaimEdits();const item=state.data.claims.find(claim=>claim.claim_id===toggleDelete.dataset.id);item.deleted=!item.deleted;state.claimEditDraft[item.claim_id]={statement:item.statement||"",category:item.category,deleted:item.deleted};state.claimDraft[item.claim_id]=item.deleted?"REJECTED":"PENDING";item.decision=state.claimDraft[item.claim_id];renderClaims();return;}
  const toggleSplit=event.target.closest(".toggle-split");
  if(toggleSplit){event.target.closest("[data-claim-row]").querySelector(".claim-split-editor").classList.toggle("hidden");return;}
  const applySplit=event.target.closest(".apply-split");
  if(applySplit){const row=event.target.closest("[data-claim-row]"),statements=row.querySelector(".split-statements").value.split(/\n+/).map(x=>x.trim()).filter(Boolean),category=row.querySelector(".claim-category").value;try{await withActivity("transformingClaims",async()=>{await api("claim-transform",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"SPLIT",claim_ids:[applySplit.dataset.id],statements,category})});await refresh();});showToast(t("claimChanged"));}catch(e){handleUiError(e);}return;}
  const merge=event.target.closest("#mergeClaims");
  if(merge){collectClaimEdits();const ids=[...document.querySelectorAll(".claim-merge-select:checked")].map(el=>el.dataset.claim);if(ids.length<2){showToast(t("chooseTwoClaims"),true);return;}const joined=ids.map(id=>document.querySelector(`[data-claim-row="${CSS.escape(id)}"] .claim-statement`).value.trim()).filter(Boolean).join(" "),statement=window.prompt(t("mergedTextPrompt"),joined);if(!statement)return;const category=document.querySelector(`[data-claim-row="${CSS.escape(ids[0])}"] .claim-category`).value;try{await withActivity("transformingClaims",async()=>{await api("claim-transform",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"MERGE",claim_ids:ids,statement,category})});await refresh();});showToast(t("claimChanged"));}catch(e){handleUiError(e);}return;}
});

document.querySelector("#documentFile").addEventListener("change",e=>{upload(e.target.files[0],document.querySelector("#documentType").value);e.target.value="";});
document.querySelector("#aiFile").addEventListener("change",e=>{upload(e.target.files[0],document.querySelector("#aiType").value);e.target.value="";});
function syncAiFileType(){document.querySelector("#aiFile").accept=document.querySelector("#aiType").value==="ai_summary"?".txt,.md,.json":".zip";}
document.querySelector("#aiType").addEventListener("change",syncAiFileType);
syncAiFileType();
document.addEventListener("change",event=>{
  if(event.target.matches('input[name="packetDecision"]')){state.reviewDecision=event.target.value;document.querySelector("#confirmPacketDecision").disabled=!state.reviewDecisionConfirmed;}
  if(event.target.matches("#packetDecisionConfirm")){state.reviewDecisionConfirmed=event.target.checked;document.querySelector("#confirmPacketDecision").disabled=!state.reviewDecision||!state.reviewDecisionConfirmed;}
  if(event.target.matches(".answer-input")){
    const row=event.target.closest("[data-question]"), status=row?.querySelector(".status-select");
    const hasValue=Array.isArray(event.target.value)?event.target.value.length>0:String(event.target.value||"").trim().length>0;
    if(status && hasValue && status.value==="UNKNOWN") status.value="CONFIRMED";
  }
  if(event.target.matches(".claim-decision")){state.claimDraft[event.target.dataset.claim]=event.target.value;updateReviewProgress();}
  if(event.target.matches(".conflict-resolution")){state.conflictDraft[event.target.dataset.conflict]=event.target.value;updateReviewProgress();}
});
document.addEventListener("input",event=>{
  if(!event.target.matches(".claim-statement,.claim-category"))return;
  const row=event.target.closest("[data-claim-row]"),id=row.dataset.claimRow,item=state.data.claims.find(claim=>claim.claim_id===id);
  state.claimEditDraft[id]={statement:row.querySelector(".claim-statement").value,category:row.querySelector(".claim-category").value,deleted:Boolean(item.deleted)};
  state.claimDraft[id]="PENDING";const decision=row.querySelector(".claim-decision");if(decision&&!decision.disabled)decision.value="PENDING";updateReviewProgress();
});

document.querySelector("#saveAnswers").addEventListener("click",async()=>{
  const answers=collectAnswerDraft();
  try{await withActivity("savingAnswers",async()=>{await api("save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({locale:state.locale,answers})});await refresh();});showToast(t("saved"));if(state.data.completion.remaining)handleUiError(makeUiError("ONBOARDING_ANSWERS_INCOMPLETE",{fields:state.data.completion.remaining_fields||[]}));else navigate("review");}catch(e){handleUiError(e);}
});

document.querySelector("#saveReview").addEventListener("click",async()=>{
  collectClaimEdits();
  const conflict_resolutions={};
  Object.entries(state.conflictDraft).forEach(([id,resolution])=>{if(resolution) conflict_resolutions[id]={resolution,manual_value:null};});
  try{await withActivity("savingReview",async()=>{await api("review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile_review:document.querySelector("#profileReview").checked?"CONFIRMED":"PENDING",claim_decisions:state.claimDraft,claim_edits:state.claimEditDraft,conflict_resolutions})});await refresh();});const blocker=currentReviewBlocker();if(blocker){handleUiError(blocker);}else{showToast(t("reviewSaved"));navigate("finish");}}catch(e){handleUiError(e);}
});

document.querySelector("#completeOnboarding").addEventListener("click",async()=>{
  if(!document.querySelector("#finalConfirm").checked){showToast(t("answerFirst"),true);return;}
  try{await withActivity("completingOnboarding",async()=>{await api("complete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user_confirmed:true})});await refresh();});document.querySelector("#completionMessage").textContent=t("completeSuccess");renderReadiness();showToast(t("completeSuccess"));}catch(e){handleUiError(e);}
});

withActivity("loadingInitial",refresh).catch(handleUiError);
