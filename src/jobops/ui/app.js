"use strict";

const STRINGS = {
  zh: {
    aiOperatorCommandLabel: "一句话交给 AI", aiOperatorCommandHelp: "可以直接输入“帮我处理这个岗位”并粘贴公司岗位链接。AI 会连续理解与决策；浏览器、材料和安全门仍由 JobFlow 执行。", aiOperatorCommandDefault: "帮我处理这个岗位", aiOperatorDelegated: "由 JobFlow 接力执行", aiOperatorUserGate: "到这里才需要你",
    guidedIntakeEyebrow: "下一份岗位", guidedIntakeTitle: "粘贴岗位链接即可开始", guidedIntakeBody: "从公司官网岗位链接开始。浏览器伴侣只读取你主动选择的岗位页和申请表结构，随后自动准备岗位简历、按需求职信、作品集与审阅包。", guidedIntakeIdle: "尚未开始", guidedOfficialUrl: "公司官网岗位链接", guidedIntakeConsent: "我允许 JobFlow 在接下来的 30 分钟内，只读取我在浏览器中主动选择的公司岗位页和申请表结构；此阶段不填写、不上传、不点击网页按钮。", startGuidedIntake: "连接浏览器并开始", guidedOpenJob: "打开公司岗位页", cancelGuidedIntake: "取消本次读取并更换网址", cancelGuidedIntakeConfirm: "确认取消本次岗位读取并更换网址？这不会删除你的简历、Profile 或其他资料，也不会修改招聘网站。", cancellingGuidedIntake: "正在取消本次岗位读取…", guidedCancelled: "本次岗位读取已取消，现在可以填写其他网址。", guidedCancelledCompanionReload: "本次岗位读取已取消，但浏览器伴侣没有确认释放旧连接。请在扩展管理页重新加载 JobFlow Browser Companion 后再开始。", guidedCancelUnavailable: "这次岗位已经生成或排队。请在待审批列表处理它，不能静默删除。", guidedStepOneTitle: "打开公司岗位页", guidedStepOneBody: "粘贴一次链接并建立浏览器连接。", guidedStepTwoTitle: "读取岗位", guidedStepTwoBody: "在岗位页打开浏览器右上角的 JobFlow J，再点“读取当前公司岗位页”。", guidedStepThreeTitle: "读取申请表", guidedStepThreeBody: "亲自点击公司的 Apply；到申请表后再次打开 J。JobFlow 会自动准备材料。", guidedStepFourTitle: "一次审阅", guidedStepFourBody: "确认材料与岗位问题后，才会出现辅助填写入口。", guidedPairing: "正在连接浏览器伴侣…", guidedPaired: "已连接。请打开公司岗位页，再使用浏览器右上角的 JobFlow J。", guidedAwaitingJob: "等待你读取公司岗位页。", guidedAwaitingForm: "岗位已读取。请亲自点击公司页面的 Apply；进入申请表后再次使用 JobFlow J。", guidedPreparing: "正在根据岗位和表单准备岗位简历、按需材料与审阅包…", guidedReady: "材料与审阅包已准备好，请在下方一次审阅。", guidedDeferred: "待审批队列已满，这个岗位已安全排队；你处理一项后会继续。", guidedFailed: "本次读取没有完成，网页没有被填写或修改。请检查当前页面后重试。", guidedExtensionMissing: "没有收到浏览器伴侣响应。请确认 JobFlow Browser Companion 已启用，然后重试。", guidedUrlRequired: "请粘贴公司官网上的 HTTPS 岗位链接。", guidedConsentRequired: "请先勾选这次只读岗位导入授权。", guidedReadinessRequired: "请先完成上方列出的资料准备项。", guidedWrongJobPage: "请先在公司自己的官网岗位页读取岗位，再亲自进入它链接的申请表。", guidedFormMissing: "当前页面没有找到申请字段。请先进入真正的申请表，再打开 JobFlow J。", guidedJobTitleMissing: "当前页面无法可靠识别岗位名称，请确认打开的是具体岗位页。", guidedLeaseInvalid: "这次岗位读取连接已过期，请重新开始。", advancedToolsTitle: "高级诊断与离线 QA", advancedToolsBody: "普通使用不需要这里。仅在开发测试或浏览器导入不可用时，才手动提供本地快照。", advancedToolsOpen: "展开", browserAssistEyebrow: "已批准的申请",
    retryCompanionPairing: "再次显示连接步骤",
    companionClickToPair: "JobFlow 已尝试自动连接，但浏览器没有确认。只需在这个 JobFlow 页面点击一次右上角的 J 完成恢复。",
    companionClickToReconnect: "浏览器伴侣已明确丢失本次绑定，但本次授权仍保留。请在这个 JobFlow 页面点击 J 重新连接；JobFlow 不会静默重试。",
    companionStatusTemporary: "暂时没有收到浏览器伴侣状态；本次授权仍保留，JobFlow 会稍后自动再检查，无需重新开始。",
    companionSessionActive: "另一种浏览器任务仍在进行中。请先完成或明确停止它，再开始这个任务。",
    guidedExtensionMissing: "没有收到浏览器伴侣响应。请确认扩展已启用并重新加载当前版本；无需把网站权限改成“在所有网站上”。",
    guidedExtensionOutdated: "浏览器伴侣版本不匹配。请从浏览器扩展商店更新后刷新本页面。",
    guidedBindingMissing: "浏览器伴侣没有通过本机安装验证。请重新运行 JobFlow 安装程序以修复本机安全通道，然后刷新页面。",
    browserCompanionChecking: "正在自动检测浏览器伴侣", browserCompanionReady: "浏览器伴侣已就绪，任务会自动连接", browserCompanionUnavailable: "未检测到浏览器伴侣；请从扩展商店安装并运行一次 JobFlow 安装器", browserCompanionUpdateRequired: "浏览器伴侣需要更新到当前版本",
    browserAssistRestartRequired: "扩展已重载，本次辅助已安全停止。请重新打开这份申请的起始页，再建立一次连接；JobFlow 没有自动重试 Next/Continue。",
    browserAssistApplyRestart: "页面可能已经填写或上传了一部分，但整页验证没有完成。本轮已停止并记入审计，绝不会自动重复填写或上传；请重新打开申请起始页再开始。",
    browserAssistManualRestart: "这次一次性下一步证明没有安全建立。请结束并重新启动这项申请辅助；JobFlow 不会自动重试。",
    browserAssistReloadUnknown: "扩展在最终提交等待阶段被重载，结果已安全标记为未知。请回答“是否提交成功”；JobFlow 不会自动重试。",
    brandSubtitle: "找工流水线", localOnly: "仅限本机 · DPAPI 加密", eyebrow: "JOBFLOW SETUP", pageTitle: "JobFlow · 找工流水线",
    browserAssistNavigationStalled: "页面在 20 秒内没有可靠前进；JobFlow 已停止且不会重试。请结束本次辅助后重新开始。",
    heroTitle: "一次填写，连续投递", heroBody: "从简历与项目材料、AI 资料和你的直接回答建立完整资料。所有私人内容只在本机解密，不写入普通项目文件。",
    demoTitle: "合成演示 · 不使用真实资料", demoBody: "这是自动清理的临时体验环境。所有示例均为虚构内容；文件上传和真实 AI 连接已关闭，请勿在这里输入个人信息。", demoReview: "查看 AI 与冲突审阅", demoQueue: "查看待审批申请",
    atsCapabilityTitle: "官网与 ATS 能力边界", atsCapabilityBody: "本地证据证明结构分析能力；实时页面会逐页重新验证，不能据此承诺兼容任意网站。", atsLiveUnverified: "实时兼容：逐页验证", atsUserPresentAssist: "在场预填与材料上传：支持", atsNavigationScoped: "非最终前进：仅明确控件", atsActionsBlocked: "最终 Submit：仅由你点击", atsEvidenceDirect: "公司官网单页快照", atsEvidenceVertical: "完整合成纵向链", atsEvidenceSingle: "保存的单页表单", atsEvidenceSequence: "保存的多步骤序列",
    offlineDiscoveryTitle: "解析已保存的公司招聘页", offlineDiscoveryBody: "只读取你选择的本地 HTML、保存页面 JSON，或 Greenhouse / Lever 岗位 JSON；不执行页面代码、不保存快照、不联网。", companyDomainLabel: "公司官网域名", careersUrlLabel: "保存页面原始 URL", officialSnapshotLabel: "招聘页快照", analyzeOfficialSnapshot: "只读解析岗位", officialInputsRequired: "请填写官网域名与招聘页 URL，并选择本地快照。", officialSnapshotInvalid: "快照、官网域名或招聘页 URL 无法安全对应，请检查后重试。", officialDiscoveryComplete: "本地快照解析完成；候选仍需实时复验。", officialCandidatesTitle: "离线岗位候选", officialCandidateCount: "找到 {count} 个", officialNoCandidates: "没有找到符合官网/允许 ATS 边界的岗位链接。", officialLiveCheckRequired: "仍需另行授权后实时复验", officialNotQueued: "未加入申请队列",
    offlineApplicationTitle: "准备一个离线申请", offlineApplicationBody: "选择已保存的岗位说明、公司官网岗位页和申请表；JobFlow 只在本机生成岗位材料与审阅包。", offlineApplicationGuard: "只到待审批", applicationOfficialUrl: "公司官网岗位 URL", applicationFormUrl: "申请表原始 URL", applicationGuestMode: "是否可访客申请", guestUnknown: "不确定", guestYes: "可以", guestNo: "不可以", applicationJdFile: "岗位说明（JD）", applicationOfficialFile: "已保存的官网岗位页", applicationFormFile: "已保存的申请表", applicationEvidenceExcerpt: "官网页中的一段公司原文", applicationEvidencePlaceholder: "粘贴官网岗位页中一段至少 12 个字符的原文，用于有依据地生成求职信。", applicationEvidenceHelp: "必须能在所选官网页中逐字找到；不会被当作你的个人经历。", offlineApplicationReadyHint: "资料准备度全部通过后即可生成。", offlineApplicationReady: "准备度已通过，可以选择本地岗位资料。", offlineApplicationNeedsReadiness: "先完成上方自动投递准备度中的所有项目。", offlineApplicationInputsRequired: "请填写两个 HTTPS URL、选择三个本地文件，并粘贴一段官网原文。", prepareOfflineApplication: "生成材料并加入待审批", preparingOfflineApplication: "正在分析岗位并生成本机材料…", offlineApplicationPrepared: "岗位材料已生成并加入待审批。", offlineApplicationDeferred: "待审批队列已满；该岗位的三份本地证据已加密排队，释放名额后会自动继续。", applicationBundleInvalid: "所选岗位文件或页面信息无法安全对应，请检查后重试。", deferredBundleTooLarge: "队列已满，且这组本地证据过大，无法安全暂存。请先处理一项待审批申请，再重新选择这组文件。",
    progressLabel: "基础资料完成度", draftSaved: "草稿保存时直接加密", stepSources: "资料来源", stepQuestions: "补充资料", stepReview: "资料与 Claim 审阅", stepFinish: "确认完成",
    pipelineEyebrow: "JOBFLOW CONTROL", pipelineTitle: "本地投递控制台", pipelineBody: "统一查看资料准备度、待审批容量和安全边界。这里不会打开招聘网站或执行外部动作。", refreshDashboard: "刷新状态", dashboardRefreshed: "本地控制台已刷新", profileReadiness: "资料准备", awaitingApproval: "待你审批", approvalQueueOnly: "只生成本地审阅包", availableSlots: "剩余容量", deferredJobs: "排队等待", continuesUntilLimit: "达到上限前继续处理其他岗位", pendingReviewTitle: "待审批申请", pendingReviewBody: "只显示安全岗位摘要；私人答案和材料正文不会出现在此处。", safetyBoardTitle: "当前安全边界", realSites: "真实网站访问", externalActions: "真实外部动作", knowledgeWrites: "知识库写入", networkMode: "运行模式", externalControl: "外部动作总开关", externalControlLocked: "已关闭", externalControlEnabled: "已启用", emergencyStop: "立即停止全部外部动作", emergencyStopConfirm: "确认立即关闭全部外部动作并使现有动作授权失效？", emergencyStopped: "全部外部动作已关闭，现有动作授权已失效。", stoppingExternalActions: "正在关闭全部外部动作…", pipelineReady: "已完成", pipelineNeedsSetup: "待完成", aiReadyShort: "AI 已连接", aiMissingShort: "AI 未连接", queueLimit: "上限 {limit}", pendingEmpty: "目前没有等待你审批的申请。离线处理完成的岗位会出现在这里。", packetHash: "审阅包 {hash}", packetVersion: "第 {version} 版", awaitingApprovalStatus: "等待你的决定", safetyGuardOn: "外部动作锁定", offlineMode: "仅限本地离线", refreshingDashboard: "刷新本地控制台…", deferredListTitle: "等待处理的岗位", deferredListBody: "达到上限后进入这里；释放位置时按顺序继续。", deferredEmpty: "目前没有因容量而等待的岗位。", recentDecisionsTitle: "最近的队列决定", recentDecisionsBody: "显示本地状态变化；不会把批准当成已提交。", recentEmpty: "还没有已处理的队列决定。", safeQueueId: "安全队列编号", queuedAt: "进入时间", viewRecord: "查看记录", approvalExpiry: "本地批准有效至 {time}", statusApproved: "本地已批准", statusClosed: "已关闭", statusRevision: "等待修改", statusDeferred: "等待容量", statusOther: "本地状态：{status}", executionRunsTitle: "自动投递执行状态", executionRunsBody: "只显示安全状态、最近检查点和下一步；不显示私人答案、文件内容或网站会话。", executionRunsEmpty: "目前没有自动投递执行记录。审阅批准不会被显示为已提交。", executionStatusAwaiting: "等待一次性最终确认", executionStatusConfirmed: "已由可靠回执确认", executionStatusUnknown: "提交结果未知，必须人工核验", executionStatusInvalidated: "已失效", executionStatusInterrupted: "发现中断，必须恢复核验", executionStatusOther: "执行状态：{status}", executionCheckpoint: "检查点 {sequence}", executionPhaseNow: "最近阶段：{phase}", executionNoRetry: "禁止自动重试", executionNextFinal: "下一步：取得一次性最终确认；当前实时动作仍关闭", executionNextNone: "下一步：无，流程已确认完成", executionNextManual: "下一步：人工核验外部证据，绝不自动重投", executionNextRebuild: "下一步：重新生成并审阅申请包", executionNextRestart: "下一步：本机恢复器核验持久化状态，不重新发送", executionNextOther: "下一步：人工检查本机状态",
    pipelineBody: "统一查看资料准备、审批队列和用户在场的公司官网与 ATS 辅助投递。每个申请都必须单独授权。", safetyGuardOn: "最终提交与自动重试始终锁定", offlineMode: "本地准备 + 用户在场辅助", assistedMode: "本地准备 + 用户在场辅助", authorizedActionsAudited: "已授权动作均已审计", browserAssistTitle: "审阅后辅助填写", browserAssistBody: "只有当前审阅包批准后，这里才会逐页预填获批字段、附加材料并安全通过明确的 Next/Continue。最终 Submit 永远留给你。", browserAssistIdle: "等待已批准申请", browserCompanionStep: "同一个浏览器伴侣", browserCompanionHelp: "岗位读取与审阅后的辅助填写都使用同一个 JobFlow J 扩展；首次加载后由 JobFlow 自动检测并连接。", browserCompanionNotPaired: "尚未与本次申请配对", browserCompanionPaired: "浏览器伴侣已配对", browserAssistBoundaryTitle: "不可越过的边界", browserAssistBoundary: "JobFlow 只会在当前页校验通过后，使用一次性授权点击一个明确的 Next/Continue；不会登录、注册、绕过验证码或点击最终 Submit。结果不明确时只会询问你，绝不自动重试。", browserAssistCompanyOnly: "支持已审批的公司官网、Greenhouse、Lever 与 Workday 路线；每一页仍会按当前结构重新校验，不会把离线测试当成实时兼容证明。", browserAssistConsent: "我授权这一个申请在 30 分钟内逐页读取表单结构、预填获批字段、附加获批材料并通过明确的非最终 Next/Continue；登录、验证、未知问题和最终 Submit 由我处理。", startBrowserAssist: "开始辅助投递", startBrowserAssistNow: "让 AI 规划并开始", startingBrowserAssist: "AI 正在规划并建立一次性浏览器连接…", aiOperatorTitle: "AI 任务规划", aiOperatorBoundary: "AI 只选择 JobFlow 的受限工具；浏览器、文件和最终提交权限仍由 JobFlow 控制。", aiOperatorRequired: "请先连接并验证 AI，再让它处理这份申请。", aiOperatorReady: "AI 已生成受限任务计划，JobFlow 正按批准边界执行。", aiOperatorExecuted: "JobFlow 已执行", aiOperatorPending: "等待页面状态后执行", browserAssistConfirmFirst: "请先勾选本次真实辅助授权。", browserAssistPairing: "正在自动连接浏览器伴侣…", browserAssistPaired: "浏览器伴侣已自动连接；JobFlow 会在已批准申请页继续，只有浏览器阻止自动连接时才需要点一次 J。", browserAssistExtensionMissing: "没有收到浏览器伴侣响应。请先运行安装入口并在浏览器中加载扩展。", openApprovedApplication: "打开已批准的申请页", browserAssistAwaitingSubmit: "所有已支持页面已处理；请检查后亲自点击最终 Submit。", browserAssistPageReview: "当前页仍有需要你填写或确认的项目；完成后在浏览器伴侣中继续。", browserAssistSupplementalReview: "页面动态显示了新的问题。JobFlow 已保留此前确认内容，只需审阅新增项；没有触碰最终 Submit。", browserAssistHandoff: "当前页需要你亲自完成登录、账号、验证码或 MFA；完成后在浏览器伴侣中恢复。", browserAssistNavigating: "当前页已通过校验，正在进入下一页…", browserAssistStepMeta: "{provider} · 第 {step}/{max} 页", browserAssistObserving: "已检测到你点击最终 Submit，正在读取结果页。", browserAssistConfirmed: "提交成功，结果页回执已记录。", browserAssistUnknown: "结果无法可靠判断：是否提交成功？", browserAssistFailed: "页面明确显示提交未成功；未自动重试，需要重新审阅后再操作。", submittedYes: "是，已提交成功", submittedNo: "否，没有提交成功", resolveUnknownConfirm: "确认你的判断？JobFlow 不会自动重试。", resolvingSubmission: "正在保存你的提交结果判断…", browserAssistResolved: "提交结果已保存。", statusSubmitted: "等待结果", statusSubmissionUnknown: "结果待确认", statusConfirmed: "已确认提交", installCompanion: "安装浏览器伴侣",
    browserAssistNotApproved: "这份申请尚未完成审阅批准，请先批准当前审阅包。", browserAssistRouteUnsupported: "该页面不属于已批准的公司官网、Greenhouse、Lever 或 Workday 路线，JobFlow 已安全停止。", browserAssistActive: "已有一份申请正在辅助处理中，请先完成或点击紧急停止。", browserAssistWrongPage: "当前页面不在已批准路线内，或表单在审批后发生了不安全变化。JobFlow 已停止。", browserAssistSafetyStop: "页面需要登录、注册、验证码、MFA 或其他人工步骤。请亲自完成后再从浏览器伴侣恢复；JobFlow 不读取凭据。", browserAssistUnsupportedControl: "表单包含当前无法安全自动填写的控件；JobFlow 没有修改页面。", browserAssistUploadMismatch: "已批准材料与当前上传控件无法一一对应；JobFlow 没有上传。", browserAssistFinalLocked: "最终 Submit 在代码层锁定，只能由你亲自点击。", browserAssistLeaseInvalid: "本次 30 分钟授权已失效，请从该申请重新开始。",
    queuePreferences: "待审批数量上限", queuePreferencesBody: "JobFlow 达到这个数量后暂停接收新岗位；你处理一项后会继续。", pendingLimitLabel: "最多等待", saveLimit: "保存上限", limitSaved: "待审批上限已保存", viewPacket: "查看审阅包", closePacket: "关闭审阅包", reviewPacketEyebrow: "LOCAL REVIEW PACKET", reviewPacketTitlePlaceholder: "本地审阅包", loadingReviewPacket: "解密并核验本地审阅包…", packetJob: "岗位", packetFit: "匹配分析", packetGaps: "硬性缺口", packetBullets: "简历表述与证据", packetPrefillProposal: "准备预填的字段", packetQuestions: "完整表单字段", packetSensitive: "必须停下确认的敏感字段", packetUploads: "待上传材料", packetActions: "审批将绑定的动作", packetRoute: "官网与 ATS 路径", packetNone: "无", packetOverall: "总体匹配", packetStatus: "状态", packetCreated: "生成时间", packetClaims: "Claim", packetEvidence: "证据", prefillPrivateSource: "来自本机加密 Profile / Answer Bank；不显示明文", prefillPublicSource: "来自你确认的公开链接；只显示内容哈希", prefillReady: "已准备，等待本次一次批准", prefillMissing: "没有可用答案，禁止预填", pendingLimitInvalid: "请输入 1 到 1000 之间的整数。", pendingLimitBelowActive: "上限不能低于当前已占用的审批位置。请先处理一项申请。", reviewPacketUnavailable: "无法安全打开这个审阅包；内容未被显示，请刷新后重试。", packetDecisionTitle: "一次决定，然后连续执行", packetDecisionBody: "普通流程默认批准；如需退回或放弃可切换选项。批准按钮同时确认当前哈希、岗位专属答案和在场预填授权，但不包含最终 Submit。", decisionApprove: "批准并开始填写", decisionApproveHelp: "一次绑定当前哈希、岗位答案、材料上传和在场预填；最终 Submit 仍锁定。", decisionRevise: "退回修改", decisionReviseHelp: "标记材料需要修订；任何旧批准都会失效。", decisionReject: "不申请这个岗位", decisionRejectHelp: "关闭这项申请并释放一个待审批位置。", decisionConfirm: "点击下方按钮即确认当前哈希版本、上方岗位专属答案和所选决定。批准会立即开始在场预填与材料上传；JobFlow 永远不会点击最终 Submit。", confirmDecision: "确认这个决定", confirmApproveAndStart: "批准一次并开始填写", confirmReviseDecision: "确认退回修改", confirmRejectDecision: "确认不申请", chooseDecision: "请先选择批准、退回修改或不申请。", confirmDecisionFirst: "请确认当前决定。", decisionApproved: "已批准并启动在场填写；最终 Submit 仍由你点击。", decisionRevised: "已退回修改；旧批准已失效。", decisionRejected: "已关闭这项申请并释放队列位置。", savingQueueDecision: "正在保存决定；如有等待岗位，将继续准备下一项…", nextApplicationPrepared: "下一份岗位材料已自动生成并进入待审批。", nextApplicationNeedsRepair: "下一项本地资料需要修正；其余安全队列未丢失。", reviewPacketStale: "这份审阅包已经变化。为防止误批，请重新打开并审阅最新版本。", reviewDecisionUnavailable: "当前申请已不在待审批状态，请刷新队列。",
    demoExecutionTitle: "合成自动投递演练", demoExecutionBody: "用虚构资料验证审阅批准、隔离预填、材料暂存、一次性最终确认和可靠假回执。真实浏览器、网络和外部动作始终为 0。", demoExecutionApproveFirst: "先打开上方虚构申请的审阅包并批准，演练按钮才会出现。", demoExecutionReady: "审阅包已批准。可以运行自动步骤，但会严格停在假的最终提交之前。", demoRehearsalConsent: "我确认只运行本机合成演练，不访问或修改任何真实网站。", demoRunRehearsal: "运行到最终确认前", demoRehearsalConfirmFirst: "请先确认这只是本机合成演练。", demoPreparingRehearsal: "正在用零网络假适配器验证预填和材料暂存…", demoRehearsalPrepared: "自动步骤已完成并停在最终确认前；临时文件已清理。", demoFinalConsent: "我确认执行一次假的最终提交，并只生成本机合成回执。", demoCompleteRehearsal: "确认假的最终提交", demoFinalConfirmFirst: "请先确认这是假的最终提交。", demoCompletingRehearsal: "正在消费一次性假授权并验证合成回执…", demoRehearsalComplete: "完整合成闭环已确认；真实网站访问和真实外部动作均为 0。",
    packetFieldTitle: "先确认这个岗位的专属问题", packetFieldBody: "这些答案只用于当前岗位，并立即保存为本机 DPAPI 加密引用。审阅包、项目文件和普通数据库不会保存明文。", packetFieldSeparateHelp: "上传、页面导航、账号动作和最终提交由各自的授权门控制，不会被误算成尚未回答。", packetFieldRequired: "必答", packetFieldOptional: "选答", packetFieldDecision: "处理方式", packetFieldValue: "本岗位答案", packetFieldConfirmValue: "填写并确认", packetFieldPreferNot: "不愿回答", packetFieldNotApplicable: "不适用", packetFieldValuePlaceholder: "输入这个岗位要求的准确答案", packetFieldConsent: "我确认这些是当前岗位的准确答案，并同意仅在本机加密保存。", savePacketFields: "加密保存并生成新版审阅包", savingApplicationFields: "正在加密岗位专属答案并重新绑定审阅包…", packetFieldsSaved: "岗位专属答案已加密；请审阅新版审阅包。", packetFieldsRequired: "先完成所有标出的岗位专属问题，才能批准审阅包。", packetFieldInvalid: "有一个岗位专属答案不完整或不属于当前表单，请检查后重试。", packetFieldUnknowns: "这个条件在岗位页面中没有明确答案。你可以确认已知晓该不确定性；JobFlow 不会把它改写成事实。", packetFieldAcknowledgeUnknown: "知晓并保留为未知",
    sourceTitle: "把已有信息交给 JobFlow", sourceBody: "资料必须先通过 AI 的实体归并、分类和完整性检查，才会进入 Claim；规则拆分结果不再显示。",
    sourceIntakeDemoTitle: "当前是合成演示，不接收真实文件", sourceIntakeDemoBody: "请关闭演示启动窗口，然后双击 Start JobFlow.cmd 进入可上传的真实本机工作区。", sourceIntakeReadonlyTitle: "当前资料版本已完成，因此文件接入已锁定", sourceIntakeReadonlyBody: "历史版本不会被覆盖。建立一个可编辑新版本后，即可继续添加、删除和重新分析资料。", sourceIntakeAiTitle: "连接 AI 后才能分析文件", sourceIntakeAiBody: "点击右上角“连接 AI”，选择已准备的 Agent 或本地模型。连接成功后文件选择会自动开放。",
    docsTitle: "简历与项目材料", docsBody: "DOCX、PDF、TXT、MD 或 JSON。适用于简历、案例、证书、作品集与补充材料。", materialType: "材料类型", resume: "简历", projectCase: "项目案例", supporting: "补充材料", portfolioFile: "作品集文件",
    aiTitle: "AI 资料", aiBody: "支持 ChatGPT 官方导出 ZIP 或你整理的 AI 总结。超过 200 MB 的官方导出请选择“雷霆大文件”；原始 ZIP 不会被保留。", aiType: "AI 资料类型", chatgptExport: "ChatGPT 官方导出（不超过 200 MB）", chatgptExportLarge: "雷霆大文件（超过 200 MB 请选择）", aiSummary: "AI 总结", chooseFile: "选择本地文件", uploadAndAnalyze: "选择并由 AI 分析",
    directTitle: "只补充缺失资料", directBody: "简历中已有的姓名、联系方式和公开链接会自动带入；这里只突出仍缺失的资格、偏好与政策答案。", startQuestions: "检查缺失项", importedSources: "已安全接入", noSources: "尚未通过此页面添加资料；已有安全简历仍然保留。",
    suggestions: "待确认的自动预填建议", continueQuestions: "检查缺失资料", questionsTitle: "只确认缺失或需要修改的信息", questionsBody: "已从简历识别的资料会折叠保留，不要求重复输入。资格硬条件必须明确；法律与签名仍然逐次确认。", resolvedProfileFacts: "已从资料填好 {count} 项（展开可检查或修改）", optionalProfileFacts: "其他可选资料 {count} 项（按需填写）", saveHint: "内容不会进入命令行或明文 JSON", saveEncrypted: "加密保存并继续",
    reviewTitle: "集中处理资料与 Claim", reviewBody: "只显示经过 AI 归并和完整性检查的 Claim。工作、实习、教育和项目分别归类；确认不等于自动对外提交。", claimsReviewed: "已审阅 Claim", conflictsResolved: "已解决冲突", conflicts: "需要单独解决的冲突", conflictsHelp: "只有两个相关来源对同一事实给出不同答案时，才算冲突；系统会直接说明哪个字段、两边各写了什么。",
    profileConfirmTitle: "我已审阅 Candidate Profile", profileConfirmBody: "确认简历、项目、技能、教育及成果栏目；未解决冲突除外。", saveReview: "保存审阅结果",
    finishTitle: "完成 JobFlow 设置", finishBody: "完成后，JobFlow 可连续准备岗位并生成待审批队列。每个已批准申请仍需你单独授权，才会在你在场时预填并附加材料；最终提交始终由你亲自点击。", finalConsent: "我确认以上答案和审阅决定准确，并同意将其加密保存为当前 Candidate Profile 与 Answer Bank。", completeButton: "完成 JobFlow 设置",
    unknown: "尚未回答", confirmed: "已确认", preferNot: "不愿回答", notApplicable: "不适用", reuse: "后续复用", confirmEach: "每次申请确认", preferPolicy: "始终不愿披露", doNotStore: "不保存具体值", policy: "使用策略", answerValueLabel: "{field}的答案", answerStatusLabel: "{field}的回答状态", answerPolicyLabel: "{field}的使用策略",
    accept: "采用", sourceImported: "资料已安全接入", uploadFailed: "资料接入失败", aiRepairApplied: "AI 已自动纠正首轮不合格输出", aiRepairFailed: "AI 已自动纠正一次，但仍有内容无法由所标注的原文行支持；本次没有导入任何内容。请重试，若持续出现请换用文字更清晰的 DOCX 或 PDF。", aiExportRepairFailed: "ZIP 已完成本机扫描，但 AI 在自动纠正后仍产生了一条无法由原文支持的内容，所以本次按真实性规则没有导入。你可以重试；若仍失败，可上传一份整理后的 AI 总结。", selectLightning: "这个 ZIP 超过 200 MB，请在“AI 资料类型”选择“雷霆大文件”后重新选择。", lightningZipOnly: "雷霆大文件只接受 ChatGPT 官方导出 ZIP。", lightningTooLarge: "此 ZIP 超过 8 GB 的本机安全上限。", uploadInterrupted: "本机文件传送被中断，请确认页面仍打开后重试。", saved: "已加密保存", reviewSaved: "审阅结果已保存", completeSuccess: "JobFlow 设置完成。本次设置没有执行外部动作；以后只有逐申请授权的预填与材料附加会运行，最终提交仍由你亲自完成。", answerFirst: "请先完成所有必需信息和审阅。",
    confirmAll: "整组确认", rejectAll: "整组排除", pending: "待审阅", rejected: "排除", evidence: "证据", noConflicts: "目前没有真正需要处理的冲突", conflictLabel: "冲突", conflictPending: "需要决定", conflictResolved: "已解决", conflictLocation: "具体冲突字段", affectedClaim: "所属 Claim", affectedField: "字段", conflictReason: "直白说明", resumeSide: "简历写的是", evidenceSide: "知识证据写的是", sourceCandidate: "来源", numericMismatch: "同一个事实的数值不同，两边不能同时成立", multipleValues: "同一个问卷字段出现了不同答案，请直接选择正确值", noEvidencePreview: "没有可比较的相关证据", chooseResolution: "哪一边正确？", reviewThisConflict: "查看并处理冲突", answerThisField: "前往问卷直接确认这个字段", useResume: "简历正确", useEvidence: "知识证据正确", useDirect: "我来直接回答", exclude: "这不是有效 Claim / 冲突",
    readyAnswers: "基础问题", readyClaims: "Claim 审阅", readyConflicts: "冲突处理", profile: "Profile 确认", complete: "完成", incomplete: "未完成", loadingInitial: "正在加载加密资料…", importing: "正在接入并由 AI 分析…", savingAnswers: "正在加密保存问卷…", savingReview: "正在保存审阅决定…", completingOnboarding: "正在生成完成记录…", savingSuggestion: "正在采用建议…", savingLanguage: "正在保存语言设置…", elapsedWithEstimate: "已进行 {elapsed} 秒 · 预计还需约 {remaining} 秒", estimatingTime: "已进行 {elapsed} 秒 · 正在动态估算剩余时间", stillWorking: "仍在处理中，请不要关闭页面", longRunningNoCountdown: "已超过初始估计，任务仍在运行；为避免误导，不会重新开始倒数。", uploadStage: "正在传送到仅限本机的安全处理区 · {percent}%", uploadEta: "已传送 {loaded} / {total} · 按当前速度约还需 {remaining} 秒", uploadMeasuring: "已传送 {loaded} / {total} · 正在测量本机传送速度", aiAnalysisStage: "本机传送完成 · 正在解析并由 AI 进行真实性分析", lightningAnalysisStage: "本机传送完成 · 正在流式扫描大型 ZIP 并由 AI 分析", aiAnalysisRange: "本阶段已进行 {elapsed} 秒 · 通常需要 {min}–{max} 分钟，取决于当前模型", aiAnalysisOverdue: "本阶段已进行 {elapsed} 秒 · 已超过通常用时，但仍在运行且不会重新倒数", close: "关闭", batchProgress: "第 {current}/{total} 份资料 · 已完成 {completed} 份", reprocessingAll: "正在一键分析全部资料…",
    completedSnapshotTitle: "当前是已完成的只读快照", completedSnapshotBody: "历史版本不会被覆盖。若要修改资料，请建立一个新的可编辑版本。", startRevision: "建立可编辑新版本", revisionStarted: "新的可编辑版本已建立", revisionReady: "当前可编辑版本已同步", revisionSyncFailed: "新版本可能已建立，但页面同步失败。请刷新页面后继续。", invalidLocalResponse: "本机服务返回了无法识别的响应，请刷新页面或重启 JobFlow。",
    attentionRequired: "这里还需要处理", serviceRestartRequired: "页面代码已更新，但当前后台还是旧版本。请关闭这个页面，在启动窗口按 Ctrl+C，然后重新启动 JobFlow。", profileReviewRequired: "请先在“资料与 Claim 审阅”底部勾选 Candidate Profile 确认框并保存。", answersIncomplete: "问卷仍有未处理的问题。请逐项回答，或明确选择“不适用 / 不愿回答”。", hardConditionsUnresolved: "求职资格硬条件仍不明确。请回到问卷，把标出的字段改为明确答案。", sourcePreviewPending: "还有 AI 分析结果尚未确认或丢弃。请先处理标出的资料预览。", sourceAiReanalysisRequired: "仍有资料没有通过当前 AI 的重新分析。请重新分析或删除对应资料。", claimReviewIncomplete: "还有 Claim 未选择“确认”或“排除”。请处理标出的第一项。", conflictReviewIncomplete: "还有冲突没有选择处理方式。请处理标出的第一项。", onboardingConfirmationRequired: "请先勾选最后的确认框。", onboardingAlreadyComplete: "这个版本已经完成；如需修改，请建立新的可编辑版本。", onboardingRevisionRequired: "当前版本是只读快照；请先建立新的可编辑版本。", invalidAnswer: "有一项问卷答案格式不完整，请检查标出的字段。", invalidClaim: "有一项 Claim 编辑不完整或分类不适用，请检查标出的内容。", sourceTypeUnsupported: "当前资料类型或文件格式不受支持，请重新选择。", sourceSizeInvalid: "文件为空或超过本地安全上限，请检查文件后重试。", localRequestFailed: "本机操作没有完成。页面没有写入不完整结果；请按标出的位置检查后重试。", privateDeleteRetry: "本机加密副本暂时无法删除，资料与审阅内容已恢复；请稍后重试。", privateDeleteRepair: "本机删除与恢复都未能完整结束。请先运行 Check JobFlow，不要继续编辑这份资料。", privateWriteRetry: "本次加密保存没有完成，原版本已恢复；请稍后重试。", privateWriteRepair: "加密保存与恢复没有全部完成。请先运行 Check JobFlow，不要继续修改资料。", reviewSavedButIncomplete: "审阅草稿已加密保存；完成标出的项目后才能进入最后一步。",
    previewTitle: "审阅 AI 归并结果", previewBody: "每条都是 AI 重建后的完整 Claim，并绑定到唯一工作、实习、教育或项目实体；不会再按页面换行拆分。", confirmSource: "确认资料并安全保留", includeAllClaims: "一键纳入全部 Claim", selectAllClaims: "全选", clearAllClaims: "清空选择", discardPreview: "放弃本次导入", previewEmpty: "AI 没有留下可直接作为 Claim 的完整句子。你仍可确认并安全保留这份资料；DOCX 仍可设为可编辑母版。", filteredCandidateNotice: "AI 已排除 {count} 条标题、残句或无依据候选；文件仍可安全接入，排除项不会成为 Claim。", selectedByDefault: "AI 已过滤（仍需你确认）", needsReview: "未通过严格 AI 门", includeAsClaim: "纳入 Claim", reprocess: "用 AI 重新分析", previewReady: "AI 归并预览已准备，请先审阅", analyzeAllSources: "一键分析全部资料", bulkAnalysisHint: "一键重新分析全部已保留加密原件的资料；ChatGPT 官方导出需要重新上传原 ZIP。", bulkAnalysisComplete: "全部可分析资料已生成新的 AI 审阅预览", bulkAnalysisPartial: "已完成 {completed} 份，{failed} 份未通过 AI 校验", noReprocessableSources: "没有可直接重新分析的资料；ChatGPT 官方导出请重新上传原 ZIP。", allSourcesAlreadyPending: "全部可分析资料都已经有待审阅的 AI 结果", reuploadAndAnalyze: "重新上传原 ZIP 并分析", reuploadRequired: "需重新上传后分析", analysisPassed: "AI 全量分析完成", analysisPassedSelected: "高信号内容分析完成", analysisMissing: "尚未通过全量 AI 分析", analysisCoverageComplete: "{chunks} 个分块 · 所选内容覆盖 100%", analysisCoverageIncomplete: "覆盖证明不完整 · 必须重新分析", archiveSelectionBounded: "完整扫描 ZIP · 安全信息 {safe} 条，AI 分析高信号 {selected} 条，未选 {omitted} 条", archiveSelectionAll: "完整扫描 ZIP · 已分析全部 {selected} 条安全用户信息", claimCandidates: "AI Claim",
    claimEditTitle: "Claim 可编辑审阅", claimEditHelp: "左侧勾选框只用于把同一实体中的多条 Claim 合并，不代表确认或采用；确认状态仍由右侧下拉框决定。", selectForMerge: "选择用于合并", mergeSelected: "合并已勾选项", editText: "可编辑 Claim 表述", category: "经历类型", claimDecisionLabel: "Claim 审阅决定", deleteClaim: "删除", restoreClaim: "恢复", splitClaim: "拆分", applySplit: "应用拆分", splitHelp: "每行填写一条完整 Claim（至少两条）", splitInputLabel: "拆分后的完整 Claim，每行一条", mergedTextPrompt: "请编辑合并后的完整 Claim：", chooseTwoClaims: "请至少勾选两条 Claim", claimChanged: "Claim 已更新", transformingClaims: "正在更新 Claim 结构…", reprocessing: "正在由 AI 重新归并，并在需要时自动纠正…", committingSource: "正在确认 AI 结果…", includingAll: "正在纳入全部 AI Claim…", discardingSource: "正在放弃本次导入…", deletingSource: "正在删除材料及其关联内容…", deleteSource: "删除材料", deleteSourceConfirm: "确定删除这份材料，以及由它生成的所有 Claim 和建议吗？本机加密副本也会删除，此操作不能撤销。", sourceDeleted: "材料及其关联内容已删除", startingRevision: "正在建立新的加密版本…", readonly: "只读", aiEngineReady: "AI 核心已连接", aiEngineReadyBody: "资料会先重建完整句、归并同一经历并区分工作、实习、教育与项目；任何 Claim 仍需你确认。", aiEngineMissing: "必须先连接 AI", aiEngineMissingBody: "当前没有可用 AI，因此上传和重新分析已暂停，也不会显示任何规则拆分候选。连接 AI 后再分析现有材料。", aiMode: "分析模式", legacyQuarantined: "已隔离 {count} 条旧规则结果；它们不会进入 Claim、Profile 或后续申请。", invalidConflictsSuppressed: "已排除 {count} 条无关或不可比较的旧证据映射；它们不算冲突。", work: "正式工作", internship: "实习", education: "教育", project: "项目", skill: "技能", certification: "证书", language: "语言", summary: "职业总结", entityClaims: "条 Claim", entityUnknown: "未命名实体", valueDifference: "同一个{dimension}：简历为 {left}，知识证据为 {right}。", resumeMetrics: "简历数值", evidenceMetrics: "证据数值",
    connectAi: "连接 AI", aiConnectedButton: "AI 已连接", aiConnectionEyebrow: "AI CONNECTION", aiConnectionTitle: "连接已经准备好的 AI", aiConnectionBody: "JobFlow 不要求再次填写模型密钥。它会自动检查 Windows 与 WSL，并通过仅限本机的通道建立连接。", existingAgentTitle: "使用已有 Agent", existingAgentBody: "自动检测 Windows 或 WSL 中的 Hermes / OpenClaw，并复用 Agent 已经配置好的模型。", localModelTitle: "使用本地大模型", localModelBody: "自动检测 Windows 或 WSL 中的 Ollama、LM Studio、LocalAI、llama.cpp 或 vLLM。", customApiTitle: "自定义 API / 适配器", customApiBody: "保留给企业模型、私有网关和其他 Agent。普通用户无需配置。", detectAndConnect: "自动检测并连接", reserved: "接口已预留", aiPrivacyNote: "JobFlow 不读取或保存 Agent 的 API Key、Cookie 或登录令牌。WSL 连接不会暴露局域网端口，私人请求只经 stdin 或回环地址传递。Hermes 与 OpenClaw 都只以零工具分析模式连接：动作工具被禁用，任何工具调用都会使结果作废。使用 Agent 时，资料去向取决于该 Agent 当前选择的本地或云端模型。", aiNotConnectedStatus: "尚未连接 AI。请选择已有 Agent 或本地大模型。", aiConnectedStatus: "已连接并通过结构化测试：{name}", aiConnectedModel: "模型：{model} · 数据路径：{route}", detectingAgent: "正在检查 Windows 与 WSL 中的 Hermes / OpenClaw，并验证结构化证据能力…", detectingLocalModel: "正在检查 Windows 与 WSL 中的本地大模型服务，并验证结构化证据能力…", aiConnectionSucceeded: "AI 已连接，并通过结构化证据测试", aiConnectionFailed: "Windows 与 WSL 中都没有找到已就绪的 AI。请先启动 Agent 或本地模型服务后重试。", aiWslHermesAuthRequired: "已在 WSL 找到 Hermes，但它当前选择的模型或登录状态不可用。请在 Hermes 中确认模型后重试。", aiWslProxyStartFailed: "已在 WSL 找到 Hermes，但本机安全连接没有成功启动。请确认 Hermes 模型可用后重试。", aiWslBridgeMissing: "已检测到 WSL，但其中缺少安全连接所需的 curl。请在该 WSL 环境安装 curl 后重试。", aiAgentSafetyRejected: "Agent 尝试调用工具或未提供可验证的零工具审计，JobFlow 已拒绝该连接。",
    aiWindowsHermesAuthRequired: "已在 Windows 找到 Hermes，但无法安全读取它当前选择的模型或提供商。请先在 Hermes 中重新选择并确认模型，然后重试。",
    aiWindowsHermesConnectionFailed: "已在 Windows 找到 Hermes 和当前模型，但 JobFlow 的隔离零工具连接测试没有通过。Hermes 终端能正常对话不代表这条安全连接已建立；请重启 Hermes 与 JobFlow 后重试。",
    aiWindowsHermesProxyFailed: "已在 Windows 找到 Hermes，但兼容代理没有提供可用模型。请确认 Hermes 当前模型可用并重试；若终端能对话但仍出现此提示，请更新 Hermes 与 JobFlow。",
    aiAgentConnectionFailed: "已找到 Agent，但它没有完成 JobFlow 的本机安全连接测试。请确认当前模型可用后重试。",
    aiAgentHandshakeFailed: "已找到 Agent，但它没有按 JobFlow 安全协议完成连接确认。请重启 Agent 后重试。",
    aiAgentModelUnavailable: "已找到 Agent，但无法安全确认当前模型。请在 Agent 中重新选择模型后重试。",
    aiConnectionRefreshWarning: "AI 已连接并通过测试，但页面状态刷新失败。连接已经保存；请刷新此页面，不要重复连接。",
    aiEngineReady: "AI 结构化能力已验证", aiEngineReadyBody: "连接和结构化证据测试均已通过。AI 会归并同一经历、区分工作/实习/教育/项目并核对数字与引用行；任何 Claim 仍需你确认。", aiEngineMissing: "必须先连接并验证 AI", aiEngineMissingBody: "当前 AI 尚未通过 JobFlow 的结构化证据测试，因此上传和重新分析已暂停。请重新连接或更换 Agent/模型。", aiCapabilityFailed: "AI 虽能连接，但没有通过实体合并、分类、数字保留和逐行证据测试。没有发送私人资料；请更换模型或 Agent。", documentOcrRequired: "该 PDF 没有足够的可提取文字，可能是扫描件。请先 OCR，或上传可编辑 DOCX。", documentExtractionRisk: "文档提取出现字体、表格或阅读顺序风险。没有导入内容；请优先上传可编辑 DOCX 或更清晰的文本型 PDF。", aiFormatFailure: "AI 自动纠正后仍未返回 JobFlow 所需的结构化格式；没有导入内容。", aiNumberFailure: "AI 改动或新增了原文数字，真实性门禁已拒绝本次结果。", aiNumberFailureDetailed: "AI 第 {candidate} 条 Claim 的数字在原文第 {start}–{end} 行中仍找不到（{count} 个）。系统已检查安全格式差异和相邻换行；本次没有导入内容。", numericFormatReview: "数字格式已统一，请人工核对", adjacentWrapReview: "引用已包含相邻换行，请人工核对", aiGroundingFailure: "AI 的 Claim 无法由引用的原文行完整支持；自动纠正后仍未通过。", aiFragmentFailure: "AI 自动纠正后仍返回标题或不完整句子；没有导入内容。", packetMaterialPlan: "岗位材料计划", materialResume: "岗位简历", materialCoverLetter: "求职信", materialPortfolioFile: "作品集文件", materialExternalActions: "真实上传与提交", materialGithub: "GitHub 链接", materialPortfolio: "作品集链接", materialWebsite: "个人网站", materialSameMaster: "由同一份已批准母版生成岗位副本", materialRequestedRequired: "该岗位必需", materialRequestedOptional: "该岗位可选", materialNotRequested: "表单未要求", materialGeneratedOnDemand: "已按岗位临时生成", materialNotGenerated: "未生成", materialBoundPublic: "已绑定已确认公开值（仅显示哈希）", materialMissingValue: "缺少用户确认的链接", materialBoundSecure: "已绑定加密文件", materialMissingFile: "缺少用户提供的文件", materialBlocked: "已锁定，等待逐岗位批准", packetExecutionPlan: "自动投递步骤", executionReady: "已形成完整计划，等待你的审阅", executionNeedsInput: "仍有资料或问题需要你补充", executionNeedsAccount: "访客申请不可用，需要单独决定是否创建账号", executionQueueContinues: "这份申请等待时，JobFlow 会继续处理其他岗位，直到达到你设置的上限。", executionFreshness: "实时确认岗位仍开放", executionGuest: "以访客方式进入申请", executionPrefill: "填写可安全复用的字段", executionUpload: "上传岗位简历及按需材料", executionProtected: "处理敏感或未知问题", executionSubmit: "最终提交", executionNotExecuted: "尚未执行", executionPlanned: "已规划", executionProposed: "只生成建议", executionBlocked: "已停止等待批准", executionNotRequired: "无需执行", gateLiveRead: "需要另行授权只读访问", gateAfterFreshness: "岗位复验后继续", gateAccount: "需要用户决定账号方案", gatePacket: "受当前审阅包约束", gateUpload: "需要单独批准上传", gatePerApplication: "每次申请单独确认", gateNone: "无额外门禁", gateSubmit: "需要新鲜的最终提交批准", applicationReadinessTitle: "自动投递准备度", applicationReadinessBody: "逐项显示资料、AI、Master Resume、Claim 授权与材料模板是否已形成闭环。", readinessReady: "本地申请准备已就绪", readinessNeedsOnboarding: "先完成一次性资料设置", readinessNeedsAi: "需要连接并验证 AI", readinessNeedsMaster: "需要上传简历", readinessNeedsEditableMaster: "需要可编辑 DOCX 母版", readinessNeedsClaims: "至少确认一条 Claim", readinessNeedsClaimApproval: "需要批准 Claim 用于材料", readinessNeedsTemplate: "需要建立安全改写位置", readinessNoBlockers: "当前没有本地准备阻挡项", externalClaimApprovalTitle: "允许已确认 Claim 用于申请材料", externalClaimApprovalBody: "这项批准只允许系统使用你已经确认的确切措辞生成简历、求职信和申请回答；不会打开网站、上传或提交。", externalClaimConsent: "我已检查这些 Claim，并允许按当前措辞用于生成申请材料。", approveExternalClaims: "批准材料使用", externalClaimsApproved: "Claim 材料使用授权已加密保存", externalClaimsCurrent: "当前授权有效 · {count} 条 Claim", externalClaimsCount: "将授权 {count} 条已确认 Claim", externalClaimConfirmFirst: "请先勾选 Claim 材料使用确认框", approvingExternalClaims: "正在绑定并加密保存 Claim 授权…", tailoringManifestTitle: "建立安全简历改写位置", tailoringManifestBody: "AI 只会提出与已确认 Claim 对应的原简历段落；你一次确认后，岗位副本才能在这些位置改写，母版不会变化。", openTailoringManifest: "检查 AI 建议位置", tailoringManifestCurrent: "安全改写位置已批准 · {count} 处", tailoringManifestNeeded: "普通 DOCX 需要一次性确认可改写位置", tailoringCandidateCount: "AI 找到 {count} 个候选位置", selectRecommendedTailoring: "选择 AI 推荐项", tailoringRecommended: "AI 推荐", tailoringManual: "需要你判断", tailoringCategory: "允许替换为哪类 Claim", tailoringManifestConsent: "我已检查以上原简历段落及类型，并允许 JobFlow 只在这些位置生成岗位副本。", approveTailoringManifest: "批准安全改写位置", tailoringSelectOne: "请至少选择一个安全改写位置", tailoringConfirmFirst: "请先勾选安全改写位置确认框", tailoringManifestApproved: "安全改写位置已加密保存", tailoringProposalEmpty: "AI 没找到能与当前已确认 Claim 可靠对应的简历段落。请检查 Claim 后再试。", tailoringProposalStale: "简历或 Claim 已变化，请重新打开并检查建议位置。", tailoringSelectionInvalid: "所选简历位置或类型无效，请重新检查。", loadingTailoringManifest: "正在解析母版并映射已确认 Claim…", approvingTailoringManifest: "正在绑定并加密保存安全改写位置…",
    aiClassificationFailure: "AI 自动纠正后仍存在无法唯一对应的经历实体；没有导入内容。请重新分析，或改用更清晰的文本/DOCX。",
    classificationNormalized: "经历类型或实体关系已安全归并，请重点核对",
    browserAssistManualNavigation: "这个 Next/Continue 必须由你亲自点击。进入下一页后，再点浏览器伴侣中的“继续分析当前页”。",
  },
  en: {
    aiOperatorCommandLabel: "Give AI one instruction", aiOperatorCommandHelp: "Type “Handle this job for me” and paste the company job link. AI keeps understanding and deciding; JobFlow retains browser, material, and safety authority.", aiOperatorCommandDefault: "Handle this job for me", aiOperatorDelegated: "Continues through JobFlow", aiOperatorUserGate: "Needs you only here",
    guidedIntakeEyebrow: "NEXT JOB", guidedIntakeTitle: "Paste a job link to begin", guidedIntakeBody: "Start from the role on the company's website. The companion reads only the job page and form you explicitly choose, then prepares the tailored resume, optional Cover Letter, portfolio items, and one review packet.", guidedIntakeIdle: "Not started", guidedOfficialUrl: "Company job link", guidedIntakeConsent: "For the next 30 minutes, I allow JobFlow to read only the company job page and application-form structure I explicitly choose in the browser. This stage does not fill, upload, or click page controls.", startGuidedIntake: "Connect browser and begin", guidedOpenJob: "Open company job page", cancelGuidedIntake: "Cancel this read and choose another URL", cancelGuidedIntakeConfirm: "Cancel this job read and choose another URL? This will not delete your resume, Profile, or other materials, and it will not modify the recruiting site.", cancellingGuidedIntake: "Cancelling this job read…", guidedCancelled: "This job read was cancelled. You can enter another URL now.", guidedCancelledCompanionReload: "This job read was cancelled, but the browser companion did not confirm that it released the old connection. Reload JobFlow Browser Companion on the extensions page before starting again.", guidedCancelUnavailable: "This job has already been created or queued. Handle it in pending review instead of deleting it silently.", guidedStepOneTitle: "Open the company role", guidedStepOneBody: "Paste the link once and connect the browser.", guidedStepTwoTitle: "Read the role", guidedStepTwoBody: "On the role page, open the JobFlow J icon and choose Read this company job page.", guidedStepThreeTitle: "Read the application form", guidedStepThreeBody: "Click Apply yourself. On the form, open J again and JobFlow will prepare the materials.", guidedStepFourTitle: "Review once", guidedStepFourBody: "Assisted filling appears only after you approve the materials and job questions.", guidedPairing: "Connecting the browser companion…", guidedPaired: "Connected. Open the company job page, then use the JobFlow J icon in the browser toolbar.", guidedAwaitingJob: "Waiting for you to read the company job page.", guidedAwaitingForm: "Role captured. Click Apply on the company page yourself, then use JobFlow J again on the application form.", guidedPreparing: "Preparing the job-specific resume, requested materials, and review packet…", guidedReady: "Materials and the review packet are ready. Complete the single review below.", guidedDeferred: "The approval queue is full. This role is safely waiting and will continue after you handle one item.", guidedFailed: "This read did not complete and the page was not filled or changed. Check the current page and retry.", guidedExtensionMissing: "The browser companion did not respond. Make sure JobFlow Browser Companion is enabled, then retry.", guidedUrlRequired: "Paste an HTTPS role link from the company's own website.", guidedConsentRequired: "Check the read-only job-import permission for this session first.", guidedReadinessRequired: "Complete the readiness items listed above first.", guidedWrongJobPage: "Read the role on the company's own website first, then open the application form linked from it yourself.", guidedFormMissing: "No application fields were found. Open the actual application form, then use JobFlow J again.", guidedJobTitleMissing: "A role title could not be identified reliably. Confirm that this is a specific company job page.", guidedLeaseInvalid: "This job-import connection expired. Start again.", advancedToolsTitle: "Advanced diagnostics and offline QA", advancedToolsBody: "Ordinary use does not need this area. Provide local snapshots only for development tests or when browser import is unavailable.", advancedToolsOpen: "Expand", browserAssistEyebrow: "APPROVED APPLICATION",
    retryCompanionPairing: "Show connection steps again",
    companionClickToPair: "JobFlow tried to connect automatically, but the browser did not confirm it. Click J once on this JobFlow page to recover the connection.",
    companionClickToReconnect: "The companion explicitly lost this binding, but the authorization is still retained. Click J on this JobFlow page to reconnect; JobFlow will not retry silently.",
    companionStatusTemporary: "The companion status is temporarily unavailable. This authorization is still retained and JobFlow will check again later; do not start over.",
    companionSessionActive: "Another browser task is still active. Complete or explicitly stop it before starting this task.",
    guidedExtensionMissing: "The browser companion did not respond. Confirm the current extension is enabled and reloaded; you do not need to grant access on every website.",
    guidedExtensionOutdated: "The Browser Companion version does not match. Update it from the browser extension store, then refresh this page.",
    guidedBindingMissing: "The Browser Companion failed this Windows installation check. Run the JobFlow installer to repair the local secure channel, then refresh this page.",
    browserCompanionChecking: "Detecting the browser companion automatically", browserCompanionReady: "Browser companion ready; tasks connect automatically", browserCompanionUnavailable: "Browser Companion not detected; install it from the extension store and run the JobFlow installer once", browserCompanionUpdateRequired: "Browser companion must be updated to the current version",
    browserAssistRestartRequired: "The extension reloaded, so this assist stopped safely. Reopen the approved application start page and connect again. JobFlow did not retry Next/Continue.",
    browserAssistApplyRestart: "The page may already contain some approved fields or an attachment, but whole-page verification did not finish. This run stopped and was audited; nothing will be filled or uploaded again automatically. Reopen the application start page and begin again.",
    browserAssistManualRestart: "The one-use Next proof was not armed safely. End this application assist and start it again. JobFlow will not retry automatically.",
    browserAssistReloadUnknown: "The extension reloaded during the final-submit window, so the result is safely marked unknown. Answer whether submission succeeded; JobFlow will not retry.",
    brandSubtitle: "Job application pipeline", localOnly: "Local only · DPAPI encrypted", eyebrow: "JOBFLOW SETUP", pageTitle: "JobFlow · Job pipeline",
    browserAssistNavigationStalled: "The page did not reliably advance within 20 seconds. JobFlow stopped and will not retry; end this assist and start again.",
    heroTitle: "Set it up once. Apply continuously.", heroBody: "Build a complete profile from resumes and project materials, AI sources, and your direct answers. Private content is decrypted only on this computer and never written to ordinary project files.",
    demoTitle: "Synthetic demo · no real data", demoBody: "This is a temporary, auto-cleaned tour using fictional content only. File intake and real AI connections are disabled; do not enter personal information here.", demoReview: "View AI and conflict review", demoQueue: "View pending application",
    atsCapabilityTitle: "Official-site and ATS capability boundary", atsCapabilityBody: "Local evidence proves structural analysis; every live page is revalidated and no blanket site compatibility is claimed.", atsLiveUnverified: "Live compatibility: page-by-page", atsUserPresentAssist: "User-present prefill and upload: supported", atsNavigationScoped: "Non-final navigation: explicit controls only", atsActionsBlocked: "Final Submit: you only", atsEvidenceDirect: "Company-site single snapshot", atsEvidenceVertical: "Complete synthetic vertical", atsEvidenceSingle: "Saved single-page form", atsEvidenceSequence: "Saved multi-step sequence",
    offlineDiscoveryTitle: "Parse a saved company careers page", offlineDiscoveryBody: "Reads only local HTML, a saved-page JSON envelope, or Greenhouse / Lever job JSON; page code is not executed, the snapshot is not retained, and no network is used.", companyDomainLabel: "Official company domain", careersUrlLabel: "Original URL of saved page", officialSnapshotLabel: "Careers-page snapshot", analyzeOfficialSnapshot: "Parse jobs read-only", officialInputsRequired: "Enter the official domain and careers-page URL, then choose a local snapshot.", officialSnapshotInvalid: "The snapshot, official domain, and careers-page URL could not be safely matched. Check them and retry.", officialDiscoveryComplete: "Local snapshot parsed; every candidate still needs a live freshness check.", officialCandidatesTitle: "Offline job candidates", officialCandidateCount: "{count} found", officialNoCandidates: "No job link matched the company/approved-ATS boundary.", officialLiveCheckRequired: "Separate authorization required for live verification", officialNotQueued: "Not added to application queue",
    offlineApplicationTitle: "Prepare one offline application", offlineApplicationBody: "Choose a saved JD, official-company job page, and application form. JobFlow only creates local materials and a review packet.", offlineApplicationGuard: "Stops at review", applicationOfficialUrl: "Official company job URL", applicationFormUrl: "Original application-form URL", applicationGuestMode: "Guest application available", guestUnknown: "Unknown", guestYes: "Yes", guestNo: "No", applicationJdFile: "Job description (JD)", applicationOfficialFile: "Saved official job page", applicationFormFile: "Saved application form", applicationEvidenceExcerpt: "One exact company excerpt from the official page", applicationEvidencePlaceholder: "Paste an exact excerpt of at least 12 characters from the official page for grounded Cover Letter generation.", applicationEvidenceHelp: "It must occur in the selected official page and is never treated as personal experience.", offlineApplicationReadyHint: "Finish every readiness item above to enable generation.", offlineApplicationReady: "Readiness passed; choose the saved job files to continue.", offlineApplicationNeedsReadiness: "Complete every item in Application readiness first.", offlineApplicationInputsRequired: "Enter both HTTPS URLs, choose all three local files, and paste an exact official-page excerpt.", prepareOfflineApplication: "Generate materials and add to review", preparingOfflineApplication: "Analyzing the job and generating local materials…", offlineApplicationPrepared: "Job materials generated and added to pending review.", offlineApplicationDeferred: "The review queue is full. All three local evidence files are encrypted in order and will continue automatically when a slot opens.", applicationBundleInvalid: "The selected job files or page metadata could not be safely matched. Check them and retry.", deferredBundleTooLarge: "The queue is full and this local evidence set is too large for safe retention. Review one pending application, then select these files again.",
    progressLabel: "Core profile progress", draftSaved: "Drafts are encrypted when saved", stepSources: "Sources", stepQuestions: "Missing details", stepReview: "Profile & Claim review", stepFinish: "Finish",
    pipelineEyebrow: "JOBFLOW CONTROL", pipelineTitle: "Local application control center", pipelineBody: "See profile readiness, approval capacity, and safety boundaries in one place. This screen never opens recruiting sites or performs external actions.", refreshDashboard: "Refresh status", dashboardRefreshed: "Local control center refreshed", profileReadiness: "Profile readiness", awaitingApproval: "Awaiting you", approvalQueueOnly: "Local review packets only", availableSlots: "Available capacity", deferredJobs: "Waiting in line", continuesUntilLimit: "Other jobs continue until the limit", pendingReviewTitle: "Applications awaiting approval", pendingReviewBody: "Only safe job summaries appear here; private answers and document bodies never do.", safetyBoardTitle: "Active safety boundary", realSites: "Real-site visits", externalActions: "Real external actions", knowledgeWrites: "Knowledge writes", networkMode: "Run mode", externalControl: "External-action master switch", externalControlLocked: "Off", externalControlEnabled: "Enabled", emergencyStop: "Stop all external actions now", emergencyStopConfirm: "Stop all external actions now and invalidate every active action authorization?", emergencyStopped: "All external actions are off and active action authorizations are invalidated.", stoppingExternalActions: "Stopping all external actions…", pipelineReady: "Complete", pipelineNeedsSetup: "Needs setup", aiReadyShort: "AI connected", aiMissingShort: "AI not connected", queueLimit: "limit {limit}", pendingEmpty: "No application currently needs your approval. Offline-processed roles will appear here.", packetHash: "packet {hash}", packetVersion: "version {version}", awaitingApprovalStatus: "Awaiting your decision", safetyGuardOn: "External actions locked", offlineMode: "Local offline only", refreshingDashboard: "Refreshing local control center…", deferredListTitle: "Waiting roles", deferredListBody: "Roles wait here at capacity and resume in order when a slot opens.", deferredEmpty: "No role is currently waiting for queue capacity.", recentDecisionsTitle: "Recent queue decisions", recentDecisionsBody: "Shows local state changes and never labels approval as submission.", recentEmpty: "No queue decision has been completed yet.", safeQueueId: "Safe queue ID", queuedAt: "Queued", viewRecord: "View record", approvalExpiry: "Local approval valid until {time}", statusApproved: "Locally approved", statusClosed: "Closed", statusRevision: "Revision needed", statusDeferred: "Waiting for capacity", statusOther: "Local state: {status}", executionRunsTitle: "Automatic-application execution status", executionRunsBody: "Shows only safe status, the latest checkpoint, and the next step; no private answers, file content, or site session is exposed.", executionRunsEmpty: "No automatic-application execution record exists yet. Review approval is never shown as submission.", executionStatusAwaiting: "Awaiting one-time final confirmation", executionStatusConfirmed: "Confirmed by reliable receipt", executionStatusUnknown: "Submission outcome unknown; manual verification required", executionStatusInvalidated: "Invalidated", executionStatusInterrupted: "Interruption detected; reconciliation required", executionStatusOther: "Execution state: {status}", executionCheckpoint: "checkpoint {sequence}", executionPhaseNow: "latest phase: {phase}", executionNoRetry: "Automatic retry prohibited", executionNextFinal: "Next: obtain one-time final confirmation; live actions remain off", executionNextNone: "Next: none; the run is confirmed", executionNextManual: "Next: manually verify external evidence; never resubmit automatically", executionNextRebuild: "Next: rebuild and review the application packet", executionNextRestart: "Next: reconcile persisted local state without sending again", executionNextOther: "Next: inspect local state manually",
    pipelineBody: "See readiness, approval queues, and user-present company/ATS application status in one place. Every application requires its own authorization.", safetyGuardOn: "Final Submit and automatic retry always locked", offlineMode: "Local prep + user-present assist", assistedMode: "Local prep + user-present assist", authorizedActionsAudited: "Authorized actions audited", browserAssistTitle: "Assisted filling after review", browserAssistBody: "This appears only after the current review packet is approved. JobFlow then fills approved fields, attaches approved materials, and safely advances through explicit Next/Continue controls. Final Submit always stays with you.", browserAssistIdle: "Waiting for an approved application", browserCompanionStep: "Use the same browser companion", browserCompanionHelp: "Job capture and post-review assisted filling use the same JobFlow J extension. Load it once; JobFlow detects and connects it automatically.", browserCompanionNotPaired: "Not paired for this application", browserCompanionPaired: "Browser companion paired", browserAssistBoundaryTitle: "Non-negotiable boundary", browserAssistBoundary: "JobFlow may click one unambiguous Next/Continue only after page validation and fresh one-use authorization. It never signs in, creates accounts, bypasses verification, or clicks final Submit. Unknown outcomes are never retried.", browserAssistCompanyOnly: "Approved company, Greenhouse, Lever, and Workday routes are supported, but every current page is revalidated; offline evidence is never treated as proof of live compatibility.", browserAssistConsent: "For this application only, I authorize 30 minutes of page-by-page form inspection, approved prefill, approved attachment, and explicit non-final Next/Continue navigation. I handle login, verification, unknown answers, and final Submit.", startBrowserAssist: "Start assisted application", startBrowserAssistNow: "Let AI plan and start", startingBrowserAssist: "AI is planning and creating the one-time browser connection…", aiOperatorTitle: "AI task plan", aiOperatorBoundary: "AI may choose only bounded JobFlow tools; JobFlow retains browser, file, and final-submit authority.", aiOperatorRequired: "Connect and verify AI before asking it to operate this application.", aiOperatorReady: "AI produced a bounded task plan; JobFlow is executing it within the approved boundary.", aiOperatorExecuted: "Executed by JobFlow", aiOperatorPending: "Runs after fresh page state", browserAssistConfirmFirst: "Check the authorization box for this real assisted session first.", browserAssistPairing: "Connecting the browser companion automatically…", browserAssistPaired: "Browser Companion connected automatically. JobFlow continues on the approved application page; click J once only if the browser blocks automatic connection.", browserAssistExtensionMissing: "No companion response arrived. Run the installer entry and load the extension in your browser first.", openApprovedApplication: "Open approved application page", browserAssistAwaitingSubmit: "All supported pages are ready. Review them and click final Submit yourself.", browserAssistPageReview: "This page still has fields for you to answer or verify. Complete them, then continue in the browser companion.", browserAssistSupplementalReview: "The page revealed new conditional questions. JobFlow kept every prior confirmation and asks you to review only the new items; final Submit was untouched.", browserAssistHandoff: "This page needs your login, account, CAPTCHA, or MFA action. Complete it yourself, then resume in the companion.", browserAssistNavigating: "This page passed validation; moving to the next page…", browserAssistStepMeta: "{provider} · page {step}/{max}", browserAssistObserving: "Your final Submit click was detected; reading the result page.", browserAssistConfirmed: "Submission confirmed and result-page receipt recorded.", browserAssistUnknown: "The outcome cannot be verified: was it submitted successfully?", browserAssistFailed: "The page clearly reports failure. Nothing was retried; review and approve again before another attempt.", submittedYes: "Yes, submitted successfully", submittedNo: "No, it was not submitted", resolveUnknownConfirm: "Confirm your answer? JobFlow will not retry automatically.", resolvingSubmission: "Saving your submission result…", browserAssistResolved: "Submission result saved.", statusSubmitted: "Reading result", statusSubmissionUnknown: "Outcome needs confirmation", statusConfirmed: "Submission confirmed", installCompanion: "Install browser companion",
    browserAssistNotApproved: "This application has not been approved yet. Approve the current review packet first.", browserAssistRouteUnsupported: "This page is outside the approved company, Greenhouse, Lever, or Workday route, so JobFlow stopped safely.", browserAssistActive: "Another application is already in an assisted session. Finish it or use the emergency stop first.", browserAssistWrongPage: "This page is outside the approved route, or the form changed unsafely after review. JobFlow stopped.", browserAssistSafetyStop: "The page needs login, registration, CAPTCHA, MFA, or another human step. Complete it yourself and resume from the companion; JobFlow never reads credentials.", browserAssistUnsupportedControl: "The form contains a control that cannot yet be filled safely. JobFlow made no page changes.", browserAssistUploadMismatch: "The approved materials do not map one-to-one to the current upload controls. Nothing was uploaded.", browserAssistFinalLocked: "Final Submit is locked in code and only you can click it.", browserAssistLeaseInvalid: "This 30-minute authorization expired. Start again from the application.",
    queuePreferences: "Pending-approval limit", queuePreferencesBody: "JobFlow pauses new intake at this number and continues after you resolve one item.", pendingLimitLabel: "Maximum waiting", saveLimit: "Save limit", limitSaved: "Pending-approval limit saved", viewPacket: "View review packet", closePacket: "Close packet", reviewPacketEyebrow: "LOCAL REVIEW PACKET", reviewPacketTitlePlaceholder: "Local review packet", loadingReviewPacket: "Decrypting and validating local review packet…", packetJob: "Job", packetFit: "Fit analysis", packetGaps: "Hard gaps", packetBullets: "Resume wording and evidence", packetPrefillProposal: "Fields proposed for prefill", packetQuestions: "Complete form field map", packetSensitive: "Sensitive fields requiring a stop", packetUploads: "Pending uploads", packetActions: "Actions bound by approval", packetRoute: "Official-site and ATS route", packetNone: "None", packetOverall: "Overall fit", packetStatus: "Status", packetCreated: "Created", packetClaims: "Claim", packetEvidence: "Evidence", prefillPrivateSource: "From the encrypted local Profile / Answer Bank; plaintext hidden", prefillPublicSource: "From a confirmed public link; content hash only", prefillReady: "Prepared for this one approval", prefillMissing: "No usable answer; prefill prohibited", pendingLimitInvalid: "Enter a whole number from 1 to 1000.", pendingLimitBelowActive: "The limit cannot be below the number of occupied approval slots. Resolve one application first.", reviewPacketUnavailable: "This review packet could not be opened safely. No content was shown; refresh and retry.", packetDecisionTitle: "One decision, then continuous execution", packetDecisionBody: "The ordinary path defaults to approve; switch only to revise or decline. The approval button confirms this hash, job-specific answers, material uploads, and user-present prefill, but never final Submit.", decisionApprove: "Approve and start filling", decisionApproveHelp: "Bind this hash, job answers, uploads, and user-present prefill once; final Submit stays locked.", decisionRevise: "Return for revision", decisionReviseHelp: "Mark materials for revision and invalidate any earlier approval.", decisionReject: "Do not apply", decisionRejectHelp: "Close this application and release one pending-review slot.", decisionConfirm: "The button below confirms the current hash, the job-specific answers above, and your selected decision. Approval immediately starts user-present fill and material upload; JobFlow never clicks final Submit.", confirmDecision: "Confirm this decision", confirmApproveAndStart: "Approve once and start filling", confirmReviseDecision: "Confirm return for revision", confirmRejectDecision: "Confirm do not apply", chooseDecision: "Choose approve, revise, or do not apply first.", confirmDecisionFirst: "Confirm the current decision.", decisionApproved: "Approved and user-present filling started; only you may click final Submit.", decisionRevised: "Returned for revision; prior approval invalidated.", decisionRejected: "Application closed and queue capacity released.", savingQueueDecision: "Saving the decision; if a role is waiting, preparing the next one…", nextApplicationPrepared: "The next job package was prepared automatically and is awaiting review.", nextApplicationNeedsRepair: "The next saved local evidence needs correction; the remaining safe queue was preserved.", reviewPacketStale: "This packet changed. To prevent stale approval, reopen and review the current version.", reviewDecisionUnavailable: "This application is no longer awaiting a decision. Refresh the queue.",
    demoExecutionTitle: "Synthetic automatic-application rehearsal", demoExecutionBody: "Uses fictional data to verify review approval, isolated prefill, material staging, one-time final confirmation, and a reliable fake receipt. Real browser, network, and external actions stay at 0.", demoExecutionApproveFirst: "Open and approve the fictional review packet above before the rehearsal control appears.", demoExecutionReady: "The review packet is approved. Automatic steps can run, but they will stop before the fake final submission.", demoRehearsalConsent: "I confirm this is a local synthetic rehearsal that will not visit or modify a real site.", demoRunRehearsal: "Run to final confirmation", demoRehearsalConfirmFirst: "Confirm that this is only a local synthetic rehearsal.", demoPreparingRehearsal: "Validating prefill and material staging with zero-network fake adapters…", demoRehearsalPrepared: "Automatic steps finished at the final-confirmation gate; temporary files were removed.", demoFinalConsent: "I confirm one fake final submission that creates only a local synthetic receipt.", demoCompleteRehearsal: "Confirm fake final submission", demoFinalConfirmFirst: "Confirm that this is a fake final submission.", demoCompletingRehearsal: "Consuming the one-time fake authorization and validating the synthetic receipt…", demoRehearsalComplete: "The full synthetic loop is confirmed; real-site visits and real external actions remain 0.",
    packetFieldTitle: "Confirm job-specific questions first", packetFieldBody: "These answers apply only to this job and are immediately stored as a local DPAPI-encrypted reference. No plaintext is written to the review packet, project files, or ordinary database fields.", packetFieldSeparateHelp: "Uploads, navigation, account actions, and final submission use their own authorization gates and are not mislabelled as unanswered questions.", packetFieldRequired: "Required", packetFieldOptional: "Optional", packetFieldDecision: "Decision", packetFieldValue: "Answer for this job", packetFieldConfirmValue: "Enter and confirm", packetFieldPreferNot: "Prefer not to answer", packetFieldNotApplicable: "Not applicable", packetFieldValuePlaceholder: "Enter the exact answer requested by this job", packetFieldConsent: "I confirm these answers are accurate for this job and agree to store them encrypted on this device only.", savePacketFields: "Encrypt answers and create a new packet", savingApplicationFields: "Encrypting job-specific answers and rebinding the review packet…", packetFieldsSaved: "Job-specific answers encrypted; review the new packet.", packetFieldsRequired: "Complete every highlighted job-specific question before approving the packet.", packetFieldInvalid: "A job-specific answer is incomplete or does not belong to the current form. Check it and retry.", packetFieldUnknowns: "This condition is not stated on the job page. You may acknowledge the uncertainty; JobFlow will not turn it into a fact.", packetFieldAcknowledgeUnknown: "Acknowledge and keep unknown",
    sourceTitle: "Bring your existing information into JobFlow", sourceBody: "A source must pass AI entity consolidation, classification, and completeness checks before anything can enter Claim review. Rule-split output is no longer shown.",
    sourceIntakeDemoTitle: "Synthetic demo mode does not accept real files", sourceIntakeDemoBody: "Close the demo launch window, then double-click Start JobFlow.cmd to open the real local workspace with file intake enabled.", sourceIntakeReadonlyTitle: "File intake is locked because this profile revision is complete", sourceIntakeReadonlyBody: "Historical revisions are preserved. Create an editable revision to add, delete, or re-analyze sources.", sourceIntakeAiTitle: "Connect AI before analyzing files", sourceIntakeAiBody: "Use Connect AI at the top right and choose a prepared Agent or local model. File selection opens automatically after a successful connection.",
    docsTitle: "Resume & project materials", docsBody: "DOCX, PDF, TXT, MD, or JSON for resumes, cases, certificates, portfolios, and supporting material.", materialType: "Material type", resume: "Resume", projectCase: "Project case", supporting: "Supporting material", portfolioFile: "Portfolio file",
    aiTitle: "AI sources", aiBody: "Use an official ChatGPT export ZIP or a curated AI summary. For exports over 200 MB, pick ZIPzilla Express. The raw ZIP is never retained.", aiType: "AI source type", chatgptExport: "Official ChatGPT export (up to 200 MB)", chatgptExportLarge: "ZIPzilla Express (over 200 MB — unleash the beast)", aiSummary: "AI summary", chooseFile: "Choose local file", uploadAndAnalyze: "Choose & analyze with AI",
    directTitle: "Fill only the missing details", directBody: "Names, contact details, and public links already present in the resume are carried forward. This section highlights only missing eligibility, preference, and policy answers.", startQuestions: "Review missing details", importedSources: "Securely imported", noSources: "No source added from this page yet; the existing secure resume remains available.",
    suggestions: "Autofill suggestions awaiting confirmation", continueQuestions: "Review missing details", questionsTitle: "Confirm only missing or changed information", questionsBody: "Facts recognized from the resume stay available in a collapsed review section instead of being requested again. Eligibility gates must be explicit; legal and signature items still require per-application confirmation.", resolvedProfileFacts: "{count} items already filled from your sources (expand to review or edit)", optionalProfileFacts: "{count} optional profile items (fill only when useful)", saveHint: "Answers never enter CLI arguments or plaintext JSON", saveEncrypted: "Encrypt, save & continue",
    reviewTitle: "Resolve Profile and Claims together", reviewBody: "Only AI-consolidated, completeness-checked Claims are shown. Work, internships, education, and projects remain separate. Confirmation never submits anything externally.", claimsReviewed: "Claims reviewed", conflictsResolved: "Conflicts resolved", conflicts: "Conflicts requiring an individual decision", conflictsHelp: "A conflict exists only when two relevant sources disagree about the same fact. Each item names the exact field and states both values plainly.",
    profileConfirmTitle: "I reviewed the Candidate Profile", profileConfirmBody: "I checked resume, project, skill, education, and outcome sections, excluding unresolved conflicts.", saveReview: "Save review",
    finishTitle: "Complete JobFlow setup", finishBody: "After setup, JobFlow can continuously prepare roles and build the approval queue. Each approved application still needs separate consent before user-present fill and attachment; only you can click the final Submit button.", finalConsent: "I confirm these answers and review decisions are accurate and authorize encrypted storage as my current Candidate Profile and Answer Bank.", completeButton: "Complete JobFlow setup",
    unknown: "Unanswered", confirmed: "Confirmed", preferNot: "Prefer not to answer", notApplicable: "Not applicable", reuse: "Reuse later", confirmEach: "Confirm each application", preferPolicy: "Always prefer not to disclose", doNotStore: "Do not store a value", policy: "Use policy", answerValueLabel: "Answer for {field}", answerStatusLabel: "Answer status for {field}", answerPolicyLabel: "Use policy for {field}",
    accept: "Accept", sourceImported: "Source securely imported", uploadFailed: "Source import failed", aiRepairApplied: "AI automatically corrected an invalid first-pass result", aiRepairFailed: "AI made one automatic correction, but some content still was not supported by its cited source lines. Nothing from this attempt was imported. Retry, or use a clearer DOCX or PDF if it persists.", aiExportRepairFailed: "The ZIP finished its local scan, but after one automatic correction the AI still produced a statement unsupported by the source. The truth gate imported nothing. Retry, or upload a curated AI summary if it persists.", selectLightning: "This ZIP is over 200 MB. Select ZIPzilla Express under AI source type, then choose it again.", lightningZipOnly: "ZIPzilla Express accepts official ChatGPT export ZIP files only.", lightningTooLarge: "This ZIP exceeds the 8 GB local safety limit.", uploadInterrupted: "The local file transfer was interrupted. Keep this page open and retry.", saved: "Encrypted draft saved", reviewSaved: "Review saved", completeSuccess: "JobFlow setup is complete. Setup itself performed no external action; later, only per-application fill and attachment can run, while you retain the final Submit click.", answerFirst: "Complete the required answers and reviews first.",
    confirmAll: "Confirm group", rejectAll: "Exclude group", pending: "Pending", rejected: "Excluded", evidence: "evidence", noConflicts: "No genuine conflicts currently require review", conflictLabel: "Conflict", conflictPending: "Decision required", conflictResolved: "Resolved", conflictLocation: "Exact conflicting field", affectedClaim: "Claim", affectedField: "Field", conflictReason: "Plain explanation", resumeSide: "Resume says", evidenceSide: "Knowledge evidence says", sourceCandidate: "Source", numericMismatch: "The same fact has different values; both cannot be true", multipleValues: "The same questionnaire field has different answers; choose the correct value", noEvidencePreview: "No comparable relevant evidence", chooseResolution: "Which side is correct?", reviewThisConflict: "Review this conflict", answerThisField: "Confirm this field in the questionnaire", useResume: "Resume is correct", useEvidence: "Knowledge evidence is correct", useDirect: "I will answer directly", exclude: "Not a valid Claim/conflict",
    readyAnswers: "Core answers", readyClaims: "Claim review", readyConflicts: "Conflict review", profile: "Profile confirmation", complete: "Complete", incomplete: "Incomplete", loadingInitial: "Loading encrypted profile…", importing: "Importing and analyzing with AI…", savingAnswers: "Encrypting questionnaire draft…", savingReview: "Saving review decisions…", completingOnboarding: "Creating completion records…", savingSuggestion: "Accepting suggestion…", savingLanguage: "Saving language preference…", elapsedWithEstimate: "{elapsed}s elapsed · about {remaining}s remaining", estimatingTime: "{elapsed}s elapsed · estimating time remaining", stillWorking: "Still working — keep this page open", longRunningNoCountdown: "The initial estimate has passed. Work is continuing; the countdown will not restart and pretend otherwise.", uploadStage: "Moving into the local-only secure workspace · {percent}%", uploadEta: "{loaded} / {total} transferred · about {remaining}s at the current speed", uploadMeasuring: "{loaded} / {total} transferred · measuring local transfer speed", aiAnalysisStage: "Local transfer complete · parsing and running AI truth checks", lightningAnalysisStage: "Local transfer complete · streaming the giant ZIP and running AI analysis", aiAnalysisRange: "This stage has run for {elapsed}s · usually {min}–{max} min, depending on the model", aiAnalysisOverdue: "This stage has run for {elapsed}s · longer than usual, still active, and no fake countdown reset", close: "Close", batchProgress: "Source {current}/{total} · {completed} completed", reprocessingAll: "Analyzing all sources in one click…",
    completedSnapshotTitle: "This is a completed read-only snapshot", completedSnapshotBody: "Historical versions are never overwritten. Create a new editable revision to change information.", startRevision: "Create editable revision", revisionStarted: "A new editable revision was created", revisionReady: "The editable revision is now in sync", revisionSyncFailed: "The revision may have been created, but this page could not synchronize. Refresh the page to continue.", invalidLocalResponse: "The local service returned an unrecognized response. Refresh the page or restart JobFlow.",
    attentionRequired: "This still needs attention", serviceRestartRequired: "The page was updated, but the running local service is an older version. Close this page, press Ctrl+C in the launch window, and start JobFlow again.", profileReviewRequired: "At the bottom of Profile & Claim review, check the Candidate Profile confirmation and save the review.", answersIncomplete: "Some questionnaire items still need a decision. Answer each one or explicitly choose not applicable / prefer not to answer.", hardConditionsUnresolved: "A required eligibility condition is still ambiguous. Return to the questionnaire and give the highlighted field a definite answer.", sourcePreviewPending: "An AI result is still awaiting confirmation or discard. Resolve the highlighted source preview first.", sourceAiReanalysisRequired: "At least one source has not passed analysis by the current AI. Re-analyze or delete that source.", claimReviewIncomplete: "At least one Claim still needs Confirm or Exclude. Resolve the highlighted item.", conflictReviewIncomplete: "At least one conflict still needs a resolution. Resolve the highlighted item.", onboardingConfirmationRequired: "Check the final confirmation box first.", onboardingAlreadyComplete: "This revision is already complete. Create an editable revision to make changes.", onboardingRevisionRequired: "This revision is a read-only snapshot. Create an editable revision first.", invalidAnswer: "A questionnaire answer is incomplete or invalid. Check the highlighted field.", invalidClaim: "A Claim edit is incomplete or has an invalid category. Check the highlighted content.", sourceTypeUnsupported: "This source type or file format is not supported. Choose a different file.", sourceSizeInvalid: "The file is empty or exceeds the local safety limit. Check it and retry.", localRequestFailed: "The local operation did not finish. No partial result was committed; check the highlighted area and retry.", privateDeleteRetry: "The local encrypted copy could not be deleted, so the source and review state were restored. Try again later.", privateDeleteRepair: "Local deletion and recovery did not both finish. Run Check JobFlow and do not continue editing this source yet.", privateWriteRetry: "This encrypted save did not complete, so the prior version was restored. Try again later.", privateWriteRepair: "Encrypted saving and recovery did not both finish. Run Check JobFlow and do not make more changes yet.", reviewSavedButIncomplete: "The encrypted review draft was saved. Finish the highlighted item before moving to the final step.",
    previewTitle: "Review AI-consolidated results", previewBody: "Each item is a complete AI-reconstructed Claim tied to one work, internship, education, or project entity. Page wrapping no longer creates fragments.", confirmSource: "Confirm and securely keep source", includeAllClaims: "Include all Claims in one click", selectAllClaims: "Select all", clearAllClaims: "Clear selection", discardPreview: "Discard this import", previewEmpty: "AI found no complete sentences eligible to become Claims. You can still confirm and securely keep this source; a DOCX can still become the editable master.", filteredCandidateNotice: "AI excluded {count} heading, fragment, or unsupported candidate(s). The file can still be securely imported; excluded items will not become Claims.", selectedByDefault: "AI filtered (still needs confirmation)", needsReview: "Did not pass strict AI gate", includeAsClaim: "Include as Claim", reprocess: "Re-analyze with AI", previewReady: "AI-consolidated preview is ready", analyzeAllSources: "Analyze every source", bulkAnalysisHint: "Re-analyze every source whose encrypted original is retained. Official ChatGPT exports must be uploaded again.", bulkAnalysisComplete: "Every eligible source now has a fresh AI review preview", bulkAnalysisPartial: "Completed {completed}; {failed} did not pass AI validation", noReprocessableSources: "No source can be re-analyzed directly. Upload the original ChatGPT export ZIP again.", allSourcesAlreadyPending: "Every eligible source already has an AI result awaiting review", reuploadAndAnalyze: "Upload original ZIP & analyze", reuploadRequired: "Re-upload to analyze", analysisPassed: "Complete AI analysis", analysisPassedSelected: "High-signal selection analyzed", analysisMissing: "Complete AI analysis required", analysisCoverageComplete: "{chunks} chunks · 100% selected-content coverage", analysisCoverageIncomplete: "Coverage proof incomplete · re-analysis required", archiveSelectionBounded: "Full ZIP scan · {safe} safe messages, {selected} high-signal messages analyzed, {omitted} not selected", archiveSelectionAll: "Full ZIP scan · all {selected} safe user messages analyzed", claimCandidates: "AI Claims",
    claimEditTitle: "Editable Claim review", claimEditHelp: "The left checkbox only merges Claims inside the same entity; it does not confirm or accept them. Use the dropdown on the right for each decision.", selectForMerge: "Select for merge", mergeSelected: "Merge selected", editText: "Editable Claim statement", category: "Experience type", claimDecisionLabel: "Claim review decision", deleteClaim: "Delete", restoreClaim: "Restore", splitClaim: "Split", applySplit: "Apply split", splitHelp: "Enter one complete Claim per line (at least two)", splitInputLabel: "Complete split Claims, one per line", mergedTextPrompt: "Edit the merged Claim statement:", chooseTwoClaims: "Select at least two Claims", claimChanged: "Claim updated", transformingClaims: "Updating Claim structure…", reprocessing: "Re-analyzing, consolidating, and correcting with AI if needed…", committingSource: "Confirming AI results…", includingAll: "Including every AI Claim…", discardingSource: "Discarding import…", deletingSource: "Deleting the source and linked content…", deleteSource: "Delete source", deleteSourceConfirm: "Delete this source and every Claim and suggestion derived from it? Its local encrypted copy will also be deleted. This cannot be undone.", sourceDeleted: "Source and linked content deleted", startingRevision: "Creating encrypted revision…", readonly: "Read only", aiEngineReady: "AI core connected", aiEngineReadyBody: "AI reconstructs complete sentences, consolidates repeated experience, and separates work, internships, education, and projects. Every Claim still needs your confirmation.", aiEngineMissing: "AI connection required", aiEngineMissingBody: "No AI is available, so uploads and re-analysis are paused and no rule-split candidates will be shown. Connect AI before analyzing retained sources.", aiMode: "Analysis mode", legacyQuarantined: "{count} legacy rule-derived items are quarantined. They cannot enter Claims, the Profile, or applications.", invalidConflictsSuppressed: "{count} unrelated or non-comparable legacy evidence mappings were excluded. They are not conflicts.", work: "Work", internship: "Internship", education: "Education", project: "Project", skill: "Skill", certification: "Certification", language: "Language", summary: "Professional summary", entityClaims: "Claims", entityUnknown: "Unnamed entity", valueDifference: "Same {dimension}: resume says {left}; knowledge evidence says {right}.", resumeMetrics: "Resume values", evidenceMetrics: "Evidence values",
    connectAi: "Connect AI", aiConnectedButton: "AI connected", aiConnectionEyebrow: "AI CONNECTION", aiConnectionTitle: "Connect an AI you already prepared", aiConnectionBody: "JobFlow does not ask for another model key. It checks Windows and WSL automatically, then connects through a local-only route.", existingAgentTitle: "Use an existing Agent", existingAgentBody: "Automatically detect Hermes / OpenClaw on Windows or WSL and reuse the model already configured by that Agent.", localModelTitle: "Use a local model", localModelBody: "Automatically detect Ollama, LM Studio, LocalAI, llama.cpp, or vLLM on Windows or WSL.", customApiTitle: "Custom API / adapter", customApiBody: "Reserved for enterprise models, private gateways, and other Agents. Most users do not need it.", detectAndConnect: "Detect and connect", reserved: "Interface reserved", aiPrivacyNote: "JobFlow never reads or stores an Agent API key, cookie, or login token. WSL connections never expose a LAN port; private requests use stdin or loopback only. Hermes and OpenClaw both connect in zero-tool analysis mode: action tools are disabled and any tool call invalidates the result. With an Agent, data routing follows that Agent's current local or cloud model configuration.", aiNotConnectedStatus: "No verified AI is connected yet. Choose an existing Agent or local model.", aiConnectedStatus: "Connected and structurally verified: {name}", aiConnectedModel: "Model: {model} · Data route: {route}", detectingAgent: "Checking Windows and WSL for Hermes / OpenClaw and verifying structured evidence output…", detectingLocalModel: "Checking Windows and WSL for a local model and verifying structured evidence output…", aiConnectionSucceeded: "AI connected and passed the structured-evidence test", aiConnectionFailed: "No ready AI was found on Windows or WSL. Start an Agent or local model service and try again.", aiWslHermesAuthRequired: "Hermes was found in WSL, but its selected model or sign-in is not currently usable. Confirm the model in Hermes, then retry.", aiWslProxyStartFailed: "Hermes was found in WSL, but the local-only bridge did not start. Confirm its model is ready, then retry.", aiWslBridgeMissing: "WSL was detected, but curl is missing there. Install curl in that WSL environment to enable the safe local bridge.", aiAgentSafetyRejected: "The Agent attempted a tool call or did not provide a verifiable zero-tool audit, so JobFlow rejected the connection.",
    aiWindowsHermesAuthRequired: "Hermes was found on Windows, but JobFlow could not safely read its active model or provider. Select and confirm the model in Hermes, then try again.",
    aiWindowsHermesConnectionFailed: "Hermes and its active model were found on Windows, but JobFlow's isolated zero-tool connection test failed. A working Hermes chat does not mean this safe bridge is ready; restart Hermes and JobFlow, then try again.",
    aiWindowsHermesProxyFailed: "Hermes was found on Windows, but its compatibility proxy exposed no ready model. Confirm the active Hermes model and retry; if chat works but this remains, update Hermes and JobFlow.",
    aiAgentConnectionFailed: "An Agent was found, but it did not complete JobFlow's local safety connection test. Confirm that its active model works, then retry.",
    aiAgentHandshakeFailed: "An Agent was found, but it did not complete the JobFlow safety handshake. Restart the Agent, then retry.",
    aiAgentModelUnavailable: "An Agent was found, but JobFlow could not safely confirm its active model. Select the model again in the Agent, then retry.",
    aiConnectionRefreshWarning: "AI connected and passed its tests, but the page state could not refresh. The connection was saved; refresh this page instead of connecting again.",
    aiEngineReady: "AI structured capability verified", aiEngineReadyBody: "Both connection and structured-evidence tests passed. AI consolidates duplicate experience, separates work/internships/education/projects, and checks numbers against cited lines. Every Claim still needs confirmation.", aiEngineMissing: "Connect and verify AI", aiEngineMissingBody: "The current AI has not passed JobFlow's structured-evidence test, so uploads and re-analysis are paused. Reconnect or choose another Agent/model.", aiCapabilityFailed: "The AI connected but failed the entity, classification, metric-preservation, and line-grounding test. No private content was sent; choose another model or Agent.", documentOcrRequired: "This PDF has too little extractable text and may be scanned. Run OCR first or upload an editable DOCX.", documentExtractionRisk: "Extraction found font, table, or reading-order risk. Nothing was imported; prefer an editable DOCX or cleaner text-based PDF.", aiFormatFailure: "The corrected AI output still did not match JobFlow's structured format. Nothing was imported.", aiNumberFailure: "The AI changed or introduced a source number, so the truth gate rejected this result.", aiNumberFailureDetailed: "AI Claim {candidate} still contains {count} number(s) not found in source lines {start}–{end}. Safe format normalization and adjacent-wrap checks were attempted; nothing was imported.", numericFormatReview: "Number formatting normalized—verify it", adjacentWrapReview: "Adjacent wrapped lines included—verify them", aiGroundingFailure: "The AI Claim was not fully supported by its cited lines after correction. Nothing was imported.", aiFragmentFailure: "The corrected AI output still contained a heading or sentence fragment. Nothing was imported.", packetMaterialPlan: "Per-job material plan", materialResume: "Job-specific resume", materialCoverLetter: "Cover Letter", materialPortfolioFile: "Portfolio file", materialExternalActions: "Real upload and submission", materialGithub: "GitHub link", materialPortfolio: "Portfolio link", materialWebsite: "Personal website", materialSameMaster: "Generated from the same approved Master Resume", materialRequestedRequired: "Required by this form", materialRequestedOptional: "Optional on this form", materialNotRequested: "Not requested by the form", materialGeneratedOnDemand: "Generated on demand for this job", materialNotGenerated: "Not generated", materialBoundPublic: "Confirmed public value bound (hash shown only)", materialMissingValue: "Confirmed link is missing", materialBoundSecure: "Encrypted file is bound", materialMissingFile: "User-provided file is missing", materialBlocked: "Locked pending per-job approval", packetExecutionPlan: "Application execution steps", executionReady: "Complete plan prepared for your review", executionNeedsInput: "Some material or answers still need your input", executionNeedsAccount: "Guest apply is unavailable; account creation needs a separate decision", executionQueueContinues: "While this application waits, JobFlow continues with other jobs until your selected limit.", executionFreshness: "Confirm the role is still open", executionGuest: "Enter the application as a guest", executionPrefill: "Fill safely reusable fields", executionUpload: "Upload the job-specific resume and requested materials", executionProtected: "Handle sensitive or unknown questions", executionSubmit: "Final submission", executionNotExecuted: "Not executed", executionPlanned: "Planned", executionProposed: "Proposal only", executionBlocked: "Stopped for approval", executionNotRequired: "Not required", gateLiveRead: "Separate read-only authorization required", gateAfterFreshness: "Continue after freshness check", gateAccount: "User account decision required", gatePacket: "Bound to this review packet", gateUpload: "Separate upload approval required", gatePerApplication: "Confirm for each application", gateNone: "No additional gate", gateSubmit: "Fresh final-submit approval required", applicationReadinessTitle: "Application readiness", applicationReadinessBody: "Shows whether profile, AI, Master Resume, Claim authorization, and the safe material template form a complete local loop.", readinessReady: "Offline application preparation is ready", readinessNeedsOnboarding: "Complete the one-time setup", readinessNeedsAi: "Connect and verify AI", readinessNeedsMaster: "Upload a resume", readinessNeedsEditableMaster: "Add an editable DOCX master", readinessNeedsClaims: "Confirm at least one Claim", readinessNeedsClaimApproval: "Approve Claim use for materials", readinessNeedsTemplate: "Build safe tailoring positions", readinessNoBlockers: "No local preparation blockers remain", externalClaimApprovalTitle: "Allow confirmed Claims in application materials", externalClaimApprovalBody: "This permits only the exact Claim wording you confirmed to generate resumes, Cover Letters, and application answers. It does not open a site, upload, or submit.", externalClaimConsent: "I reviewed these Claims and allow their current wording in generated application materials.", approveExternalClaims: "Approve material use", externalClaimsApproved: "Encrypted Claim material authorization saved", externalClaimsCurrent: "Current authorization · {count} Claims", externalClaimsCount: "Will authorize {count} confirmed Claims", externalClaimConfirmFirst: "Check the Claim material-use confirmation first", approvingExternalClaims: "Binding and encrypting Claim authorization…", tailoringManifestTitle: "Build safe resume tailoring positions", tailoringManifestBody: "AI proposes only original resume paragraphs mapped to confirmed Claims. Once you approve them, JobFlow may edit only those positions in job-specific copies; the master stays unchanged.", openTailoringManifest: "Review AI-proposed positions", tailoringManifestCurrent: "Safe tailoring positions approved · {count}", tailoringManifestNeeded: "An ordinary DOCX needs one-time tailoring-position approval", tailoringCandidateCount: "AI found {count} candidate positions", selectRecommendedTailoring: "Select AI recommendations", tailoringRecommended: "AI recommended", tailoringManual: "Review manually", tailoringCategory: "Claim category allowed here", tailoringManifestConsent: "I reviewed these original resume paragraphs and categories and allow JobFlow to generate job-specific copies only at these positions.", approveTailoringManifest: "Approve safe tailoring positions", tailoringSelectOne: "Select at least one safe tailoring position", tailoringConfirmFirst: "Check the safe-tailoring confirmation first", tailoringManifestApproved: "Encrypted safe-tailoring positions saved", tailoringProposalEmpty: "AI could not reliably map any current confirmed Claim to an original resume paragraph. Review the Claims and try again.", tailoringProposalStale: "The resume or Claims changed. Reopen and review the proposed positions.", tailoringSelectionInvalid: "The selected resume position or category is invalid. Review it again.", loadingTailoringManifest: "Inspecting the master and mapping confirmed Claims…", approvingTailoringManifest: "Binding and encrypting safe-tailoring positions…",
    aiClassificationFailure: "The corrected AI output still contained an experience entity that could not be matched unambiguously. Nothing was imported; re-analyze or use a clearer text/DOCX source.",
    classificationNormalized: "Experience type or entity relation normalized—review carefully",
    browserAssistManualNavigation: "You must click this Next/Continue yourself. On the new page, choose Continue on this page in the Browser Companion.",
  }
};

Object.assign(STRINGS.zh,{
  guidedIntakeTitle:"一句话即可开始",
  guidedIntakeBody:"输入岗位方向，或粘贴已有官网岗位链接。JobFlow 会在可见标签页中搜索、核验官网、沿公司公布的 Apply 路径读取申请表，并自动准备岗位材料。",
  guidedOfficialUrl:"已有公司官网岗位链接（可选）",
  guidedIntakeConsent:"点击开始即授权 30 分钟可见搜索和只读页面核验；看到具体岗位与材料后只需批准一次，才会预填和上传。最终 Submit 永远由你点击。",
  startGuidedIntake:"开始连续处理",
  guidedStepOneTitle:"自动搜索与核验",
  guidedStepOneBody:"JobFlow 使用你的岗位目标搜索可见结果，并只选择经过核验的公司官网岗位。",
  guidedStepTwoBody:"自动读取选中的官网 JD；结果不唯一时才请你选择。",
  guidedStepThreeBody:"沿公司公布且验证通过的 Apply 路径进入申请表并准备材料。",
  guidedStoppedTitle:"本次处理已停止",
  guidedStoppedHint:"错误状态会保留在这里，直到你重试或取消本次读取。网页没有被填写或修改。",
  guidedEligibilityReview:"岗位和申请表已经读取完成，但发现需要你确认的匹配条件：{gaps}。没有生成申请材料，也没有修改招聘网页。",
  guidedLevelPreferenceMismatch:"职位级别与已保存的目标偏好不同",
  guidedGapWorkAuthorization:"工作授权条件冲突",
  guidedGapVisa:"签证担保条件冲突",
  guidedGapLocation:"地点偏好不一致",
  guidedGapSalary:"薪资偏好不一致",
  guidedGapRequirement:"岗位硬性要求仍有冲突",
  guidedGapUnknown:"未识别的匹配条件"
});
Object.assign(STRINGS.en,{
  guidedIntakeTitle:"Start with one instruction",
  guidedIntakeBody:"Describe the role you want, or paste an existing official job URL. JobFlow searches visibly, verifies the company page, follows its published Apply route, reads the form, and prepares the materials.",
  guidedOfficialUrl:"Official company job URL (optional)",
  guidedIntakeConsent:"Starting grants a 30-minute visible-search and read-only page-verification session. After the exact role and materials appear, one approval enables prefill and upload. Only you may click final Submit.",
  startGuidedIntake:"Start continuous workflow",
  guidedStepOneTitle:"Search and verify automatically",
  guidedStepOneBody:"JobFlow searches visible results from your role goal and selects only a verified official company job.",
  guidedStepTwoBody:"The selected official JD is read automatically; you choose only when the result is genuinely ambiguous.",
  guidedStepThreeBody:"JobFlow follows a verified company-published Apply route, reads the form, and prepares materials.",
  guidedStoppedTitle:"This run stopped",
  guidedStoppedHint:"This error stays visible until you retry or cancel the read. The recruiting page was not filled or changed.",
  guidedEligibilityReview:"The role and form were read, but these match conditions need your review: {gaps}. No application material was generated and the recruiting page was not changed.",
  guidedLevelPreferenceMismatch:"Role level differs from the saved target preference",
  guidedGapWorkAuthorization:"Work-authorization conflict",
  guidedGapVisa:"Visa-sponsorship conflict",
  guidedGapLocation:"Location preference mismatch",
  guidedGapSalary:"Salary preference mismatch",
  guidedGapRequirement:"A stated job requirement still conflicts",
  guidedGapUnknown:"Unrecognized match condition"
});

Object.assign(STRINGS.zh,{
  aiOperatorCommandHelp:"说清岗位方向和地点即可；也可以粘贴公司官网岗位链接。JobFlow 会在可见标签页中搜索、核验官网、进入 Apply 并准备申请。",
  aiOperatorCommandDefault:"帮我寻找并处理最匹配的岗位",
  guidedIntakeTitle:"说出目标，一步运行到最终提交前",
  guidedIntakeBody:"JobFlow 会自动搜索并核验公司官网岗位、进入安全 Apply 路径、读取申请表并准备材料。你只需审阅一次；最终 Submit 永远由你点击。",
  guidedOfficialUrl:"已有公司官网岗位链接（可选）",
  guidedIntakeConsent:"我确认本次岗位目标，并允许 JobFlow 在接下来的 30 分钟内进行可见搜索、读取官网岗位与申请表、准备材料；批准审阅包后可预填和上传，但最终 Submit 永远由我点击。",
  startGuidedIntake:"确认并运行",
  guidedStepOneTitle:"搜索并核验官网",guidedStepOneBody:"JobFlow 使用可见浏览器搜索，只保留公司官网岗位。",
  guidedStepTwoTitle:"读取岗位并进入 Apply",guidedStepTwoBody:"核验岗位后沿官网发布的安全 Apply 路径进入申请表。",
  guidedStepThreeTitle:"读取表单并准备材料",guidedStepThreeBody:"生成岗位简历和按需材料，只列出真正需要确认的问题。",
  guidedStepFourTitle:"一次审阅后持续填写",guidedStepFourBody:"批准后自动逐页预填和上传，停在最终 Submit 前。",
  guidedPairing:"正在连接浏览器伴侣并开始任务…",guidedPaired:"浏览器已连接，正在自动搜索或核验岗位。",
  guidedAwaitingJob:"正在打开并核验公司官网岗位页。",guidedAwaitingForm:"岗位已核验，正在沿安全 Apply 路径进入申请表。",
  guidedUrlRequired:"请说清要寻找的岗位方向，或粘贴公司官网 HTTPS 岗位链接。",
  guidedConsentRequired:"请先确认本次岗位目标和运行授权。",
  guidedExtensionMissing:"没有收到浏览器伴侣响应。请确认扩展已启用；首次使用请打开 J 授权一次。",
  guidedSearchChoiceTitle:"AI 找到多个同样可信的公司官网岗位，请选择一个继续：",
  guidedSearchChoiceAction:"选择并继续",
  guidedSearchChoiceSaving:"正在绑定你选择的公司岗位并继续…",
  packetFieldTitle:"只补充简历和既有资料无法确定的问题",
  packetFieldBody:"把当前岗位确实需要、但简历与已确认资料中没有的答案填在这里。它们会随下面同一次批准在本机加密保存，不需要另点保存。",
  packetDecisionTitle:"一次确认：答案、材料与开始填写",
  packetDecisionBody:"批准会加密保存上面的岗位专属答案，并立即在刚才的申请标签页预填和上传。登录、验证码、敏感或未知问题会停下；最终 Submit 永远由你点击。",
  decisionApprove:"批准并运行到最终提交前",
  decisionApproveHelp:"绑定当前审阅包和上面的答案，随后连续逐页填写与上传；不会点击最终 Submit。",
  decisionConfirm:"我已审阅当前岗位、材料和补充答案，并允许 JobFlow 在我在场时运行到最终 Submit 前。",
  confirmDecision:"确认并开始填写",
  decisionApprovedAndStarted:"审阅已批准，JobFlow 正在刚才的申请标签页继续预填和上传。最终 Submit 仍由你点击。"
});
Object.assign(STRINGS.en,{
  aiOperatorCommandHelp:"Describe the role direction and location, or paste a company job link. JobFlow searches in a visible tab, verifies the official page, follows Apply, and prepares the application.",
  aiOperatorCommandDefault:"Find and handle the best matching role for me",
  guidedIntakeTitle:"State the goal; run to final review",
  guidedIntakeBody:"JobFlow searches and verifies official company roles, enters the safe Apply route, reads the form, and prepares materials. Review once; only you click final Submit.",
  guidedOfficialUrl:"Existing company job link (optional)",
  guidedIntakeConsent:"I confirm this job goal and allow JobFlow for 30 minutes to perform visible search, read the official job and application form, and prepare materials. After packet approval it may prefill and upload, but only I click final Submit.",
  startGuidedIntake:"Confirm and run",
  guidedStepOneTitle:"Search and verify official roles",guidedStepOneBody:"JobFlow uses a visible browser search and keeps only company careers pages.",
  guidedStepTwoTitle:"Read the role and enter Apply",guidedStepTwoBody:"After verification, JobFlow follows the safe Apply route published by the company.",
  guidedStepThreeTitle:"Read the form and prepare materials",guidedStepThreeBody:"Build the job resume and requested materials, then list only questions that truly need you.",
  guidedStepFourTitle:"Review once, then keep filling",guidedStepFourBody:"Approval starts continuous page-by-page prefill and upload, stopping before final Submit.",
  guidedPairing:"Connecting the browser companion and starting the task…",guidedPaired:"Browser connected; automatically searching or verifying the role.",
  guidedAwaitingJob:"Opening and verifying the official company role.",guidedAwaitingForm:"Role verified; following the safe Apply route to the form.",
  guidedUrlRequired:"Describe the role you want, or paste an HTTPS role link from the company website.",
  guidedConsentRequired:"Confirm this job goal and run permission first.",
  guidedExtensionMissing:"The browser companion did not respond. Make sure it is enabled; on first use, open J once to grant access.",
  guidedSearchChoiceTitle:"AI found several equally credible company roles. Choose one to continue:",
  guidedSearchChoiceAction:"Choose and continue",
  guidedSearchChoiceSaving:"Binding the selected company role and continuing…",
  packetFieldTitle:"Only answer what the resume and approved profile cannot determine",
  packetFieldBody:"Provide only application-specific answers missing from the resume and confirmed profile. They are encrypted with the single approval below; there is no separate save step.",
  packetDecisionTitle:"One confirmation: answers, materials, and start",
  packetDecisionBody:"Approval encrypts the application-specific answers above and immediately continues prefill and uploads in the inspected application tab. Login, verification, sensitive, or unknown questions pause; only you click final Submit.",
  decisionApprove:"Approve and run to final review",
  decisionApproveHelp:"Bind this packet and the answers above, then continue page by page without ever clicking final Submit.",
  decisionConfirm:"I reviewed this role, its materials, and the answers above, and allow JobFlow to run while I am present until final Submit.",
  confirmDecision:"Confirm and start filling",
  decisionApprovedAndStarted:"Review approved. JobFlow is continuing prefill and uploads in the application tab it just inspected. Only you click final Submit."
});

Object.assign(STRINGS.zh,{
  workflowNowLabel:"当前下一步", workflowLoadingTitle:"正在读取本机状态", workflowLoadingDetail:"完成后会在这里给出唯一的继续入口。",
  workflowWorkingDetail:"已持续 {elapsed} 秒。任务仍在运行；此状态不会因浮动提示关闭而消失。", workflowWorkingEstimate:"已持续 {elapsed} 秒，按当前估计约还需 {remaining} 秒。", workflowUploadDetail:"本机安全传送已完成 {percent}%。此状态会一直保留到处理结束。",
  workflowConnectAiTitle:"先连接并验证 AI", workflowConnectAiDetail:"JobFlow 需要一个已配置好的 Agent 或本地模型来理解资料和岗位。连接成功后会自动继续显示下一步。", workflowConnectAiAction:"连接 AI",
  workflowSourcesTitle:"先把已有资料交给 JobFlow", workflowSourcesDetail:"上传简历和可选项目资料；AI 会提取已有事实，后面只追问仍然缺失的内容。", workflowSourcesAction:"打开资料来源",
  workflowQuestionsTitle:"只补充仍然缺失的资料", workflowQuestionsDetail:"已从简历识别出的字段会折叠保存；这里只需要完成仍未解决的必填项。", workflowQuestionsAction:"继续补充资料",
  workflowReviewTitle:"集中审阅 Profile 与 Claim", workflowReviewDetail:"确认 AI 整理出的个人事实、Claim 和真正冲突；不会自动批准任何对外表述。", workflowReviewAction:"开始集中审阅",
  workflowFinishTitle:"完成一次性资料确认", workflowFinishDetail:"最后确认当前 Profile 和答案库，之后每个岗位只补充岗位专属问题。", workflowFinishAction:"完成资料设置",
  workflowReadyTitle:"资料已就绪，可以处理下一份岗位", workflowReadyDetail:"说出岗位目标或粘贴公司官网链接；JobFlow 会连续运行到一次审阅或必须由你处理的安全门。", workflowReadyAction:"开始处理岗位",
  workflowReadinessTitle:"完成自动投递准备项", workflowReadinessDetail:"主资料已完成，但材料授权或安全改写位置仍未闭环。控制台会列出唯一缺口。", workflowReadinessAction:"查看准备缺口",
  workflowGuidedAction:"查看岗位处理进度", workflowReviewPacketTitle:"岗位材料已准备，等待一次审阅", workflowReviewPacketDetail:"检查岗位专属答案和材料；一次批准后才会开始预填和上传，最终 Submit 仍由你点击。", workflowReviewPacketAction:"打开一次审阅",
  workflowBrowserTitle:"正在处理已批准的申请", workflowBrowserDetail:"JobFlow 正在用户在场模式下逐页校验；登录、验证码、未知问题和最终 Submit 会停下来交给你。", workflowBrowserAction:"查看填写状态",
  workflowFailureAction:"查看原因并重试", workflowContinue:"继续",
  preparingGuidedApplication:"正在根据岗位生成材料与审阅包…", startingGuidedIntake:"正在建立连续岗位任务…", startingBrowserAssist:"正在建立用户在场填写任务…", resolvingSubmission:"正在保存提交结果判断…"
});
Object.assign(STRINGS.en,{
  workflowNowLabel:"Current next step", workflowLoadingTitle:"Reading local state", workflowLoadingDetail:"The single continuation entry will remain here when loading finishes.",
  workflowWorkingDetail:"Running for {elapsed}s. This state remains here even if the floating notice closes.", workflowWorkingEstimate:"Running for {elapsed}s · about {remaining}s remaining at the current estimate.", workflowUploadDetail:"{percent}% transferred into local secure processing. This state remains until processing finishes.",
  workflowConnectAiTitle:"Connect and verify AI first", workflowConnectAiDetail:"JobFlow needs an already configured Agent or local model to understand sources and roles. The next step appears automatically after verification.", workflowConnectAiAction:"Connect AI",
  workflowSourcesTitle:"Give JobFlow the sources you already have", workflowSourcesDetail:"Upload the resume and optional project sources. AI extracts existing facts so later questions cover only genuine gaps.", workflowSourcesAction:"Open sources",
  workflowQuestionsTitle:"Fill only the remaining gaps", workflowQuestionsDetail:"Fields recovered from the resume stay collapsed and saved; only unresolved required items remain here.", workflowQuestionsAction:"Continue missing details",
  workflowReviewTitle:"Review Profile and Claims together", workflowReviewDetail:"Confirm the personal facts, Claims, and genuine conflicts organized by AI. No external wording is auto-approved.", workflowReviewAction:"Start consolidated review",
  workflowFinishTitle:"Complete the one-time profile confirmation", workflowFinishDetail:"Confirm the current Profile and answer bank once; future roles ask only role-specific questions.", workflowFinishAction:"Finish profile setup",
  workflowReadyTitle:"Profile ready — process the next role", workflowReadyDetail:"Describe the role or paste an official company URL. JobFlow continues until one review or a safety gate genuinely needs you.", workflowReadyAction:"Process a role",
  workflowReadinessTitle:"Finish application-readiness items", workflowReadinessDetail:"The core profile is complete, but a material permission or safe tailoring position is still missing. The console lists the exact gap.", workflowReadinessAction:"View readiness gaps",
  workflowGuidedAction:"View job progress", workflowReviewPacketTitle:"Role materials are ready for one review", workflowReviewPacketDetail:"Review role-specific answers and materials. Only one approval starts prefill and upload; final Submit remains yours.", workflowReviewPacketAction:"Open one review",
  workflowBrowserTitle:"Processing the approved application", workflowBrowserDetail:"JobFlow is validating each page while you are present. Login, verification, unknown questions, and final Submit stop for you.", workflowBrowserAction:"View filling status",
  workflowFailureAction:"Review the cause and retry", workflowContinue:"Continue",
  preparingGuidedApplication:"Generating role materials and the review packet…", startingGuidedIntake:"Starting the continuous role workflow…", startingBrowserAssist:"Starting the user-present filling session…", resolvingSubmission:"Saving the submission-result decision…"
});

Object.assign(STRINGS.zh,{
  aiOperatorTitle:"AI 当前决策",
  aiOperatorActivityTitle:"AI 当前决策与执行记录",
  aiOperatorActivityBody:"AI 每次只根据最新状态选择一个受限动作；JobFlow 校验并执行后，才会把新状态交给 AI。",
  aiOperatorActivityIdle:"尚无 AI 决策",
  aiOperatorActivityTurns:"{count} 次已验证决策",
  aiOperatorReady:"AI 已选择当前受限动作，JobFlow 正在批准边界内执行。",
  aiOperatorPending:"等待当前状态执行",
  aiOperatorSelected:"AI 已选择",
  aiOperatorVerified:"JobFlow 已验证",
  aiOperatorRejected:"JobFlow 已拒绝",
  startBrowserAssistNow:"让 AI 决策并开始",
  startingBrowserAssist:"AI 正在判断当前动作并建立一次性浏览器连接…",
  aiToolSearchOfficialJobs:"搜索并核验公司官网岗位",
  aiToolStartGuidedIntake:"建立只读岗位任务",
  aiToolPlanResumeChanges:"选择获批简历证据",
  aiToolInspectApplicationForm:"理解当前申请表",
  aiToolStartUserPresentAssist:"开始用户在场填写"
});
Object.assign(STRINGS.en,{
  aiOperatorTitle:"AI current decision",
  aiOperatorActivityTitle:"AI decisions and execution",
  aiOperatorActivityBody:"AI selects one bounded action from fresh state. Only after JobFlow validates and executes it does AI receive the next state.",
  aiOperatorActivityIdle:"No AI decision yet",
  aiOperatorActivityTurns:"{count} verified decisions",
  aiOperatorReady:"AI selected the current bounded action; JobFlow is executing it within the approved boundary.",
  aiOperatorPending:"Waiting on current state",
  aiOperatorSelected:"Selected by AI",
  aiOperatorVerified:"Verified by JobFlow",
  aiOperatorRejected:"Rejected by JobFlow",
  startBrowserAssistNow:"Let AI decide and start",
  startingBrowserAssist:"AI is selecting the current action and creating the one-time browser connection…",
  aiToolSearchOfficialJobs:"Search and verify official company roles",
  aiToolStartGuidedIntake:"Create the read-only job task",
  aiToolPlanResumeChanges:"Select approved resume evidence",
  aiToolInspectApplicationForm:"Understand the current form",
  aiToolStartUserPresentAssist:"Start user-present filling"
});

const UI_PROTOCOL_VERSION = 31;
const AI_QUALITY_CONTRACT = "ENTITY_DEDUPED_LINE_ANCHORED_V6";
const COMPANION_EXTENSION_IDS = [
  "hhlliaaafegldkmcgmaoaelabipcaooj",
  "pgcnlkfakkacphkdojdbphccjnbbefic",
  "cebejbohadiofomfiplljnpdefjeiccp"
];
const COMPANION_VERSION = "0.9.1";
const COMPANION_PAIRING_STORAGE = "jobflow-companion-pairing-v2";
const COMPANION_POLL_BASE_MS = 1500;
const COMPANION_POLL_MAX_MS = 12000;
const state = { locale: "zh", data: null, serviceCompatible: false, lastBlockingError: null, reviewPacket: null, reviewDecision: "", officialDiscovery: null, tailoringProposal: null, guidedIntakeSession: null, guidedIntakePairTimer: null, guidedIntakeActivity: null, browserAssistSelection: null, browserAssistSession: null, aiOperatorPlan: null, aiOperatorExecution: null, browserAssistPairTimer: null, companionPairing: null, companionStatusTimer: null, companionPollInFlight: false, companionPollFailures: 0, companionConnectionNotice: null, companionTerminalHandled: null, companionAvailability: {status:"CHECKING",extension_version:null}, aiConnectionErrorCode: null, aiConnectionRefreshWarning: false, answerDraft: {}, claimDraft: {}, claimEditDraft: {}, conflictDraft: {}, selectedClaims: new Set(), activities: [], activityDurations: {} };
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
  reprocessingAll: 45, refreshingDashboard: 5, loadingReviewPacket: 5, savingQueueDecision: 7, savingApplicationFields: 8,
  discoveringJobs: 8, approvingExternalClaims: 7, loadingTailoringManifest: 8, approvingTailoringManifest: 7,
  preparingOfflineApplication: 150, startingGuidedIntake: 10, cancellingGuidedIntake: 5, preparingGuidedApplication: 300, startingBrowserAssist: 10, resolvingSubmission: 5
};
const STANDARD_CHATGPT_EXPORT_BYTES = 200 * 1024 * 1024;
const MAX_RETAINED_SOURCE_BYTES = 64 * 1024 * 1024;
const MAX_LIGHTNING_EXPORT_BYTES = 8 * 1024 * 1024 * 1024;
const MAX_OFFICIAL_SNAPSHOT_BYTES = 32 * 1024 * 1024;
const MAX_APPLICATION_JD_BYTES = 32 * 1024 * 1024;
const MAX_APPLICATION_OFFICIAL_BYTES = 32 * 1024 * 1024;
const MAX_APPLICATION_FORM_BYTES = 16 * 1024 * 1024;

function t(key) { return (STRINGS[state.locale] || STRINGS.zh)[key] || key; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c])); }
function aiOperatorStepsHtml(plan){
  if(!plan)return "";
  const executed=new Set(state.aiOperatorExecution?.host_executed_tools||[]);
  const delegated=new Set(state.aiOperatorExecution?.event_driven_pipeline_tools||[]);
  const userGates=new Set(state.aiOperatorExecution?.pending_user_gates||[]);
  return plan.steps.map(step=>{
    const stage=executed.has(step.tool)?t("aiOperatorExecuted"):userGates.has(step.tool)?t("aiOperatorUserGate"):delegated.has(step.tool)?t("aiOperatorDelegated"):t("aiOperatorPending");
    return `<li><strong>${escapeHtml(step.tool)}</strong><span>${escapeHtml(stage)} · ${escapeHtml(step.reason)}</span></li>`;
  }).join("");
}
function aiOperatorToolLabel(tool){
  const keys={
    "jobflow.search_official_jobs":"aiToolSearchOfficialJobs",
    "jobflow.start_guided_intake":"aiToolStartGuidedIntake",
    "jobflow.plan_resume_changes":"aiToolPlanResumeChanges",
    "jobflow.inspect_application_form":"aiToolInspectApplicationForm",
    "jobflow.start_user_present_assist":"aiToolStartUserPresentAssist"
  };
  return keys[tool]?t(keys[tool]):String(tool||"—");
}
function aiOperatorStatusLabel(status){
  if(status==="HOST_EXECUTED"||status==="HOST_PIPELINE_VERIFIED")return t("aiOperatorVerified");
  if(status==="HOST_REJECTED")return t("aiOperatorRejected");
  return t("aiOperatorSelected");
}
function renderAiOperatorActivity(){
  const activity=state.data?.ai_operator?.activity||{}, turns=Array.isArray(activity.recent_turns)?activity.recent_turns:[];
  const panel=document.querySelector("#aiOperatorActivity"), list=document.querySelector("#aiOperatorActivityList"), badge=document.querySelector("#aiOperatorActivityBadge");
  if(!panel||!list||!badge)return;
  panel.classList.toggle("hidden",!turns.length);
  badge.textContent=turns.length?t("aiOperatorActivityTurns").replace("{count}",String(turns.length)):t("aiOperatorActivityIdle");
  list.innerHTML=turns.slice(-4).reverse().map(item=>`<article class="ai-operator-activity-item"><div><strong>${escapeHtml(aiOperatorToolLabel(item.selected_tool))}</strong><small>${escapeHtml(item.application_id||item.decision_point||"")}</small></div><b>${escapeHtml(aiOperatorStatusLabel(item.status))}</b></article>`).join("");
}
function isReadonly() { return state.data?.status === "ONBOARDING_COMPLETE"; }
function disabledAttr(condition=true) { return condition ? " disabled" : ""; }
function arrangePrimaryWorkflow() {
  const finish=document.querySelector("#finish"), dashboard=document.querySelector("#pipelineDashboard");
  if(finish&&dashboard&&finish.nextElementSibling!==dashboard)finish.after(dashboard);
  const guided=document.querySelector("#guidedIntakePanel"), review=document.querySelector("#reviewPacketPanel"), browser=document.querySelector("#browserAssistPanel");
  const execution=document.querySelector(".execution-status-board"), advanced=document.querySelector("#advancedTools");
  if(guided&&review&&guided.nextElementSibling!==review)guided.after(review);
  if(review&&browser&&review.nextElementSibling!==browser)review.after(browser);
  if(execution&&advanced&&execution.nextElementSibling!==advanced)execution.after(advanced);
}
// Establish the user-facing order before any later listener or bootstrap code
// can fail. Materials and onboarding must remain above the application console.
arrangePrimaryWorkflow();
function isCompleteAiAnalysis(value) {
  const chunks=Number(value?.ai_chunks||0), input=Number(value?.ai_input_characters??-1), covered=Number(value?.ai_covered_characters??-2);
  return value?.analysis_mode==="AI_CORE_ENTITY_ANALYSIS" && value?.quality_contract===AI_QUALITY_CONTRACT && value?.ai_input_truncated===false && chunks>=1 && input>0 && covered===input;
}
function analysisCoverageLabel(value) {
  if(!isCompleteAiAnalysis(value)) return t("analysisCoverageIncomplete");
  const coverage=t("analysisCoverageComplete").replace("{chunks}",String(Number(value.ai_chunks)));
  if(value?.archive_scan_complete!==true) return coverage;
  const key=value.ai_selection_bounded?"archiveSelectionBounded":"archiveSelectionAll";
  const archive=t(key)
    .replace("{safe}",String(Number(value.safe_fragments_considered||0)))
    .replace("{selected}",String(Number(value.ai_selected_fragments||0)))
    .replace("{omitted}",String(Number(value.ai_omitted_fragments||0)));
  return `${coverage} · ${archive}`;
}
function showToast(message, error=false, duration=4200) {
  const el=document.querySelector("#toast");
  clearTimeout(toastTimer); el.textContent=message; el.className=error?"show error":"show";
  toastTimer=setTimeout(()=>el.className="",duration);
}

function companionVersionCurrent(result){return result?.protocol_version===2&&result?.extension_version===COMPANION_VERSION;}
function pairingIdentityMatches(record,result){
  if(!record||!result)return false;
  return record.kind==="guided"
    ?Boolean(result.intake_id&&result.intake_id===record.session?.intake_id)
    :Boolean(result.assist_id&&result.assist_id===record.session?.assist_id);
}
function guidedCompanionActive(){
  const session=state.guidedIntakeSession||state.data?.guided_intake;
  const terminal=new Set(["REVIEW_PACKET_READY","DEFERRED","FAILED"]);
  return Boolean(session&&(session.active||session.intake_id)&&!terminal.has(session.status));
}
function browserCompanionActive(){
  const info=state.data?.browser_assist||{}, session=state.browserAssistSession;
  const terminal=new Set(["CONFIRMED","SUBMISSION_UNKNOWN","AWAITING_APPROVAL","SUPPLEMENTAL_REVIEW_REQUIRED","APPLY_RESTART_REQUIRED","REVOKED","FAILED"]);
  return Boolean((session?.assist_id&&!terminal.has(session.status))||(info.active_assist_id&&!terminal.has(info.active_status)));
}
function companionModeConflict(kind){return kind==="guided"?browserCompanionActive():guidedCompanionActive();}
function storedCompanionPairingValid(record){
  const expiry=Number(record?.expires_epoch), pairing=record?.pairing, session=record?.session;
  if(!record||!pairing||!session||!["guided","assist"].includes(record.kind))return false;
  if(!Number.isFinite(expiry)||expiry<=Date.now()||pairing.protocol_version!==2||pairing.base_url!==location.origin)return false;
  const expectedPrefix=record.kind==="guided"?"/intake/":"/assist/";
  if(typeof pairing.assist_path!=="string"||!pairing.assist_path.startsWith(expectedPrefix)||!/^\/(?:assist|intake)\/[A-Za-z0-9_-]{40,}$/.test(pairing.assist_path))return false;
  return record.kind==="guided"
    ?typeof session.intake_id==="string"&&session.intake_id.length>0
    :typeof session.assist_id==="string"&&session.assist_id.length>0;
}
function persistCompanionPairing(record){
  state.companionPairing=record;
  try{sessionStorage.setItem(COMPANION_PAIRING_STORAGE,JSON.stringify(record));}catch(_error){/* Memory state remains sufficient. */}
}
function clearCompanionPairing(){
  state.companionPairing=null;state.companionTerminalHandled=null;
  state.companionPollFailures=0;state.companionConnectionNotice=null;
  if(state.companionStatusTimer){clearTimeout(state.companionStatusTimer);state.companionStatusTimer=null;}
  try{sessionStorage.removeItem(COMPANION_PAIRING_STORAGE);}catch(_error){/* No persistent fallback. */}
}
function restoreCompanionPairing(){
  try{
    const record=JSON.parse(sessionStorage.getItem(COMPANION_PAIRING_STORAGE)||"null");
    if(!storedCompanionPairingValid(record)){sessionStorage.removeItem(COMPANION_PAIRING_STORAGE);return null;}
    state.companionPairing=record;
    if(record.kind==="guided")state.guidedIntakeSession={...record.session,active:true};
    else state.browserAssistSession={...record.session};
    return record;
  }catch(_error){return null;}
}
function companionExternalMessage(message,timeout=1400){
  return new Promise((resolve,reject)=>{
    if(!globalThis.chrome?.runtime?.sendMessage){reject(makeUiError("BROWSER_COMPANION_UNAVAILABLE"));return;}
    let settled=false,pending=COMPANION_EXTENSION_IDS.length;
    const timer=setTimeout(()=>{if(!settled){settled=true;reject(makeUiError("BROWSER_COMPANION_TIMEOUT"));}},timeout);
    const unavailable=()=>{pending-=1;if(!settled&&pending===0){settled=true;clearTimeout(timer);reject(makeUiError("BROWSER_COMPANION_UNAVAILABLE"));}};
    for(const extensionId of COMPANION_EXTENSION_IDS){
      try{
        chrome.runtime.sendMessage(extensionId,message,response=>{
          if(settled)return;
          const runtimeError=chrome.runtime.lastError;
          if(runtimeError||!response||typeof response!=="object"){unavailable();return;}
          settled=true;clearTimeout(timer);resolve(response);
        });
      }catch(_error){unavailable();}
    }
  });
}
async function probeCompanionAvailability(render=true){
  let availability;
  try{
    const result=await companionExternalMessage({type:"JOBFLOW_PING"},1800);
    availability=companionVersionCurrent(result)
      ?{status:"AVAILABLE",extension_version:result.extension_version}
      :{status:"UPDATE_REQUIRED",extension_version:result?.extension_version||null};
  }catch(_error){availability={status:"UNAVAILABLE",extension_version:null};}
  state.companionAvailability=availability;
  if(render){renderCompanionConnectionState();renderWorkflowNow();}
  return availability;
}
function companionAvailabilityKey(){
  const status=state.companionAvailability?.status;
  if(status==="AVAILABLE")return "browserCompanionReady";
  if(status==="UPDATE_REQUIRED")return "browserCompanionUpdateRequired";
  if(status==="UNAVAILABLE")return "browserCompanionUnavailable";
  return "browserCompanionChecking";
}
async function requireCurrentCompanion(){
  const availability=await probeCompanionAvailability();
  if(availability.status==="AVAILABLE")return availability;
  if(availability.status==="UPDATE_REQUIRED")throw makeUiError("BROWSER_COMPANION_UPDATE_REQUIRED",{expected:COMPANION_VERSION,actual:availability.extension_version||"UNKNOWN"});
  throw makeUiError("BROWSER_COMPANION_UNAVAILABLE");
}
function pairingError(result){
  if(!companionVersionCurrent(result))return makeUiError("BROWSER_COMPANION_UPDATE_REQUIRED",{expected:COMPANION_VERSION,actual:result?.extension_version||"UNKNOWN"});
  return Object.assign(new Error(result?.message||result?.code||"BROWSER_COMPANION_UNAVAILABLE"),result||{});
}
function acceptCompanionPairResult(result){
  const record=state.companionPairing;
  if(!record)return false;
  if(!companionVersionCurrent(result))throw pairingError(result);
  if(!pairingIdentityMatches(record,result)){
    if(result?.status==="BLOCKED")throw pairingError(result);
    return false;
  }
  if(record.kind==="guided"){
    if(result.status!=="GUIDED_INTAKE_PAIRED")throw pairingError(result);
    if(state.guidedIntakePairTimer)clearTimeout(state.guidedIntakePairTimer);
    state.guidedIntakeSession={...record.session,...result,status:result.capture_status||result.status,active:true,paired:true};
    record.session={...state.guidedIntakeSession};record.paired=true;persistCompanionPairing(record);
    renderGuidedIntake();showToast(t("guidedPaired"),false,9000);
  }else{
    if(result.status!=="BROWSER_COMPANION_PAIRED")throw pairingError(result);
    if(state.browserAssistPairTimer)clearTimeout(state.browserAssistPairTimer);
    state.browserAssistSession={...record.session,...result,paired:true};
    record.session={...state.browserAssistSession};record.paired=true;persistCompanionPairing(record);
    renderBrowserAssist(state.data?.dashboard?.recent_applications||[]);showToast(t("browserAssistPaired"),false,9000);
  }
  state.companionPollFailures=0;state.companionConnectionNotice=null;
  startCompanionStatusPolling();
  return true;
}
async function beginCompanionPairing(record){
  persistCompanionPairing(record);
  state.companionConnectionNotice=record.paired?null:"companionClickToPair";
  startCompanionStatusPolling();
  renderGuidedIntake();renderBrowserAssist(state.data?.dashboard?.recent_applications||[]);
  if(record.paired){showToast(t("companionStatusTemporary"),false,12000);return true;}
  return await attemptAutomaticCompanionPairing(record);
}
async function attemptAutomaticCompanionPairing(record){
  if(!record||state.companionPairing!==record||record.paired)return false;
  try{
    const result=await companionExternalMessage({type:"JOBFLOW_PAIR",pairing:record.pairing},4200);
    if(state.companionPairing!==record)return false;
    return acceptCompanionPairResult(result);
  }catch(error){
    if(state.companionPairing!==record)return false;
    state.companionConnectionNotice=error?.code==="BROWSER_COMPANION_UPDATE_REQUIRED"
      ?"guidedExtensionOutdated":"companionClickToPair";
    renderCompanionConnectionState();
    showToast(t(state.companionConnectionNotice),error?.code==="BROWSER_COMPANION_UPDATE_REQUIRED",12000);
    return false;
  }
}
function postPendingCompanionPairing(){
  const record=state.companionPairing;if(!record)return;
  // Fallback for browsers that did not complete the normal signed external
  // pairing. pair.js is injected only after the user clicks the extension.
  window.postMessage({type:"JOBFLOW_PAIR_REQUEST",protocol_version:2,pairing:record.pairing},location.origin);
}

async function releaseGuidedCompanionBinding(record){
  if(!record||record.kind!=="guided")return false;
  try{
    const result=await companionExternalMessage({
      type:"JOBFLOW_CANCEL_GUIDED",binding:record.pairing,intake_id:record.session?.intake_id
    },2200);
    if(!companionVersionCurrent(result))throw pairingError(result);
    return result.status==="GUIDED_INTAKE_COMPANION_CLEARED";
  }catch(_error){return false;}
}

function makeUiError(code, details={}) { const error=new Error(code); error.code=code; error.details=details; return error; }
function isAiReady(engine){
  const capability=engine?.structured_capability_status;
  return engine?.status==="READY" && (!capability||["VERIFIED","VALIDATED_ON_USE"].includes(capability));
}

function assertUiCompatibility(payload) {
  if(payload?.build?.product!=="JobFlow" || payload?.build?.ui_protocol!==UI_PROTOCOL_VERSION) {
    state.serviceCompatible=false;
    throw makeUiError("SERVICE_RESTART_REQUIRED");
  }
  state.serviceCompatible=true;
}

const LOCAL_ERROR_KEYS = {
  SERVICE_RESTART_REQUIRED:"serviceRestartRequired", LOCAL_RESPONSE_INVALID:"invalidLocalResponse",
  BROWSER_COMPANION_UPDATE_REQUIRED:"guidedExtensionOutdated", BROWSER_COMPANION_UNAVAILABLE:"guidedExtensionMissing", BROWSER_COMPANION_TIMEOUT:"guidedExtensionMissing",
  COMPANION_BINDING_MISSING:"guidedBindingMissing", COMPANION_BINDING_INVALID:"guidedBindingMissing", COMPANION_BINDING_PROOF_INVALID:"guidedBindingMissing",
  BROWSER_COMPANION_BINDING_MISSING:"guidedBindingMissing", BROWSER_COMPANION_BINDING_INVALID:"guidedBindingMissing", BROWSER_COMPANION_BINDING_MISMATCH:"guidedBindingMissing", BROWSER_COMPANION_BINDING_REQUEST_INVALID:"guidedBindingMissing",
  BROWSER_COMPANION_SESSION_ACTIVE:"companionSessionActive",
  AI_OPERATOR_REQUIRED:"aiOperatorRequired", AI_OPERATOR_RESPONSE_INVALID:"localRequestFailed", AI_OPERATOR_REQUIRED_TOOL_MISSING:"localRequestFailed", AI_OPERATOR_JOB_URL_INVALID:"guidedUrlRequired",
  AI_OPERATOR_BOUNDARY_REJECTED:"localRequestFailed", AI_OPERATOR_TOOL_FORBIDDEN:"localRequestFailed",
  BROWSER_ASSIST_RESTART_REQUIRED:"browserAssistRestartRequired", COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED:"browserAssistManualRestart", COMPANION_APPLY_RESTART_REQUIRED:"browserAssistApplyRestart", BROWSER_ASSIST_SUBMISSION_UNKNOWN:"browserAssistReloadUnknown",
  PENDING_LIMIT_INVALID:"pendingLimitInvalid", PENDING_LIMIT_BELOW_ACTIVE:"pendingLimitBelowActive",
  REVIEW_PACKET_NOT_FOUND:"reviewPacketUnavailable", REVIEW_PACKET_SIZE_INVALID:"reviewPacketUnavailable",
  REVIEW_PACKET_INVALID:"reviewPacketUnavailable", REVIEW_PACKET_BINDING_INVALID:"reviewPacketUnavailable",
  REVIEW_PACKET_HASH_INVALID:"reviewPacketUnavailable", SECURE_REFERENCE_MISSING:"reviewPacketUnavailable",
  SECURE_REFERENCE_REVOKED:"reviewPacketUnavailable", SECURE_REFERENCE_HASH_MISMATCH:"reviewPacketUnavailable",
  REVIEW_PACKET_STALE:"reviewPacketStale", REVIEW_DECISION_INVALID:"chooseDecision",
  EXPLICIT_CONFIRMATION_REQUIRED:"confirmDecisionFirst", APPLICATION_NOT_AWAITING_APPROVAL:"reviewDecisionUnavailable",
  APPLICATION_NOT_REVISABLE:"reviewDecisionUnavailable", APPLICATION_BINDING_MISSING:"reviewPacketUnavailable",
  APPLICATION_FIELDS_UNRESOLVED:"packetFieldsRequired", APPLICATION_FIELD_RESOLUTIONS_INCOMPLETE:"packetFieldInvalid",
  APPLICATION_FIELD_RESOLUTION_INVALID:"packetFieldInvalid", APPLICATION_FIELD_REFERENCE_INVALID:"packetFieldInvalid",
  APPLICATION_FIELD_VALUE_REQUIRED:"packetFieldInvalid", APPLICATION_FIELD_VALUE_INVALID:"packetFieldInvalid",
  APPLICATION_FIELD_OPTION_INVALID:"packetFieldInvalid", APPLICATION_FIELD_BINDING_INVALID:"packetFieldInvalid",
  APPLICATION_ANSWER_BUNDLE_TOO_LARGE:"packetFieldInvalid", APPLICATION_FIELD_RESOLUTION_ROLLBACK_FAILED:"privateWriteRepair",
  PROFILE_REVIEW_REQUIRED:"profileReviewRequired", ONBOARDING_ANSWERS_INCOMPLETE:"answersIncomplete",
  ONBOARDING_HARD_CONDITIONS_UNRESOLVED:"hardConditionsUnresolved", SOURCE_PREVIEW_PENDING:"sourcePreviewPending",
  SOURCE_AI_REANALYSIS_REQUIRED:"sourceAiReanalysisRequired", CLAIM_REVIEW_INCOMPLETE:"claimReviewIncomplete",
  CONFLICT_REVIEW_INCOMPLETE:"conflictReviewIncomplete", ONBOARDING_CONFIRMATION_REQUIRED:"onboardingConfirmationRequired",
  ONBOARDING_ALREADY_COMPLETE:"onboardingAlreadyComplete", ONBOARDING_REVISION_REQUIRED:"onboardingRevisionRequired",
  ONBOARDING_ANSWER_REQUIRED:"invalidAnswer", ONBOARDING_ANSWER_INVALID:"invalidAnswer", ONBOARDING_ANSWERS_INVALID:"invalidAnswer",
  CLAIM_EDIT_INVALID:"invalidClaim", CLAIM_REVIEW_INVALID:"invalidClaim", CLAIM_TRANSFORM_INVALID:"invalidClaim",
  CONFLICT_REVIEW_INVALID:"conflictReviewIncomplete", ONBOARDING_SOURCE_TYPE_INVALID:"sourceTypeUnsupported",
  ONBOARDING_SOURCE_EXTENSION_INVALID:"sourceTypeUnsupported", CHATGPT_EXPORT_FORMAT_INVALID:"sourceTypeUnsupported",
  ONBOARDING_SOURCE_SIZE_INVALID:"sourceSizeInvalid", REQUEST_SIZE_INVALID:"sourceSizeInvalid",
  ONBOARDING_DOCUMENT_TOO_LARGE:"sourceSizeInvalid", ONBOARDING_DOCUMENT_COMPRESSION_UNSAFE:"sourceSizeInvalid",
  ONBOARDING_DOCUMENT_AMBIGUOUS:"sourceTypeUnsupported", ONBOARDING_DOCUMENT_ENCRYPTED:"sourceTypeUnsupported",
  ONBOARDING_JSON_COMPLEXITY_LIMIT:"sourceSizeInvalid", CHATGPT_EXPORT_CONVERSATION_TOO_COMPLEX:"sourceSizeInvalid",
  PDF_PAGE_LIMIT_EXCEEDED:"sourceSizeInvalid", PDF_TEXT_LIMIT_EXCEEDED:"sourceSizeInvalid",
  REQUEST_CONTENT_TYPE_INVALID:"localRequestFailed", REQUEST_TRANSFER_ENCODING_FORBIDDEN:"localRequestFailed",
  ONBOARDING_UPLOAD_INTERRUPTED:"uploadInterrupted",
  SOURCE_PRIVATE_DELETE_FAILED:"privateDeleteRetry", SOURCE_PREVIEW_PRIVATE_DELETE_FAILED:"privateDeleteRetry",
  SOURCE_DELETE_ROLLBACK_FAILED:"privateDeleteRepair", SOURCE_PREVIEW_DISCARD_ROLLBACK_FAILED:"privateDeleteRepair",
  PRIVATE_DELETE_STATE_UNKNOWN:"privateDeleteRepair", PRIVATE_DELETE_ROLLBACK_FAILED:"privateDeleteRepair",
  SECURE_CIPHERTEXT_UNAVAILABLE:"privateWriteRepair", SECURE_CIPHERTEXT_HASH_MISMATCH:"privateWriteRepair",
  ONBOARDING_ANSWER_SAVE_FAILED:"privateWriteRetry", ONBOARDING_COMPLETION_WRITE_FAILED:"privateWriteRetry",
  ONBOARDING_REVISION_WRITE_FAILED:"privateWriteRetry", ONBOARDING_STATE_INDEX_WRITE_FAILED:"privateWriteRetry",
  ONBOARDING_INITIAL_INDEX_WRITE_FAILED:"privateWriteRetry",
  ONBOARDING_ANSWER_SAVE_ROLLBACK_FAILED:"privateWriteRepair", ONBOARDING_COMPLETION_ROLLBACK_FAILED:"privateWriteRepair",
  ONBOARDING_REVISION_ROLLBACK_FAILED:"privateWriteRepair", ONBOARDING_STATE_INDEX_ROLLBACK_FAILED:"privateWriteRepair",
  ONBOARDING_PRIVATE_ROLLBACK_FAILED:"privateWriteRepair", ONBOARDING_INITIAL_STATE_ROLLBACK_FAILED:"privateWriteRepair",
  SOURCE_PREVIEW_SAVE_FAILED:"privateWriteRetry", SOURCE_IMPORT_SAVE_FAILED:"privateWriteRetry",
  SOURCE_PREVIEW_ROLLBACK_FAILED:"privateWriteRepair", SOURCE_IMPORT_ROLLBACK_FAILED:"privateWriteRepair",
  PRIVATE_STAGING_CLEANUP_FAILED:"privateWriteRepair", PRIVATE_STAGING_REPARSE_FORBIDDEN:"privateWriteRepair",
  OFFICIAL_SNAPSHOT_SIZE_INVALID:"sourceSizeInvalid", OFFICIAL_SNAPSHOT_FORMAT_UNSUPPORTED:"sourceTypeUnsupported",
  OFFICIAL_SNAPSHOT_ENCODING_INVALID:"officialSnapshotInvalid", OFFICIAL_PAGE_SNAPSHOT_INVALID:"officialSnapshotInvalid",
  OFFICIAL_PAGE_SOURCE_MISMATCH:"officialSnapshotInvalid", COMPANY_DOMAIN_MISMATCH:"officialSnapshotInvalid",
  OFFICIAL_CAREERS_PATH_NOT_PROVEN:"officialSnapshotInvalid", OFFICIAL_SNAPSHOT_HTML_INVALID:"officialSnapshotInvalid",
  OFFICIAL_SNAPSHOT_COMPLEXITY_LIMIT:"officialSnapshotInvalid", OFFICIAL_URL_SENSITIVE_QUERY:"officialSnapshotInvalid",
  EXTERNAL_CLAIM_CONFIRMATION_REQUIRED:"externalClaimConfirmFirst",
  EXTERNAL_CLAIM_REVIEW_HASH_INVALID:"localRequestFailed", EXTERNAL_CLAIM_USES_INVALID:"localRequestFailed",
  EXTERNAL_CLAIM_REVIEW_STALE:"readinessNeedsClaimApproval", ONBOARDING_INCOMPLETE:"readinessNeedsOnboarding",
  MASTER_RESUME_MISSING:"readinessNeedsMaster", CONFIRMED_CLAIMS_MISSING:"readinessNeedsClaims",
  CANDIDATE_PROFILE_MISSING:"readinessNeedsOnboarding",
  TAILORING_PROPOSAL_EMPTY:"tailoringProposalEmpty", TAILORING_PROPOSAL_STALE:"tailoringProposalStale",
  TAILORING_PROPOSAL_HASH_INVALID:"tailoringProposalStale", TAILORING_SELECTION_INVALID:"tailoringSelectionInvalid",
  TAILORING_SELECTION_EMPTY:"tailoringSelectOne", TAILORING_CONFIRMATION_REQUIRED:"tailoringConfirmFirst",
  EDITABLE_MASTER_DOCX_MISSING:"readinessNeedsEditableMaster",
  APPLICATION_BUNDLE_FILES_INVALID:"applicationBundleInvalid", APPLICATION_BUNDLE_FILE_INVALID:"applicationBundleInvalid",
  APPLICATION_BUNDLE_SIZE_INVALID:"applicationBundleInvalid", APPLICATION_BUNDLE_PROTOCOL_INVALID:"applicationBundleInvalid",
  APPLICATION_RESEARCH_EXCERPT_INVALID:"applicationBundleInvalid", APPLICATION_RESEARCH_EXCERPT_MISSING:"applicationBundleInvalid",
  APPLICATION_GUEST_STATUS_INVALID:"applicationBundleInvalid", APPLICATION_ONBOARDING_APPROVAL_REQUIRED:"offlineApplicationNeedsReadiness",
  APPLICATION_ONBOARDING_BINDING_MISMATCH:"offlineApplicationNeedsReadiness", APPLICATION_PRIVATE_REFERENCE_INVALID:"offlineApplicationNeedsReadiness",
  APPLICATION_PRIVATE_REFERENCE_HASH_INVALID:"offlineApplicationNeedsReadiness", APPLICATION_MASTER_BINDING_MISMATCH:"offlineApplicationNeedsReadiness",
  APPLICATION_CLAIM_SOURCE_CHANGED:"offlineApplicationNeedsReadiness", TAILORING_MANIFEST_STALE:"readinessNeedsTemplate",
  APPLICATION_PREPARATION_ROLLBACK_FAILED:"privateWriteRepair", OFFLINE_RESEARCH_METADATA_REQUIRED:"applicationBundleInvalid",
  CONTINUOUS_EVIDENCE_BUNDLE_TOO_LARGE:"deferredBundleTooLarge", CONTINUOUS_EVIDENCE_FILES_INVALID:"applicationBundleInvalid",
  CONTINUOUS_EVIDENCE_BUNDLE_INVALID:"applicationBundleInvalid", CONTINUOUS_EVIDENCE_BUNDLE_CHANGED:"applicationBundleInvalid",
  CONTINUOUS_EVIDENCE_REFERENCE_INVALID:"privateWriteRepair", CONTINUOUS_EVIDENCE_CLEANUP_FAILED:"privateWriteRepair",
  DEFERRED_EVIDENCE_ROLLBACK_FAILED:"privateWriteRepair",
  HTTPS_REQUIRED:"applicationBundleInvalid", ATS_PROVIDER_UNKNOWN:"applicationBundleInvalid",
  UNSAFE_ROUTE_HOP:"applicationBundleInvalid", UNAPPROVED_APPLICATION_HOST:"applicationBundleInvalid",
  SOURCE_ROUTE_INVALID:"applicationBundleInvalid", TAILORING_RELEVANCE_INSUFFICIENT:"applicationBundleInvalid",
  APPLICATION_READINESS_INCOMPLETE:"guidedReadinessRequired", GUIDED_INTAKE_COMPANY_URL_REQUIRED:"guidedUrlRequired",
  GUIDED_INTAKE_WRONG_JOB_PAGE:"guidedWrongJobPage", GUIDED_INTAKE_FORM_MISSING:"guidedFormMissing",
  GUIDED_INTAKE_JOB_TITLE_MISSING:"guidedJobTitleMissing", GUIDED_INTAKE_PAGE_INVALID:"guidedFailed",
  GUIDED_INTAKE_JOB_PAGE_MISSING:"guidedFailed", GUIDED_INTAKE_STAGE_INVALID:"guidedFailed",
  GUIDED_INTAKE_TOKEN_INVALID:"guidedLeaseInvalid", GUIDED_INTAKE_NOT_FOUND:"guidedLeaseInvalid",
  GUIDED_INTAKE_EXPIRED:"guidedLeaseInvalid", ROUTE_URL_SENSITIVE_QUERY:"guidedUrlRequired",
  GUIDED_INTAKE_ID_INVALID:"guidedLeaseInvalid", GUIDED_INTAKE_CANCEL_UNAVAILABLE:"guidedCancelUnavailable",
  BROWSER_COMPANION_ORIGIN_FORBIDDEN:"guidedExtensionMissing",
  APPLICATION_NOT_APPROVED:"browserAssistNotApproved", BROWSER_ASSIST_ROUTE_UNSUPPORTED:"browserAssistRouteUnsupported",
  BROWSER_ASSIST_ROUTE_UNSAFE:"browserAssistRouteUnsupported", BROWSER_ASSIST_ALREADY_ACTIVE:"browserAssistActive",
  BROWSER_ASSIST_TOKEN_INVALID:"browserAssistLeaseInvalid", BROWSER_ASSIST_STATE_INVALID:"browserAssistLeaseInvalid",
  FORM_ROUTE_BINDING_CHANGED:"browserAssistWrongPage", SITE_CHANGED:"browserAssistWrongPage",
  BROWSER_SECURITY_STOP:"browserAssistSafetyStop", BROWSER_CONTROL_TYPE_UNSUPPORTED:"browserAssistUnsupportedControl",
  APPROVED_UPLOAD_CONTROL_MISSING:"browserAssistUploadMismatch", FINAL_SUBMIT_FORBIDDEN:"browserAssistFinalLocked"
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

const WORKFLOW_PANEL_TARGETS=new Set(["sources","questionnaire","review","finish"]);
const WORKFLOW_ACTIVITY_TARGETS={
  importing:"sources",reprocessing:"sources",reprocessingAll:"sources",committingSource:"sources",deletingSource:"sources",discardingSource:"sources",
  savingAnswers:"questionnaire",savingReview:"review",includingAll:"review",transformingClaims:"review",completingOnboarding:"finish",
  detectingAgent:"aiConnectionPanel",detectingLocalModel:"aiConnectionPanel",preparingGuidedApplication:"guidedIntakePanel",startingGuidedIntake:"guidedIntakePanel",
  loadingReviewPacket:"reviewPacketPanel",startingBrowserAssist:"browserAssistPanel",resolvingSubmission:"browserAssistPanel"
};
function workflowActivityDetail(activity){
  const elapsed=Math.max(0,Math.floor((Date.now()-activity.started)/1000));
  if(activity.phase==="uploading"){
    const total=Math.max(1,Number(activity.totalBytes)||1),loaded=Math.max(0,Number(activity.loadedBytes)||0);
    return t("workflowUploadDetail").replace("{percent}",String(Math.max(0,Math.min(100,Math.round(loaded/total*100)))));
  }
  const estimate=Math.max(0,Number(activity.estimatedSeconds)||0),remaining=estimate&&elapsed<estimate?Math.max(1,Math.ceil(estimate-elapsed)):null;
  return t(remaining===null?"workflowWorkingDetail":"workflowWorkingEstimate").replace("{elapsed}",String(elapsed)).replace("{remaining}",String(remaining||0));
}
function workflowNowModel(){
  const activity=state.activities[state.activities.length-1];
  if(activity)return {tone:"working",title:t(activity.key),detail:workflowActivityDetail(activity),target:WORKFLOW_ACTIVITY_TARGETS[activity.key]||""};
  const guided=state.guidedIntakeSession||state.data?.guided_intake||{};
  if(["FORM_CAPTURE_FAILED","FAILED"].includes(guided.status))return {tone:"blocked",title:t("guidedStoppedTitle"),detail:guidedFailureMessage(guided),target:"guidedIntakePanel",action:t("workflowFailureAction")};
  const browserStatus=state.browserAssistSession?.status||state.data?.browser_assist?.active_status;
  if(browserStatus&&!new Set(["CONFIRMED","SUBMISSION_UNKNOWN","AWAITING_APPROVAL","SUPPLEMENTAL_REVIEW_REQUIRED","APPLY_RESTART_REQUIRED","REVOKED","FAILED"]).has(browserStatus)){
    return {tone:"working",title:t("workflowBrowserTitle"),detail:t("workflowBrowserDetail"),target:"browserAssistPanel",action:t("workflowBrowserAction")};
  }
  if(browserStatus&&!new Set(["CONFIRMED","REVOKED"]).has(browserStatus)){
    return {tone:"blocked",title:browserAssistResultMessage(browserStatus),detail:document.querySelector("#browserAssistMessage")?.textContent||t("workflowBrowserDetail"),target:"browserAssistPanel",action:t("workflowFailureAction")};
  }
  if(guided?.status&&!["IDLE","REVIEW_PACKET_READY","DEFERRED","FAILED"].includes(guided.status)){
    return {tone:["FORM_CAPTURE_FAILED"].includes(guided.status)?"blocked":"working",title:guidedIntakeMessage(guided.status),detail:document.querySelector("#guidedIntakeMessage")?.textContent||t("workflowReadyDetail"),target:"guidedIntakePanel",action:t("workflowGuidedAction")};
  }
  if(state.reviewPacket){return {tone:"ready",title:t("workflowReviewPacketTitle"),detail:t("workflowReviewPacketDetail"),target:"reviewPacketPanel",action:t("workflowReviewPacketAction")};}
  const data=state.data;
  if(!data)return {tone:"working",title:t("workflowLoadingTitle"),detail:t("workflowLoadingDetail"),target:""};
  if(!isAiReady(data.ai_engine)&&data.demo_mode!==true)return {tone:"blocked",title:t("workflowConnectAiTitle"),detail:t("workflowConnectAiDetail"),target:"aiConnectionPanel",action:t("workflowConnectAiAction")};
  if(data.status!=="ONBOARDING_COMPLETE"){
    const sources=Array.isArray(data.sources)?data.sources:[],pending=Array.isArray(data.pending_sources)?data.pending_sources:[];
    if(!sources.length||pending.length||sources.some(item=>item.analysis_mode!=="AI_CORE_ENTITY_ANALYSIS"))return {tone:"neutral",title:t("workflowSourcesTitle"),detail:t("workflowSourcesDetail"),target:"sources",action:t("workflowSourcesAction")};
    if(Number(data.completion?.remaining||0)>0)return {tone:"neutral",title:t("workflowQuestionsTitle"),detail:t("workflowQuestionsDetail"),target:"questionnaire",action:t("workflowQuestionsAction")};
    const claims=Array.isArray(data.claims)?data.claims:[],conflicts=Array.isArray(data.conflicts)?data.conflicts:[];
    if(data.profile_review!=="CONFIRMED"||claims.some(item=>item.decision==="PENDING")||conflicts.some(item=>!item.resolution))return {tone:"neutral",title:t("workflowReviewTitle"),detail:t("workflowReviewDetail"),target:"review",action:t("workflowReviewAction")};
    return {tone:"ready",title:t("workflowFinishTitle"),detail:t("workflowFinishDetail"),target:"finish",action:t("workflowFinishAction")};
  }
  if(data.application_readiness?.status!=="READY_FOR_OFFLINE_APPLICATION_PREPARATION")return {tone:"blocked",title:t("workflowReadinessTitle"),detail:t("workflowReadinessDetail"),target:"pipelineDashboard",action:t("workflowReadinessAction")};
  if(["UNAVAILABLE","UPDATE_REQUIRED"].includes(state.companionAvailability?.status))return {
    tone:"blocked",title:t(companionAvailabilityKey()),detail:t("browserCompanionHelp"),target:"guidedIntakePanel",action:t("workflowFailureAction")
  };
  return {tone:"ready",title:t("workflowReadyTitle"),detail:t("workflowReadyDetail"),target:"guidedIntakePanel",action:t("workflowReadyAction")};
}
function renderWorkflowNow(){
  const root=document.querySelector("#workflowNow"),title=document.querySelector("#workflowNowTitle"),detail=document.querySelector("#workflowNowDetail"),action=document.querySelector("#workflowNowAction");
  if(!root||!title||!detail||!action)return;
  const model=workflowNowModel();root.dataset.tone=model.tone||"neutral";title.textContent=model.title;detail.textContent=model.detail;
  action.dataset.target=model.target||"";action.textContent=model.action||t("workflowContinue");action.classList.toggle("hidden",!model.target);
}
function focusWorkflowNow(){
  const target=document.querySelector("#workflowNowAction")?.dataset.target;if(!target)return;
  if(WORKFLOW_PANEL_TARGETS.has(target)){navigate(target);return;}
  const element=document.querySelector(`#${CSS.escape(target)}`);if(!element)return;
  if(target==="aiConnectionPanel"){element.classList.remove("hidden");document.querySelector("#aiConnectButton")?.setAttribute("aria-expanded","true");}
  element.scrollIntoView({behavior:"smooth",block:"start"});
}

function renderActivity() {
  const indicator=document.querySelector("#activityIndicator"), main=document.querySelector("main");
  const guided=state.guidedIntakeSession||state.data?.guided_intake||{};
  const terminalGuided=["FORM_CAPTURE_FAILED","FAILED"].includes(guided.status)
    ?{terminal:true,key:"guidedStoppedTitle",detail:guidedFailureMessage(guided)}:null;
  const activity=state.activities[state.activities.length-1]||terminalGuided;
  renderWorkflowNow();
  if(!activity){indicator.classList.add("hidden");document.body.classList.remove("is-busy");main?.removeAttribute("aria-busy");return;}
  const progressBar=document.querySelector("#activityProgress");
  indicator.classList.remove("hidden");
  indicator.classList.toggle("failed",Boolean(activity.terminal));
  if(activity.terminal){
    document.body.classList.remove("is-busy");main?.removeAttribute("aria-busy");
    document.querySelector("#activityTitle").textContent=t(activity.key);
    document.querySelector("#activityStage").textContent=activity.detail;
    document.querySelector("#activityElapsed").textContent=t("guidedStoppedHint");
    progressBar.classList.remove("indeterminate");progressBar.style.width="100%";
    return;
  }
  indicator.classList.remove("failed");document.body.classList.add("is-busy");main?.setAttribute("aria-busy","true");
  const seconds=Math.max(0,Math.floor((Date.now()-activity.started)/1000));
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
function learnedActivityEstimate(key){
  const samples=(state.activityDurations[key]||[]).slice().sort((a,b)=>a-b);
  if(!samples.length)return null;
  const middle=Math.floor(samples.length/2);
  const median=samples.length%2?samples[middle]:(samples[middle-1]+samples[middle])/2;
  return Math.max(2,Math.ceil(median*1.15));
}
function beginActivity(key,options={}){const id=++activitySequence;state.activities.push({id,key,started:Date.now(),estimatedSeconds:options.estimatedSeconds??learnedActivityEstimate(key)??ACTIVITY_ESTIMATES[key]??0,total:options.total??0,completed:options.completed??0,currentEstimate:options.currentEstimate??0});if(!activityTimer)activityTimer=setInterval(renderActivity,1000);renderActivity();return id;}
function updateActivity(id,patch){const activity=state.activities.find(item=>item.id===id);if(activity)Object.assign(activity,patch);renderActivity();}
function endActivity(id,recordDuration=true){const activity=state.activities.find(item=>item.id===id);if(recordDuration&&activity&&activity.phase!=="uploading"){const seconds=Math.max(1,Math.ceil((Date.now()-activity.started)/1000));const samples=state.activityDurations[activity.key]||[];samples.push(seconds);state.activityDurations[activity.key]=samples.slice(-5);}state.activities=state.activities.filter(item=>item.id!==id);if(!state.activities.length&&activityTimer){clearInterval(activityTimer);activityTimer=null;}renderActivity();}
async function withActivity(key,operation,options={}){const id=beginActivity(key,options);let succeeded=false;try{const result=await operation(id);succeeded=true;return result;}finally{endActivity(id,succeeded);}}

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

function fileExtension(file){
  return (String(file?.name||"").match(/\.[^.]+$/)||[""])[0].toLowerCase();
}

function isHttpsUrl(value){
  try{return new URL(String(value)).protocol==="https:";}catch(_){return false;}
}

function jobUrlFromOperatorInputs(){
  const explicit=document.querySelector("#guidedOfficialUrl")?.value.trim()||"";
  if(isHttpsUrl(explicit))return explicit;
  const command=document.querySelector("#aiOperatorCommand")?.value||"";
  const match=command.match(/https:\/\/[^\s<>"']+/i);
  return match&&isHttpsUrl(match[0].replace(/[),.;!?\]}，。！？]+$/u,""))?match[0].replace(/[),.;!?\]}，。！？]+$/u,""):"";
}

function buildOfflineApplicationBundle(metadata,parts){
  const manifest={
    schema_version:1,
    metadata,
    files:parts.map(item=>({key:item.key,extension:fileExtension(item.file),size:item.file.size}))
  };
  const encoded=new TextEncoder().encode(JSON.stringify(manifest));
  const header=new ArrayBuffer(4);
  new DataView(header).setUint32(0,encoded.byteLength,false);
  return new Blob([header,encoded,...parts.map(item=>item.file)],{type:"application/octet-stream"});
}

function aiConnectionErrorMessage(error) {
  if(error?.code==="SERVICE_RESTART_REQUIRED")return localizedErrorMessage(error);
  if(error?.code==="AI_STRUCTURED_CAPABILITY_FAILED")return t("aiCapabilityFailed");
  const byCode = {
    AI_WINDOWS_HERMES_AUTH_REQUIRED: "aiWindowsHermesAuthRequired",
    AI_WINDOWS_HERMES_CONNECTION_FAILED: "aiWindowsHermesConnectionFailed",
    AI_WINDOWS_HERMES_PROXY_FAILED: "aiWindowsHermesProxyFailed",
    AI_WSL_HERMES_AUTH_REQUIRED: "aiWslHermesAuthRequired",
    AI_WSL_PROXY_START_FAILED: "aiWslProxyStartFailed",
    AI_WSL_LOCAL_BRIDGE_MISSING: "aiWslBridgeMissing",
    AI_AGENT_UNAVAILABLE: "aiAgentConnectionFailed",
    AI_AGENT_HANDSHAKE_FAILED: "aiAgentHandshakeFailed",
    AI_AGENT_MODEL_UNAVAILABLE: "aiAgentModelUnavailable",
    AI_AGENT_MODEL_INVALID: "aiAgentModelUnavailable",
    AI_AGENT_PROVIDER_INVALID: "aiAgentModelUnavailable",
    AI_AGENT_TOOL_AUDIT_MISSING: "aiAgentSafetyRejected",
    AI_AGENT_TOOL_CALL_BLOCKED: "aiAgentSafetyRejected"
  };
  return t(byCode[error?.code] || "aiConnectionFailed");
}

function sourceAnalysisErrorMessage(error, sourceType="") {
  if(error?.code==="SERVICE_RESTART_REQUIRED")return localizedErrorMessage(error);
  const quality=error?.details?.document_quality||{};
  const qualityCodes=Array.isArray(quality.reason_codes)?quality.reason_codes:[];
  if(error?.code==="ONBOARDING_DOCUMENT_QUALITY_FAILED"||quality.status==="FAIL")return t(qualityCodes.includes("OCR_REQUIRED")?"documentOcrRequired":"documentExtractionRisk");
  if(error?.code==="AI_STRUCTURED_CAPABILITY_FAILED")return t("aiCapabilityFailed");
  if (["AI_RESPONSE_INVALID", "AI_RESPONSE_REPAIR_FAILED"].includes(error?.code)) {
    const category=error?.details?.failure_category||"";
    if(["RESPONSE_FORMAT","STRUCTURED_OUTPUT_CONTRACT"].includes(category))return t("aiFormatFailure");
    if(["EXPERIENCE_CLASSIFICATION","ENTITY_IDENTITY","ENTITY_RELATION","DUPLICATE_ENTITY","CATEGORY_CONTRACT"].includes(category))return t("aiClassificationFailure");
    if(category==="UNSUPPORTED_NUMBER"){
      const details=error?.details||{};
      const candidate=Number(details.candidate_index), count=Number(details.unsupported_number_count);
      const start=Number(details.expanded_line_start||details.cited_line_start);
      const end=Number(details.expanded_line_end||details.cited_line_end);
      if([candidate,count,start,end].every(Number.isInteger)){
        return t("aiNumberFailureDetailed")
          .replace("{candidate}",candidate).replace("{count}",count)
          .replace("{start}",start).replace("{end}",end);
      }
      return t("aiNumberFailure");
    }
    if(["CITED_LINE_GROUNDING","PROVENANCE_LINES","ENTITY_DATE"].includes(category))return t("aiGroundingFailure");
    if(category==="STATEMENT_FRAGMENT")return t("aiFragmentFailure");
    return t(sourceType.startsWith("chatgpt_export")?"aiExportRepairFailed":"aiRepairFailed");
  }
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
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder=t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll("[data-locale]").forEach(el => {
    const active=el.dataset.locale === state.locale;
    el.classList.toggle("active",active);
    el.setAttribute("aria-pressed",String(active));
  });
  document.querySelectorAll("[data-step-label]").forEach(el => {
    const number=el.querySelector("b")?.textContent||"";
    el.setAttribute("aria-label",`${number} ${t(el.dataset.stepLabel)}`.trim());
  });
  document.querySelector("#officialSnapshotFile").setAttribute("aria-label",t("officialSnapshotLabel"));
  if(state.lastBlockingError&&!document.querySelector("#blockingNotice").classList.contains("hidden")){
    document.querySelector("#blockingNoticeTitle").textContent=t("attentionRequired");
    document.querySelector("#blockingNoticeBody").textContent=localizedErrorMessage(state.lastBlockingError);
  }
  if(state.data)renderDashboard();
  if(state.reviewPacket)renderReviewPacket();
  if(state.tailoringProposal)renderTailoringProposal(true);
  renderActivity();
}

function queueStatusLabel(status){
  const keys={APPROVED:"statusApproved",SUBMITTED:"statusSubmitted",SUBMISSION_UNKNOWN:"statusSubmissionUnknown",CONFIRMED:"statusConfirmed",CLOSED:"statusClosed",MATERIALS_NEEDS_CORRECTION:"statusRevision",DEFERRED:"statusDeferred"};
  return keys[status]?t(keys[status]):t("statusOther").replace("{status}",status||"—");
}

function atsEvidenceLabel(level){
  const keys={
    DIRECT_SNAPSHOT_PASS:"atsEvidenceDirect",
    SYNTHETIC_VERTICAL_PASS:"atsEvidenceVertical",
    SINGLE_SNAPSHOT_PASS:"atsEvidenceSingle",
    SAVED_SEQUENCE_PASS:"atsEvidenceSequence"
  };
  return keys[level]?t(keys[level]):String(level||"—");
}

function safeJobLocator(value){
  try{
    const parsed=new URL(String(value));
    return `${parsed.hostname}${parsed.pathname}`.slice(0,300);
  }catch(_){return "—";}
}

function renderOfficialDiscovery(report){
  const candidates=Array.isArray(report?.candidates)?report.candidates:[];
  document.querySelector("#officialCandidateCount").textContent=t("officialCandidateCount").replace("{count}",String(candidates.length));
  const list=document.querySelector("#officialCandidateList");
  list.innerHTML=candidates.length?candidates.map(item=>`<article class="official-candidate-item"><div><strong>${escapeHtml(item.title||"UNKNOWN")} · ${escapeHtml(item.provider||"—")}</strong><small>${escapeHtml(item.location||"UNKNOWN")} · ${escapeHtml(safeJobLocator(item.discovered_url))}</small><small>${escapeHtml(t("officialNotQueued"))}</small></div><aside>${escapeHtml(t("officialLiveCheckRequired"))}</aside></article>`).join(""):`<p>${escapeHtml(t("officialNoCandidates"))}</p>`;
  document.querySelector("#officialDiscoveryResults").classList.remove("hidden");
}

function clearOfficialDiscovery(){
  state.officialDiscovery=null;
  document.querySelector("#officialCandidateCount").textContent="0";
  document.querySelector("#officialCandidateList").replaceChildren();
  document.querySelector("#officialDiscoveryResults").classList.add("hidden");
}

function readinessStatusLabel(status){
  const keys={
    READY_FOR_OFFLINE_APPLICATION_PREPARATION:"readinessReady",
    NEEDS_ONBOARDING:"readinessNeedsOnboarding", NEEDS_AI:"readinessNeedsAi",
    NEEDS_MASTER_RESUME:"readinessNeedsMaster", NEEDS_EDITABLE_MASTER_RESUME:"readinessNeedsEditableMaster",
    NEEDS_CONFIRMED_CLAIMS:"readinessNeedsClaims", NEEDS_EXTERNAL_CLAIM_APPROVAL:"readinessNeedsClaimApproval",
    NEEDS_TEMPLATE_PREPARATION:"readinessNeedsTemplate"
  };
  return keys[status]?t(keys[status]):String(status||"—");
}

function readinessBlockerLabel(code){
  const keys={
    ONBOARDING_INCOMPLETE:"readinessNeedsOnboarding", AI_NOT_READY:"readinessNeedsAi",
    MASTER_RESUME_MISSING:"readinessNeedsMaster", EDITABLE_MASTER_DOCX_MISSING:"readinessNeedsEditableMaster",
    CONFIRMED_CLAIMS_MISSING:"readinessNeedsClaims", EXTERNAL_CLAIM_APPROVAL_REQUIRED:"readinessNeedsClaimApproval",
    MASTER_TAILORING_MANIFEST_REQUIRED:"readinessNeedsTemplate"
  };
  return keys[code]?t(keys[code]):String(code||"—");
}

function executionRunStatusLabel(status){
  const keys={
    AWAITING_FINAL_AUTHORIZATION:"executionStatusAwaiting",
    CONFIRMED:"executionStatusConfirmed",
    SUBMISSION_UNKNOWN:"executionStatusUnknown",
    INVALIDATED:"executionStatusInvalidated",
    INTERRUPTED_RECONCILIATION_REQUIRED:"executionStatusInterrupted"
  };
  return keys[status]?t(keys[status]):t("executionStatusOther").replace("{status}",status||"—");
}

function executionNextActionLabel(action){
  const keys={
    USER_FINAL_CONFIRMATION_REQUIRED:"executionNextFinal",
    NONE:"executionNextNone",
    MANUAL_EXTERNAL_VERIFICATION_REQUIRED:"executionNextManual",
    REBUILD_REVIEW_PACKET:"executionNextRebuild",
    RESTART_RECONCILIATION_REQUIRED:"executionNextRestart"
  };
  return t(keys[action]||"executionNextOther");
}

function renderApplicationReadiness(){
  const readiness=state.data?.application_readiness||{}, approval=state.data?.external_claim_approval||{};
  const status=document.querySelector("#applicationReadinessStatus"), blockers=document.querySelector("#applicationReadinessBlockers");
  const ready=readiness.status==="READY_FOR_OFFLINE_APPLICATION_PREPARATION";
  status.textContent=readinessStatusLabel(readiness.status);
  status.classList.toggle("ready",ready);
  const items=Array.isArray(readiness.blockers)?readiness.blockers:[];
  blockers.innerHTML=items.length
    ?items.map(item=>`<span class="application-readiness-blocker">${escapeHtml(readinessBlockerLabel(item.code))}</span>`).join("")
    :`<span class="application-readiness-clear">${escapeHtml(t("readinessNoBlockers"))}</span>`;
  const panel=document.querySelector("#externalClaimApprovalPanel"), checkbox=document.querySelector("#externalClaimConfirm"), button=document.querySelector("#approveExternalClaims"), meta=document.querySelector("#externalClaimApprovalMeta");
  const show=Boolean(approval.available||approval.current);
  panel.classList.toggle("hidden",!show);
  if(show){
    const current=approval.current===true, count=Math.max(0,Number(approval.confirmed_claim_count)||0);
    if(current)checkbox.checked=true;
    checkbox.disabled=current||state.data?.demo_mode===true;
    button.disabled=current||!approval.available||!checkbox.checked||state.data?.demo_mode===true;
    meta.textContent=current?t("externalClaimsCurrent").replace("{count}",String(count)):t("externalClaimsCount").replace("{count}",String(count));
    panel.classList.toggle("approved",current);
  }
  const manifest=state.data?.tailoring_manifest||{}, manifestPanel=document.querySelector("#tailoringManifestPanel"), manifestButton=document.querySelector("#openTailoringManifest"), manifestMeta=document.querySelector("#tailoringManifestMeta");
  const showManifest=Boolean(manifest.available||manifest.current);
  manifestPanel.classList.toggle("hidden",!showManifest);
  if(showManifest){
    manifestPanel.classList.toggle("approved",manifest.current===true);
    manifestButton.disabled=manifest.current===true||state.data?.demo_mode===true;
    manifestMeta.textContent=manifest.current
      ?t("tailoringManifestCurrent").replace("{count}",String(Number(manifest.block_count)||0))
      :t("tailoringManifestNeeded");
    if(manifest.current){state.tailoringProposal=null;document.querySelector("#tailoringManifestProposal").classList.add("hidden");}
  }
  const prepareButton=document.querySelector("#prepareOfflineApplication"), prepareStatus=document.querySelector("#offlineApplicationStatus");
  if(prepareButton&&prepareStatus){
    prepareButton.disabled=!ready||state.data?.demo_mode===true;
    prepareStatus.textContent=t(ready?"offlineApplicationReady":"offlineApplicationNeedsReadiness");
  }
  renderGuidedIntake();
}

function guidedIntakeMessage(status){
  const keys={
    IDLE:"guidedIntakeIdle", GUIDED_INTAKE_PAIRING:"guidedPairing", GUIDED_INTAKE_PAIRED:"guidedPaired",
    AWAITING_JOB_DISCOVERY:"guidedPairing", SEARCH_SELECTION_REQUIRED:"guidedStoppedTitle",
    AWAITING_JOB_PAGE_CAPTURE:"guidedAwaitingJob", AWAITING_APPLICATION_FORM_CAPTURE:"guidedAwaitingForm",
    PREPARING_APPLICATION:"guidedPreparing", REVIEW_PACKET_READY:"guidedReady", DEFERRED:"guidedDeferred",
    FORM_CAPTURE_FAILED:"guidedFailed", FAILED:"guidedFailed"
  };
  return t(keys[status]||"guidedIntakeIdle");
}

function guidedFailureMessage(session){
  if(session?.code==="INELIGIBLE"){
    const labels={
      level:"guidedLevelPreferenceMismatch",work_authorization:"guidedGapWorkAuthorization",
      visa_sponsorship:"guidedGapVisa",location:"guidedGapLocation",salary:"guidedGapSalary"
    };
    const gaps=(Array.isArray(session.hard_gap_codes)?session.hard_gap_codes:[]).map(code=>{
      if(String(code).startsWith("hard_requirement:"))return t("guidedGapRequirement");
      return t(labels[code]||"guidedGapUnknown");
    });
    return t("guidedEligibilityReview").replace("{gaps}",[...new Set(gaps.length?gaps:[t("guidedGapUnknown")])].join("、"));
  }
  if(session?.code&&LOCAL_ERROR_KEYS[session.code])return localizedErrorMessage(session);
  return guidedIntakeMessage(session?.status||"FORM_CAPTURE_FAILED");
}

function renderGuidedIntake(){
  const bootstrap=state.data?.guided_intake||{}, session=state.guidedIntakeSession||bootstrap;
  const status=session?.status||"IDLE", ready=state.data?.application_readiness?.status==="READY_FOR_OFFLINE_APPLICATION_PREPARATION";
  const demo=state.data?.demo_mode===true, active=Boolean(session?.active||state.guidedIntakeSession), assistActive=browserCompanionActive();
  const terminal=["REVIEW_PACKET_READY","DEFERRED","FAILED"].includes(status);
  const recoverableStatuses=["GUIDED_INTAKE_PAIRING","AWAITING_JOB_DISCOVERY","SEARCH_SELECTION_REQUIRED","AWAITING_JOB_PAGE_CAPTURE","AWAITING_APPLICATION_FORM_CAPTURE","FORM_CAPTURE_FAILED"];
  const retryPairing=active&&session?.paired!==true&&recoverableStatuses.includes(status)&&state.companionPairing?.kind==="guided";
  const restartPairing=active&&recoverableStatuses.includes(status)&&!state.companionPairing;
  const input=document.querySelector("#guidedOfficialUrl"), command=document.querySelector("#aiOperatorCommand"), start=document.querySelector("#startGuidedIntake"), cancel=document.querySelector("#cancelGuidedIntake");
  if(!input||!start)return;
  const hasGoal=(command?.value||"").trim().length>=3;
  const companionReady=state.companionAvailability?.status==="AVAILABLE";
  input.disabled=demo||!ready||assistActive||(active&&!terminal&&!restartPairing);
  if(command)command.disabled=input.disabled;
  start.textContent=t(retryPairing||restartPairing?"retryCompanionPairing":"startGuidedIntake");
  start.disabled=demo||!ready||assistActive||!companionReady||(retryPairing?false:(!hasGoal||(active&&!terminal&&!restartPairing)));
  const cancellable=["GUIDED_INTAKE_PAIRING","GUIDED_INTAKE_PAIRED","AWAITING_JOB_DISCOVERY","SEARCH_SELECTION_REQUIRED","AWAITING_JOB_PAGE_CAPTURE","AWAITING_APPLICATION_FORM_CAPTURE","FORM_CAPTURE_FAILED"].includes(status);
  if(cancel){cancel.classList.toggle("hidden",demo||!active||!cancellable);cancel.disabled=assistActive;}
  const badge=document.querySelector("#guidedIntakeBadge"), message=document.querySelector("#guidedIntakeMessage");
  const statusMessage=["FORM_CAPTURE_FAILED","FAILED"].includes(status)?guidedFailureMessage(session):guidedIntakeMessage(status);
  badge.textContent=statusMessage;
  const connectionNotice=state.companionPairing?.kind==="guided"&&state.companionConnectionNotice?t(state.companionConnectionNotice):"";
  const availabilityNotice=!active?t(companionAvailabilityKey()):"";
  message.textContent=assistActive?t("companionSessionActive"):ready?[statusMessage,availabilityNotice,connectionNotice].filter(Boolean).join(" "):t("guidedReadinessRequired");
  message.classList.toggle("working",["GUIDED_INTAKE_PAIRING","PREPARING_APPLICATION"].includes(status));
  if(status==="PREPARING_APPLICATION"&&!state.guidedIntakeActivity){
    state.guidedIntakeActivity=beginActivity("preparingGuidedApplication",{estimatedSeconds:300});
  }else if(status!=="PREPARING_APPLICATION"&&state.guidedIntakeActivity){
    endActivity(state.guidedIntakeActivity,status==="REVIEW_PACKET_READY");state.guidedIntakeActivity=null;
  }
  const operatorPanel=document.querySelector("#guidedAiOperatorPlan"), operatorPlan=state.aiOperatorPlan;
  if(operatorPanel){
    operatorPanel.classList.toggle("hidden",!operatorPlan||Boolean(operatorPlan.application_id));
    document.querySelector("#guidedAiOperatorSummary").textContent=operatorPlan?.summary||"";
    document.querySelector("#guidedAiOperatorSteps").innerHTML=operatorPlan&&!operatorPlan.application_id
      ?aiOperatorStepsHtml(operatorPlan):"";
  }
  const link=document.querySelector("#guidedOpenJob"), officialUrl=session?.official_url;
  if(officialUrl&&isHttpsUrl(officialUrl)){link.href=officialUrl;link.classList.remove("hidden");}
  else{link.removeAttribute("href");link.classList.add("hidden");}
  const choices=document.querySelector("#guidedSearchChoices"),candidateOptions=Array.isArray(session?.candidate_options)?session.candidate_options:[];
  if(status==="SEARCH_SELECTION_REQUIRED"&&candidateOptions.length){
    choices.innerHTML=`<strong>${escapeHtml(t("guidedSearchChoiceTitle"))}</strong>${candidateOptions.slice(0,3).map(item=>`<button type="button" class="guided-search-choice secondary" data-candidate-ref="${escapeHtml(item.candidate_ref)}"><span>${escapeHtml(item.title||item.company_domain)}</span><small>${escapeHtml(item.company_domain||"")}</small><b>${escapeHtml(t("guidedSearchChoiceAction"))}</b></button>`).join("")}`;
    choices.classList.remove("hidden");
  }else{choices.replaceChildren();choices.classList.add("hidden");}
  let completed=0, current=0;
  if(["GUIDED_INTAKE_PAIRED","AWAITING_JOB_DISCOVERY"].includes(status)){completed=0;current=0;}
  else if(["SEARCH_SELECTION_REQUIRED","AWAITING_JOB_PAGE_CAPTURE"].includes(status)){completed=1;current=1;}
  else if(status==="AWAITING_APPLICATION_FORM_CAPTURE"){completed=2;current=2;}
  else if(["PREPARING_APPLICATION","FORM_CAPTURE_FAILED"].includes(status)){completed=3;current=3;}
  else if(["REVIEW_PACKET_READY","DEFERRED"].includes(status)){completed=4;current=4;}
  ["#guidedStepOne","#guidedStepTwo","#guidedStepThree","#guidedStepFour"].forEach((selector,index)=>{
    const item=document.querySelector(selector);if(!item)return;
    item.classList.toggle("done",index<completed);
    item.classList.toggle("active",index===current&&completed<4);
  });
  renderWorkflowNow();
}

function browserAssistResultMessage(status){
  const keys={
    BROWSER_COMPANION_PAIRING:"browserAssistPairing", BROWSER_COMPANION_PAIRED:"browserAssistPaired",
    READY:"browserAssistPaired", PAGE_PREPARED:"browserAssistPaired",
    PAGE_REVIEW_REQUIRED:"browserAssistPageReview", MANUAL_NAVIGATION_REQUIRED:"browserAssistManualNavigation", MANUAL_NAVIGATION_RESTART_REQUIRED:"browserAssistManualRestart", APPLY_RESTART_REQUIRED:"browserAssistApplyRestart", HANDOFF_REQUIRED:"browserAssistHandoff",
    SUPPLEMENTAL_REVIEW_REQUIRED:"browserAssistSupplementalReview",
    AWAITING_NAVIGATION:"browserAssistNavigating", NAVIGATION_STARTED:"browserAssistNavigating",
    NAVIGATION_PENDING:"browserAssistNavigating", NAVIGATION_STALLED:"browserAssistNavigationStalled",
    AWAITING_USER_SUBMIT:"browserAssistAwaitingSubmit", OBSERVING_RESULT_PAGE:"browserAssistObserving",
    CONFIRMED:"browserAssistConfirmed", SUBMISSION_UNKNOWN:"browserAssistUnknown",
    AWAITING_APPROVAL:"browserAssistFailed"
  };
  return t(keys[status]||"browserAssistIdle");
}

function browserAssistApplyFailureMessage(result){
  const label=String(result?.failure_field_label||"")
    .replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g,"")
    .replace(/\s+/g," ").trim().slice(0,200);
  const position=Number.isInteger(result?.failure_page_position)&&result.failure_page_position>0
    ?result.failure_page_position:null;
  const reasons=state.locale==="en"?{
    COMPANION_CHOICE_OPTION_NOT_FOUND:"the current page no longer offers the approved choice",
    COMPANION_CHOICE_VALUE_NOT_APPLIED:"the page did not retain the selected choice",
    COMPANION_CUSTOM_SELECT_OPTION_NOT_FOUND:"the menu no longer offers the approved choice",
    COMPANION_CUSTOM_SELECT_VERIFY_FAILED:"the page did not retain the menu selection",
    COMPANION_CONTROL_REBIND_FAILED:"the field structure changed after upload or page refresh",
    COMPANION_CONTROL_CHANGED:"the field structure changed before it could be filled",
    COMPANION_FIELD_VERIFY_FAILED:"the page did not retain the entered value",
    COMPANION_FILE_VERIFY_FAILED:"the page did not confirm the attachment"
  }:{
    COMPANION_CHOICE_OPTION_NOT_FOUND:"当前网页没有审阅时批准的选项",
    COMPANION_CHOICE_VALUE_NOT_APPLIED:"网页没有保留已选择的选项",
    COMPANION_CUSTOM_SELECT_OPTION_NOT_FOUND:"下拉菜单没有审阅时批准的选项",
    COMPANION_CUSTOM_SELECT_VERIFY_FAILED:"网页没有保留下拉菜单选择",
    COMPANION_CONTROL_REBIND_FAILED:"上传或页面刷新后，该字段结构发生了变化",
    COMPANION_CONTROL_CHANGED:"填写前该字段结构发生了变化",
    COMPANION_FIELD_VERIFY_FAILED:"网页没有保留已填写的内容",
    COMPANION_FILE_VERIFY_FAILED:"网页没有确认附件已经保留"
  };
  const reason=reasons[String(result?.failure_code||"")]||"";
  const base=t("browserAssistApplyRestart");
  if(!label&&!position&&!reason)return base;
  if(state.locale==="en"){
    const location=label?`${position?`item ${position} `:""}\"${label}\"`:position?`item ${position}`:"the current control";
    return `Stopped at ${location}. Reason: ${reason||"the page did not pass safe verification"}. ${base}`;
  }
  const location=label?`${position?`第 ${position} 项 `:""}“${label}”`:position?`第 ${position} 项`:"当前控件";
  return `停止位置：${location}。原因：${reason||"网页没有通过安全验证"}。${base}`;
}

function renderBrowserAssist(recent){
  const info=state.data?.browser_assist||{};
  if(!state.browserAssistSelection&&info.active_application_id){
    const activeItem=recent.find(value=>value.application_id===info.active_application_id);
    if(activeItem)state.browserAssistSelection={...activeItem};
  }
  const panel=document.querySelector("#browserAssistPanel");
  panel.classList.toggle("hidden",!state.browserAssistSelection&&!state.browserAssistSession);
  const badge=document.querySelector("#browserAssistBadge"), companion=document.querySelector("#browserCompanionStatus");
  const activeStatus=state.browserAssistSession?.status||info.active_status;
  badge.textContent=activeStatus?browserAssistResultMessage(activeStatus):t("browserAssistIdle");
  const currentAssistId=state.browserAssistSession?.assist_id||info.active_assist_id;
  const localPairing=state.companionPairing?.kind==="assist"&&state.companionPairing.session?.assist_id===currentAssistId;
  companion.textContent=((localPairing&&state.companionPairing.paired)||(!state.companionPairing&&info.paired))
    ?t("browserCompanionPaired")
    :state.companionPairing?t("browserCompanionNotPaired"):t(companionAvailabilityKey());
  const selection=document.querySelector("#browserAssistSelection");
  if(!state.browserAssistSelection){selection.classList.add("hidden");renderWorkflowNow();return;}
  const item=recent.find(value=>value.application_id===state.browserAssistSelection.application_id)||state.browserAssistSelection;
  document.querySelector("#browserAssistJob").textContent=[item.title,item.company].filter(Boolean).join(" · ");
  const run=(info.recent_runs||[]).find(value=>value.assist_id===(state.browserAssistSession?.assist_id||info.active_assist_id));
  const provider=state.browserAssistSession?.provider||info.active_provider||run?.provider;
  const step=state.browserAssistSession?.current_step||info.active_step||run?.current_step;
  const max=state.browserAssistSession?.max_steps||run?.max_steps;
  const meta=provider&&step&&max?t("browserAssistStepMeta").replace("{provider}",provider).replace("{step}",String(step)).replace("{max}",String(max)):"";
  document.querySelector("#browserAssistApplication").textContent=[item.application_id,meta].filter(Boolean).join(" · ");
  const operatorPanel=document.querySelector("#aiOperatorPlan"), operatorPlan=state.aiOperatorPlan;
  const currentPlan=operatorPlan&&operatorPlan.application_id===item.application_id?operatorPlan:null;
  operatorPanel.classList.toggle("hidden",!currentPlan);
  document.querySelector("#aiOperatorSummary").textContent=currentPlan?.summary||"";
  document.querySelector("#aiOperatorSteps").innerHTML=currentPlan?aiOperatorStepsHtml(currentPlan):"";
  const confirm=document.querySelector("#browserAssistConfirm"), start=document.querySelector("#startBrowserAssistNow");
  const guidedActive=guidedCompanionActive();
  const canRepeatPairingHelp=localPairing&&state.companionPairing?.paired!==true;
  const aiReady=state.data?.ai_engine?.status==="READY";
  const companionReady=state.companionAvailability?.status==="AVAILABLE";
  start.disabled=guidedActive||(browserCompanionActive()&&!canRepeatPairingHelp)||!confirm.checked||item.status!=="APPROVED"||!aiReady||!companionReady;
  start.title=!aiReady?t("aiOperatorRequired"):!companionReady?t(companionAvailabilityKey()):"";
  const link=document.querySelector("#browserAssistOpenPage");
  if(state.browserAssistSession?.approved_url){
    link.href=state.browserAssistSession.approved_url;link.classList.remove("hidden");
  }else{link.removeAttribute("href");link.classList.add("hidden");}
  const message=document.querySelector("#browserAssistMessage");
  const connectionNotice=state.companionPairing?.kind==="assist"&&state.companionConnectionNotice?t(state.companionConnectionNotice):"";
  const activeMessage=activeStatus==="APPLY_RESTART_REQUIRED"
    ?browserAssistApplyFailureMessage(state.browserAssistSession?.last_result||state.browserAssistSession)
    :activeStatus?browserAssistResultMessage(activeStatus):"";
  message.textContent=guidedActive?t("companionSessionActive"):[activeMessage,connectionNotice].filter(Boolean).join(" ");
  selection.classList.remove("hidden");
  renderWorkflowNow();
}

async function handleGuidedCompanionStatus(result){
  const record=state.companionPairing;
  if(!state.guidedIntakeSession||record?.kind!=="guided")return false;
  if(result?.intake_id!==state.guidedIntakeSession.intake_id||result?.intake_id!==record.session?.intake_id)return false;
  state.guidedIntakeSession={...state.guidedIntakeSession,...result,active:true,paired:true};
  if(state.companionPairing?.kind==="guided"){
    state.companionPairing.session={...state.guidedIntakeSession};
    persistCompanionPairing(state.companionPairing);
  }
  renderGuidedIntake();
  const terminalKey=`guided:${result.intake_id}:${result.status}`;
  if(state.companionTerminalHandled===terminalKey)return true;
  if(result.status==="REVIEW_PACKET_READY"&&result.application_id){
    state.companionTerminalHandled=terminalKey;
    try{
      await refreshLatest();
      state.reviewPacket=await api("review-packet",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({application_id:result.application_id})});
      state.reviewDecision="";renderReviewPacket();
      document.querySelector("#reviewPacketPanel").scrollIntoView({behavior:"smooth",block:"start"});showToast(t("guidedReady"),false,9000);
      clearCompanionPairing();
    }catch(error){handleUiError(error);}
  }else if(result.status==="DEFERRED"){
    state.companionTerminalHandled=terminalKey;
    try{await refreshLatest();showToast(t("guidedDeferred"),false,9000);clearCompanionPairing();}catch(error){handleUiError(error);}
  }else if(["FORM_CAPTURE_FAILED","FAILED"].includes(result.status)){
    state.companionTerminalHandled=terminalKey;
    const error=Object.assign(new Error(result.message||result.code||t("guidedFailed")),result);
    showToast(guidedFailureMessage(error),true,12000);
  }
  return true;
}

async function handleBrowserCompanionStatus(result){
  const record=state.companionPairing;
  if(!state.browserAssistSession||record?.kind!=="assist")return false;
  if(result?.application_id!==state.browserAssistSession.application_id||result?.assist_id!==state.browserAssistSession.assist_id)return false;
  if(result?.assist_id!==record.session?.assist_id||result?.application_id!==record.session?.application_id)return false;
  state.browserAssistSession={...state.browserAssistSession,...result,paired:true};
  if(state.companionPairing?.kind==="assist"){
    state.companionPairing.session={...state.browserAssistSession};
    persistCompanionPairing(state.companionPairing);
  }
  renderBrowserAssist(state.data?.dashboard?.recent_applications||[]);
  if(["CONFIRMED","SUBMISSION_UNKNOWN","AWAITING_APPROVAL","SUPPLEMENTAL_REVIEW_REQUIRED","APPLY_RESTART_REQUIRED"].includes(result.status)){
    const terminalKey=`assist:${result.application_id}:${result.status}`;
    if(state.companionTerminalHandled!==terminalKey){
      state.companionTerminalHandled=terminalKey;
      if(result.status==="APPLY_RESTART_REQUIRED")showToast(browserAssistApplyFailureMessage(result.last_result||result),true,20000);
      try{
        await refreshLatest();
        if(result.status==="SUPPLEMENTAL_REVIEW_REQUIRED"){
          state.reviewPacket=await api("review-packet",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({application_id:result.application_id})});
          state.reviewDecision="";renderReviewPacket();
          document.querySelector("#reviewPacketPanel").scrollIntoView({behavior:"smooth",block:"start"});
          showToast(t("browserAssistSupplementalReview"),false,10000);
        }
        clearCompanionPairing();
      }catch(error){handleUiError(error);}
    }
  }
  return true;
}

function renderCompanionConnectionState(){
  renderGuidedIntake();renderBrowserAssist(state.data?.dashboard?.recent_applications||[]);
}

function markCompanionBindingLost(record,wasPaired){
  record.paired=false;record.session={...record.session,paired:false};persistCompanionPairing(record);
  if(record.kind==="guided")state.guidedIntakeSession={...record.session,active:true};
  else state.browserAssistSession={...record.session};
  state.companionConnectionNotice=wasPaired?"companionClickToReconnect":"companionClickToPair";
  renderCompanionConnectionState();
}

async function acceptPolledCompanionStatus(record,result){
  if(!companionVersionCurrent(result))throw pairingError(result);
  state.companionPollFailures=0;
  const wasPaired=record.paired===true;
  if(result?.paired!==true||!pairingIdentityMatches(record,result)){
    markCompanionBindingLost(record,wasPaired);
    return false;
  }
  record.paired=true;record.session={...record.session,paired:true};persistCompanionPairing(record);
  if(record.kind==="guided")state.guidedIntakeSession={...record.session,active:true};
  else state.browserAssistSession={...record.session};
  state.companionPollFailures=0;state.companionConnectionNotice=null;
  const statusResult=result.last_result?.status===result.status
    ?{...result.last_result,...result,status:result.status,last_result:result.last_result}
    :result;
  if(record.kind==="guided")await handleGuidedCompanionStatus(statusResult);
  else await handleBrowserCompanionStatus(statusResult);
  return true;
}

function companionPollDelay(){
  if(state.companionPollFailures<=0)return COMPANION_POLL_BASE_MS;
  return Math.min(COMPANION_POLL_MAX_MS,COMPANION_POLL_BASE_MS*(2**Math.min(state.companionPollFailures-1,3)));
}

function scheduleCompanionStatusPoll(delay=companionPollDelay()){
  if(state.companionStatusTimer)clearTimeout(state.companionStatusTimer);
  if(!state.companionPairing){state.companionStatusTimer=null;return;}
  state.companionStatusTimer=setTimeout(()=>{state.companionStatusTimer=null;pollCompanionStatus();},delay);
}

async function pollCompanionStatus(){
  if(state.companionPollInFlight||!state.companionPairing)return;
  state.companionPollInFlight=true;
  const record=state.companionPairing;
  try{
    const result=await companionExternalMessage({type:"JOBFLOW_GET_STATUS",binding:record.pairing},1800);
    if(state.companionPairing!==record)return;
    await acceptPolledCompanionStatus(record,result);
  }catch(error){
    if(state.companionPairing!==record)return;
    // Transport failures never erase a valid lease or silently re-pair. They
    // only slow the next status check; an explicit unpaired response is handled
    // above and tells the user to click the extension.
    state.companionPollFailures+=1;
    state.companionConnectionNotice=error?.code==="BROWSER_COMPANION_UPDATE_REQUIRED"
      ?"guidedExtensionOutdated"
      :record.paired?"companionStatusTemporary":"companionClickToPair";
    renderCompanionConnectionState();
    if(error?.code==="BROWSER_COMPANION_UPDATE_REQUIRED")showToast(t("guidedExtensionOutdated"),true,10000);
  }finally{
    state.companionPollInFlight=false;
    if(state.companionPairing===record)scheduleCompanionStatusPoll();
  }
}

function startCompanionStatusPolling(){
  scheduleCompanionStatusPoll(0);
}

async function resumeCompanionPairing(record){
  if(!record)return;
  state.companionConnectionNotice=record.paired?null:"companionClickToPair";
  renderCompanionConnectionState();
  startCompanionStatusPolling();
  if(!record.paired)await attemptAutomaticCompanionPairing(record);
}

function recentApplicationActions(item){
  if(item.status==="APPROVED"){
    const blocked=guidedCompanionActive()||browserCompanionActive();
    return `<button class="primary compact browser-assist-start" type="button" data-id="${escapeHtml(item.application_id)}"${blocked?` disabled title="${escapeHtml(t("companionSessionActive"))}"`:""}>${escapeHtml(t("startBrowserAssist"))}</button>`;
  }
  if(item.status==="SUBMISSION_UNKNOWN")return `<div class="browser-assist-resolve"><small>${escapeHtml(t("browserAssistUnknown"))}</small><button class="secondary compact resolve-browser-unknown" type="button" data-id="${escapeHtml(item.application_id)}" data-submitted="true">${escapeHtml(t("submittedYes"))}</button><button class="secondary compact resolve-browser-unknown" type="button" data-id="${escapeHtml(item.application_id)}" data-submitted="false">${escapeHtml(t("submittedNo"))}</button></div>`;
  return "";
}

function renderTailoringProposal(preserveInput=false){
  const proposal=state.tailoringProposal, panel=document.querySelector("#tailoringManifestProposal"), list=document.querySelector("#tailoringManifestCandidates");
  if(!proposal){panel.classList.add("hidden");list.replaceChildren();return;}
  const previous=new Map();
  if(preserveInput){
    list.querySelectorAll(".tailoring-candidate").forEach(row=>previous.set(row.dataset.tailoringBlock,{
      selected:row.querySelector(".tailoring-select")?.checked===true,
      category:row.querySelector("select")?.value||""
    }));
  }
  const confirmation=preserveInput&&document.querySelector("#tailoringManifestConfirm").checked;
  const candidates=Array.isArray(proposal.candidates)?proposal.candidates:[];
  document.querySelector("#tailoringManifestCandidateCount").textContent=t("tailoringCandidateCount").replace("{count}",String(candidates.length));
  list.innerHTML=candidates.map(item=>{
    const saved=previous.get(item.block_ref), selected=saved?saved.selected:item.recommended===true;
    const options=(item.allowed_categories||[]).map(category=>`<option value="${escapeHtml(category)}" ${saved?.category===category?"selected":""}>${escapeHtml(categoryLabel(category))}</option>`).join("");
    return `<article class="tailoring-candidate" data-tailoring-block="${escapeHtml(item.block_ref)}"><label><input class="tailoring-select" type="checkbox" ${selected?"checked":""}><span><b>${escapeHtml(item.recommended?t("tailoringRecommended"):t("tailoringManual"))}</b><small>${escapeHtml(item.text)}</small></span></label><label class="tailoring-category"><span>${escapeHtml(t("tailoringCategory"))}</span><select>${options}</select></label></article>`;
  }).join("");
  document.querySelector("#tailoringManifestConfirm").checked=confirmation;
  panel.classList.remove("hidden");
  updateTailoringApprovalButton();
}

function updateTailoringApprovalButton(){
  const selected=document.querySelectorAll(".tailoring-select:checked").length>0;
  const confirmed=document.querySelector("#tailoringManifestConfirm").checked;
  document.querySelector("#approveTailoringManifest").disabled=!selected||!confirmed||state.data?.demo_mode===true;
}

function renderDemoExecution(dashboard,executions,pending,recent){
  const panel=document.querySelector("#demoExecutionPanel");
  if(state.data?.demo_mode!==true){panel.classList.add("hidden");return;}
  const applicationId=state.data?.demo_constraints?.application_id||"";
  const run=executions.find(item=>item.application_id===applicationId);
  const pendingItem=pending.find(item=>item.application_id===applicationId);
  const recentItem=recent.find(item=>item.application_id===applicationId);
  const rehearsal=document.querySelector("#demoRehearsalControls"),finalControls=document.querySelector("#demoFinalControls");
  const status=document.querySelector("#demoExecutionStatus");
  panel.dataset.applicationId=applicationId;
  panel.dataset.runId=run?.run_id||"";
  rehearsal.classList.add("hidden");finalControls.classList.add("hidden");
  if(run?.status==="CONFIRMED")status.textContent=t("demoRehearsalComplete");
  else if(run?.status==="AWAITING_FINAL_AUTHORIZATION"){
    status.textContent=t("demoRehearsalPrepared");
    finalControls.classList.remove("hidden");
  }else if(recentItem?.status==="APPROVED"){
    status.textContent=t("demoExecutionReady");
    rehearsal.classList.remove("hidden");
  }else if(pendingItem)status.textContent=t("demoExecutionApproveFirst");
  else status.textContent=executionNextActionLabel(run?.next_safe_action);
  panel.classList.remove("hidden");
}

function renderDashboard(){
  const dashboard=state.data?.dashboard;
  if(!dashboard)return;
  const queue=dashboard.queue||{}, safety=dashboard.safety||{}, pending=dashboard.pending_applications||[], deferred=dashboard.deferred_intake||[], recent=dashboard.recent_applications||[], executions=dashboard.execution_runs||[];
  renderAiOperatorActivity();
  document.querySelector("#metricOnboarding").textContent=dashboard.onboarding_status==="ONBOARDING_COMPLETE"?t("pipelineReady"):t("pipelineNeedsSetup");
  document.querySelector("#metricAi").textContent=isAiReady(state.data.ai_engine)?t("aiReadyShort"):t("aiMissingShort");
  document.querySelector("#metricAwaiting").textContent=String(queue.awaiting_approval||0);
  document.querySelector("#metricSlots").textContent=String(queue.slots_available||0);
  document.querySelector("#metricLimit").textContent=t("queueLimit").replace("{limit}",queue.pending_limit||0);
  document.querySelector("#metricDeferred").textContent=String(queue.deferred_intake||0);
  const limitInput=document.querySelector("#pendingLimitInput");if(document.activeElement!==limitInput)limitInput.value=String(queue.pending_limit||10);
  document.querySelector("#pendingDashboardCount").textContent=String(pending.length);
  const safe=safety.knowledge_write_operations===0&&safety.submit_capability===false&&safety.automatic_retry===false;
  const guard=document.querySelector("#pipelineGuard");guard.textContent=t("safetyGuardOn");guard.classList.toggle("unsafe",!safe);
  document.querySelector("#safetySites").textContent=String(safety.real_website_accesses||0);
  document.querySelector("#safetyActions").textContent=String(safety.real_external_actions||0);
  document.querySelector("#safetyKnowledge").textContent=String(safety.knowledge_write_operations||0);
  document.querySelector("#safetyMode").textContent=t(safety.network_mode==="LOCAL_OFFLINE_PLUS_USER_PRESENT_BROWSER_ASSIST"?"assistedMode":"offlineMode");
  const controlEnabled=safety.external_action_control_enabled===true;
  document.querySelector("#safetyControl").textContent=t(controlEnabled?"externalControlEnabled":"externalControlLocked");
  document.querySelector("#emergencyStop").disabled=!controlEnabled;
  const list=document.querySelector("#pendingDashboardList");
  list.classList.toggle("empty",!pending.length);
  list.innerHTML=pending.length?pending.map(item=>`<article class="pending-dashboard-item" data-application="${escapeHtml(item.application_id)}"><div><strong>${escapeHtml(item.title)} · ${escapeHtml(item.company)}</strong><small>${escapeHtml([item.location,item.application_id].filter(Boolean).join(" · "))}</small></div><div class="pending-dashboard-item-meta"><b>${escapeHtml(t("awaitingApprovalStatus"))}</b><small>${escapeHtml(t("packetVersion").replace("{version}",item.packet_version||"—"))} · ${escapeHtml(t("packetHash").replace("{hash}",item.packet_hash_prefix||"—"))}</small><button class="secondary compact open-review-packet" type="button" data-id="${escapeHtml(item.application_id)}">${escapeHtml(t("viewPacket"))}</button></div></article>`).join(""):`<p>${escapeHtml(t("pendingEmpty"))}</p>`;
  document.querySelector("#deferredDashboardCount").textContent=String(deferred.length);
  const deferredList=document.querySelector("#deferredDashboardList");deferredList.classList.toggle("empty",!deferred.length);
  deferredList.innerHTML=deferred.length?deferred.map(item=>`<article class="compact-dashboard-item"><div><strong>${escapeHtml(t("safeQueueId"))}: ${escapeHtml(item.safe_intake_id)}</strong><small>${escapeHtml(item.source_type)} · ${escapeHtml(t("queuedAt"))}: ${escapeHtml(item.created_at)}</small></div><aside><b>${escapeHtml(queueStatusLabel(item.status))}</b></aside></article>`).join(""):`<p>${escapeHtml(t("deferredEmpty"))}</p>`;
  document.querySelector("#recentDashboardCount").textContent=String(recent.length);
  const recentList=document.querySelector("#recentDashboardList");recentList.classList.toggle("empty",!recent.length);
  recentList.innerHTML=recent.length?recent.map(item=>`<article class="compact-dashboard-item"><div><strong>${escapeHtml(item.title)} · ${escapeHtml(item.company)}</strong><small>${escapeHtml([item.location,item.application_id,item.updated_at].filter(Boolean).join(" · "))}</small>${item.approval_expires_at?`<small>${escapeHtml(t("approvalExpiry").replace("{time}",item.approval_expires_at))}</small>`:""}</div><aside><b>${escapeHtml(queueStatusLabel(item.status))}</b>${item.packet_id?`<button class="secondary compact open-review-packet" type="button" data-id="${escapeHtml(item.application_id)}">${escapeHtml(t("viewRecord"))}</button>`:""}${recentApplicationActions(item)}</aside></article>`).join(""):`<p>${escapeHtml(t("recentEmpty"))}</p>`;
  document.querySelector("#executionRunsCount").textContent=String(executions.length);
  const executionList=document.querySelector("#executionRunsList");executionList.classList.toggle("empty",!executions.length);
  executionList.innerHTML=executions.length?executions.map(item=>{
    const reviewRequired=["SUBMISSION_UNKNOWN","INTERRUPTED_RECONCILIATION_REQUIRED"].includes(item.status);
    return `<article class="execution-dashboard-item${reviewRequired?" review-required":""}"><div><strong>${escapeHtml(item.title)} · ${escapeHtml(item.company)}</strong><small>${escapeHtml([item.location,item.application_id,item.run_id,item.updated_at].filter(Boolean).join(" · "))}</small><small>${escapeHtml(t("executionCheckpoint").replace("{sequence}",String(item.checkpoint_sequence)))} · ${escapeHtml(t("executionPhaseNow").replace("{phase}",item.last_phase||"—"))}</small></div><aside><b>${escapeHtml(executionRunStatusLabel(item.status))}</b><small>${escapeHtml(executionNextActionLabel(item.next_safe_action))}</small>${item.automatic_retry===false?`<small class="no-retry">${escapeHtml(t("executionNoRetry"))}</small>`:""}</aside></article>`;
  }).join(""):`<p>${escapeHtml(t("executionRunsEmpty"))}</p>`;
  renderDemoExecution(dashboard,executions,pending,recent);
  renderBrowserAssist(recent);
  const capabilities=state.data?.ats_capabilities?.providers||[];
  document.querySelector("#atsCapabilityCount").textContent=String(capabilities.length);
  document.querySelector("#atsCapabilityList").innerHTML=capabilities.map(item=>`<article class="ats-capability-item"><strong>${escapeHtml(item.provider)}</strong><small>${escapeHtml(atsEvidenceLabel(item.offline_evidence_level))}</small><b>${escapeHtml(t("atsUserPresentAssist"))}</b><small>${escapeHtml(t("atsNavigationScoped"))}</small><small>${escapeHtml(t("atsLiveUnverified"))}</small><small>${escapeHtml(t("atsActionsBlocked"))}</small></article>`).join("");
  renderApplicationReadiness();
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
function renderPrefillProposal(items){
  const proposed=Array.isArray(items)?items.filter(item=>String(item.action||"").startsWith("PREFILL")):[];
  return packetList(proposed,item=>{
    const source=item.redacted_summary==="PRIVATE_VALUE_PRESENT"||item.redacted_summary==="ENCRYPTED_JOB_SPECIFIC_CONFIRMATION"
      ?t("prefillPrivateSource")
      :item.redacted_summary==="PUBLIC_VALUE_HASH_PRESENT"?t("prefillPublicSource"):t("prefillMissing");
    const readiness=item.status==="READY"||item.status==="RESOLVED_FOR_APPLICATION"?t("prefillReady"):t("prefillMissing");
    return `<strong>${escapeHtml(packetValue(item.label||item.id))}</strong><br><small>${escapeHtml(packetValue(item.classification))} · ${escapeHtml(source)} · ${escapeHtml(readiness)}</small>`;
  });
}
function materialStatusLabel(value){
  const keys={
    TAILORED_COPY_OF_SINGLE_APPROVED_MASTER:"materialSameMaster",
    REQUESTED_REQUIRED:"materialRequestedRequired", REQUESTED_OPTIONAL:"materialRequestedOptional",
    NOT_REQUESTED:"materialNotRequested", GENERATED_ON_DEMAND:"materialGeneratedOnDemand",
    NOT_GENERATED:"materialNotGenerated", BOUND_CONFIRMED_PUBLIC_VALUE:"materialBoundPublic",
    MISSING_USER_VALUE:"materialMissingValue", BOUND_SECURE_FILE:"materialBoundSecure",
    MISSING_USER_MATERIAL:"materialMissingFile"
  };
  return keys[value]?t(keys[value]):packetValue(value);
}
function materialKindLabel(value){
  return t({github:"materialGithub",portfolio:"materialPortfolio",website:"materialWebsite"}[value]||"packetNone");
}
function executionPhaseLabel(value){
  const keys={LIVE_FRESHNESS_RECHECK:"executionFreshness",GUEST_APPLICATION_ENTRY:"executionGuest",SAFE_FIELD_PREFILL:"executionPrefill",MATERIAL_UPLOAD:"executionUpload",PROTECTED_AND_UNKNOWN_FIELDS:"executionProtected",FINAL_SUBMISSION:"executionSubmit"};
  return keys[value]?t(keys[value]):packetValue(value);
}
function executionStateLabel(value){
  const keys={NOT_EXECUTED:"executionNotExecuted",PLANNED:"executionPlanned",PROPOSED_ONLY:"executionProposed",BLOCKED:"executionBlocked",NOT_REQUIRED:"executionNotRequired"};
  return keys[value]?t(keys[value]):packetValue(value);
}
function executionGateLabel(value){
  const keys={SEPARATE_LIVE_READ_AUTHORIZATION:"gateLiveRead",NONE_AFTER_FRESHNESS:"gateAfterFreshness",USER_ACCOUNT_DECISION:"gateAccount",REVIEW_PACKET_APPROVAL:"gatePacket",SEPARATE_UPLOAD_AUTHORIZATION:"gateUpload",PER_APPLICATION_CONFIRMATION:"gatePerApplication",NONE:"gateNone",FRESH_EXPLICIT_SUBMISSION_APPROVAL:"gateSubmit"};
  return keys[value]?t(keys[value]):packetValue(value);
}
function applicationFieldDecisionOptions(field){
  const allowed=new Set(field.allowed_decisions||["CONFIRMED_VALUE"]);
  const options=[
    ["CONFIRMED_VALUE","packetFieldConfirmValue"],
    ["PREFER_NOT_TO_ANSWER","packetFieldPreferNot"],
    ["NOT_APPLICABLE","packetFieldNotApplicable"]
  ];
  return options.filter(([value])=>allowed.has(value)).map(([value,key])=>`<option value="${value}">${escapeHtml(t(key))}</option>`).join("");
}
function applicationFieldValueControl(field){
  const options=Array.isArray(field.options)?field.options:[];
  if(options.length){
    return `<select class="application-field-value"><option value="">${escapeHtml(t("packetFieldValuePlaceholder"))}</option>${options.map(value=>`<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")}</select>`;
  }
  return `<input class="application-field-value" type="text" maxlength="4000" autocomplete="off" placeholder="${escapeHtml(t("packetFieldValuePlaceholder"))}">`;
}
function updateApplicationFieldResolutionButton(){
  const panel=document.querySelector("#applicationFieldResolutionPanel");
  if(panel.classList.contains("hidden")){updatePacketDecisionButton();return;}
  let valid=true;
  panel.querySelectorAll(".application-field-row").forEach(row=>{
    const decision=row.querySelector(".application-field-decision").value;
    const value=row.querySelector(".application-field-value");
    value.disabled=decision!=="CONFIRMED_VALUE";
    if(decision!=="CONFIRMED_VALUE")value.value="";
    if(decision==="CONFIRMED_VALUE"&&!value.value.trim())valid=false;
  });
  document.querySelector("#saveApplicationFieldResolutions").disabled=!valid||!document.querySelector("#applicationFieldResolutionConfirm").checked;
  updatePacketDecisionButton();
}
function applicationFieldResolutionReady(){
  const panel=document.querySelector("#applicationFieldResolutionPanel");
  if(!panel||panel.classList.contains("hidden"))return true;
  return collectApplicationFieldResolutions()!==null;
}
function updatePacketDecisionButton(){
  const button=document.querySelector("#confirmPacketDecision");
  if(!button)return;
  const fieldsReady=state.reviewDecision!=="APPROVE"||applicationFieldResolutionReady();
  button.disabled=!state.reviewDecision||!fieldsReady;
  const key=state.reviewDecision==="APPROVE"?"confirmApproveAndStart":state.reviewDecision==="REVISE"?"confirmReviseDecision":"confirmRejectDecision";
  button.textContent=t(key);
}
function renderApplicationFieldResolution(result){
  const summary=result?.field_resolution||{},fields=summary.unresolved_fields||[],unknowns=summary.remaining_non_form_unknowns||[];
  const panel=document.querySelector("#applicationFieldResolutionPanel"),list=document.querySelector("#applicationFieldResolutionList");
  document.querySelector("#applicationFieldResolutionConfirm").checked=false;
  if(!fields.length&&!unknowns.length){list.replaceChildren();panel.classList.add("hidden");return;}
  const fieldRows=fields.map(field=>`<article class="application-field-row" data-control-ref="${escapeHtml(field.control_ref)}">
    <div><strong>${escapeHtml(field.label||field.answer_key||field.control_ref)}</strong><small>${escapeHtml(field.required?t("packetFieldRequired"):t("packetFieldOptional"))} · ${escapeHtml(field.classification)}</small></div>
    <label><span>${escapeHtml(t("packetFieldDecision"))}</span><select class="application-field-decision">${applicationFieldDecisionOptions(field)}</select></label>
    <label><span>${escapeHtml(t("packetFieldValue"))}</span>${applicationFieldValueControl(field)}</label>
  </article>`).join("");
  const unknownRows=unknowns.map(unknownId=>`<article class="application-non-form-row" data-unknown-id="${escapeHtml(unknownId)}">
    <div><strong>${escapeHtml(unknownId)}</strong><small>${escapeHtml(t("packetFieldUnknowns"))}</small></div>
    <label><span>${escapeHtml(t("packetFieldDecision"))}</span><select disabled><option>${escapeHtml(t("packetFieldAcknowledgeUnknown"))}</option></select></label>
  </article>`).join("");
  list.innerHTML=fieldRows+unknownRows;
  panel.classList.remove("hidden");updateApplicationFieldResolutionButton();
}
function collectApplicationFieldResolutions(){
  const rows=[...document.querySelectorAll("#applicationFieldResolutionList .application-field-row")];
  const values=[];
  for(const row of rows){
    const decision=row.querySelector(".application-field-decision").value;
    const value=row.querySelector(".application-field-value").value.trim();
    if(decision==="CONFIRMED_VALUE"&&!value)return null;
    values.push({control_ref:row.dataset.controlRef,decision,value:decision==="CONFIRMED_VALUE"?value:""});
  }
  const nonForm=[...document.querySelectorAll("#applicationFieldResolutionList .application-non-form-row")]
    .map(row=>({unknown_id:row.dataset.unknownId,decision:"ACKNOWLEDGED_UNKNOWN"}));
  return {fields:values,nonForm};
}
function renderReviewPacket(){
  const result=state.reviewPacket;if(!result)return;
  const packet=result.packet||{},job=packet.job||result.job_summary||{},fit=packet.fit||{},route=packet.source_route||{},materialPlan=packet.material_plan||{},executionPlan=packet.execution_plan||{};
  document.querySelector("#reviewPacketTitle").textContent=`${packetValue(job.title)} · ${packetValue(job.company)}`;
  document.querySelector("#reviewPacketMeta").textContent=`${result.application_id} · ${t("packetVersion").replace("{version}",result.packet_version||"—")} · ${t("packetStatus")}: ${result.status} · ${t("packetCreated")}: ${result.created_at}`;
  const materialPlanBody=`<dl class="packet-kv"><dt>${escapeHtml(t("materialResume"))}</dt><dd>${escapeHtml(materialStatusLabel(materialPlan.resume?.derivation))}</dd><dt>${escapeHtml(t("materialCoverLetter"))}</dt><dd>${escapeHtml(materialStatusLabel(materialPlan.cover_letter?.request_status))} · ${escapeHtml(materialStatusLabel(materialPlan.cover_letter?.generation_status))}</dd><dt>${escapeHtml(t("materialPortfolioFile"))}</dt><dd>${escapeHtml(materialStatusLabel(materialPlan.portfolio_file?.binding_status))}</dd><dt>${escapeHtml(t("materialExternalActions"))}</dt><dd>${escapeHtml(materialPlan.all_uploads_and_submission_blocked?t("materialBlocked"):t("packetNone"))}</dd></dl>${packetList(materialPlan.public_links,item=>`${escapeHtml(materialKindLabel(item.kind))} · ${escapeHtml(materialStatusLabel(item.binding_status))}`)}`;
  const executionStatus={READY_FOR_REVIEW:"executionReady",NEEDS_USER_INPUT:"executionNeedsInput",NEEDS_ACCOUNT_APPROVAL:"executionNeedsAccount"}[executionPlan.status]||"packetNone";
  const executionBody=`<p><strong>${escapeHtml(t(executionStatus))}</strong></p>${packetList(executionPlan.steps,item=>`<strong>${escapeHtml(packetValue(item.sequence))}. ${escapeHtml(executionPhaseLabel(item.phase))}</strong><br><small>${escapeHtml(executionStateLabel(item.state))} · ${escapeHtml(executionGateLabel(item.gate))}${Number.isInteger(item.item_count)?` · ${escapeHtml(packetValue(item.item_count))}`:""}</small>`)}<p><small>${escapeHtml(t("executionQueueContinues"))}</small></p>`;
  const sections=[
    ["packetJob",`<dl class="packet-kv"><dt>ID</dt><dd>${escapeHtml(packetValue(job.job_id))}</dd><dt>${escapeHtml(t("packetStatus"))}</dt><dd>${escapeHtml(result.application_status)}</dd><dt>URL</dt><dd>${escapeHtml(packetValue(job.official_url))}</dd></dl>`,false],
    ["packetFit",`<dl class="packet-kv"><dt>${escapeHtml(t("packetOverall"))}</dt><dd>${escapeHtml(packetValue(fit.overall_score))}</dd><dt>${escapeHtml(t("packetStatus"))}</dt><dd>${escapeHtml(packetValue(fit.recommendation||fit.eligibility_status))}</dd></dl>${packetList(fit.explanation,item=>escapeHtml(packetValue(item)))}`,false],
    ["packetGaps",packetList(packet.hard_gaps,item=>escapeHtml(packetValue(item))),false],
    ["packetBullets",packetList(packet.resume_bullets,item=>`<strong>${escapeHtml(packetValue(item.text))}</strong><br><small>${escapeHtml(t("packetClaims"))}: ${escapeHtml(packetValue(item.claim_id))} · ${escapeHtml(t("packetEvidence"))}: ${escapeHtml(packetValue(item.evidence))}</small>`),true],
    ["packetPrefillProposal",renderPrefillProposal(packet.form_questions),true],
    ["packetQuestions",packetList(packet.form_questions,item=>`${escapeHtml(packetValue(item.label||item.id))} · ${escapeHtml(packetValue(item.classification||item.action||item.status))}${item.ai_semantic_role?` · AI: ${escapeHtml(packetValue(item.ai_semantic_role))}`:""}`),false],
    ["packetSensitive",packetList(packet.sensitive_fields,item=>`${escapeHtml(packetValue(item.label||item.id))} · ${escapeHtml(packetValue(item.classification||item.action||item.status))}`),false],
    ["packetUploads",packetList(packet.uploads,item=>`${escapeHtml(packetValue(item.filename))} · ${escapeHtml(packetValue(item.purpose))} · ${escapeHtml(packetValue(item.sha256).slice(0,15))}`),false],
    ["packetMaterialPlan",materialPlanBody,true],
    ["packetExecutionPlan",executionBody,true],
    ["packetActions",packetList(packet.external_actions,item=>escapeHtml(packetValue(item))),false],
    ["packetRoute",`<dl class="packet-kv"><dt>Route</dt><dd>${escapeHtml(packetValue(route.route_kind))}</dd><dt>Guest</dt><dd>${escapeHtml(packetValue(route.guest_mode))}</dd><dt>Account</dt><dd>${escapeHtml(packetValue(route.account_action))}</dd></dl>`,false]
  ];
  document.querySelector("#reviewPacketBody").innerHTML=sections.map(([key,body,wide])=>`<section class="packet-section${wide?" wide":""}"><h4>${escapeHtml(t(key))}</h4>${body}</section>`).join("");
  renderApplicationFieldResolution(result);
  const canDecide=result.application_status==="AWAITING_APPROVAL"&&result.status==="AWAITING_APPROVAL";
  if(canDecide&&!state.reviewDecision)state.reviewDecision="APPROVE";
  document.querySelectorAll('input[name="packetDecision"]').forEach(input=>{input.checked=input.value===state.reviewDecision;});
  updatePacketDecisionButton();
  const approveInput=document.querySelector('input[name="packetDecision"][value="APPROVE"]');approveInput.disabled=false;
  document.querySelector("#packetDecisionPanel").classList.toggle("hidden",!canDecide);
  if(!canDecide)state.reviewDecision="";
  const panel=document.querySelector("#reviewPacketPanel");panel.classList.remove("hidden");
  renderWorkflowNow();
}

function navigate(target) {
  document.querySelectorAll(".panel").forEach(el => el.classList.toggle("active-panel", el.id === target));
  document.querySelectorAll(".step").forEach(el => {
    const active=el.dataset.target === target;
    el.classList.toggle("active",active);
    if(active)el.setAttribute("aria-current","step");else el.removeAttribute("aria-current");
  });
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
  const controlId=`answer-${field.id}`;
  const accessibleName=t("answerValueLabel").replace("{field}",field.label[state.locale]);
  if (field.input_type === "select") {
    const opts = [`<option value=""></option>`, ...field.options.map(item=>`<option value="${escapeHtml(item.value)}" ${item.value===value?"selected":""}>${escapeHtml(item.label[state.locale])}</option>`)].join("");
    return `<select id="${escapeHtml(controlId)}" class="answer-input" data-field="${field.id}" aria-label="${escapeHtml(accessibleName)}"${disabled}>${opts}</select>`;
  }
  if (field.input_type === "textarea") return `<textarea id="${escapeHtml(controlId)}" class="answer-input" data-field="${field.id}" aria-label="${escapeHtml(accessibleName)}"${disabled}>${escapeHtml(value)}</textarea>`;
  return `<input id="${escapeHtml(controlId)}" type="text" class="answer-input" data-field="${field.id}" aria-label="${escapeHtml(accessibleName)}" value="${escapeHtml(value)}"${disabled}>`;
}

function answerForField(field) {
  const answer=state.data?.answers?.[field.id];
  return answer&&typeof answer==="object"
    ?answer
    :{value:null,status:"UNKNOWN",source:"UNKNOWN",use_policy:field.default_policy||"reuse",updated_at:null};
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
    answers[id]={value,status,use_policy:row.querySelector(".policy-select")?.value || answerForField(field).use_policy};
  });
  return answers;
}

function renderQuestionRow(field) {
  const answer=answerForField(field);
  const fieldLabel=field.label[state.locale];
  const answerId=`answer-${field.id}`;
  const policyId=`policy-${field.id}`;
  const statusId=`status-${field.id}`;
  return `<div class="question-row" data-question="${field.id}">
    <div class="question-copy"><label for="${escapeHtml(answerId)}">${escapeHtml(fieldLabel)}</label><small>${escapeHtml(field.help[state.locale] || "")}</small></div>
    <div>${inputControl(field, answer)}${field.sensitive?`<div class="policy-row"><label for="${escapeHtml(policyId)}">${escapeHtml(t("policy"))}</label><select id="${escapeHtml(policyId)}" class="policy-select" data-field="${field.id}" aria-label="${escapeHtml(t("answerPolicyLabel").replace("{field}",fieldLabel))}"${disabledAttr(isReadonly())}>${policyOptions(answer.use_policy)}</select></div>`:""}</div>
    <select id="${escapeHtml(statusId)}" class="status-select" data-field="${field.id}" aria-label="${escapeHtml(t("answerStatusLabel").replace("{field}",fieldLabel))}"${disabledAttr(isReadonly())}>${statusOptions(answer.status)}</select>
  </div>`;
}

function renderQuestions() {
  const catalog=state.data.catalog;
  const groups=catalog.groups.map(group=>{
    const fields=catalog.fields.filter(field=>field.group===group.id);
    const resolved=fields.filter(field=>answerForField(field).status!=="UNKNOWN");
    const unresolved=fields.filter(field=>answerForField(field).status==="UNKNOWN");
    const priority=unresolved.filter(field=>field.required_resolution||group.id==="identity_and_contact");
    const optional=unresolved.filter(field=>!priority.includes(field));
    const priorityRows=priority.map(renderQuestionRow).join("");
    const optionalRows=optional.length?`<details class="question-details optional-question-details"><summary>${escapeHtml(t("optionalProfileFacts").replace("{count}",String(optional.length)))}</summary>${optional.map(renderQuestionRow).join("")}</details>`:"";
    const resolvedRows=resolved.length?`<details class="question-details resolved-question-details"><summary>${escapeHtml(t("resolvedProfileFacts").replace("{count}",String(resolved.length)))}</summary>${resolved.map(renderQuestionRow).join("")}</details>`:"";
    return `<section class="question-group" data-question-group="${escapeHtml(group.id)}"><h3>${escapeHtml(group.label[state.locale])}</h3>${priorityRows}${optionalRows}${resolvedRows}</section>`;
  }).join("");
  document.querySelector("#questionGroups").innerHTML=groups;
}

function renderAiConnection() {
  const engine=state.data?.ai_engine||{}, ready=isAiReady(engine);
  const selected=state.data?.ai_connection?.selected||{};
  const button=document.querySelector("#aiConnectButton"), status=document.querySelector("#aiConnectionStatus");
  button.classList.toggle("connected",ready);
  button.textContent=t(ready?"aiConnectedButton":"connectAi");
  status.classList.toggle("ready",ready);
  if(!ready){
    status.textContent=state.aiConnectionErrorCode
      ?aiConnectionErrorMessage({code:state.aiConnectionErrorCode})
      :t("aiNotConnectedStatus");
    return;
  }
  const name=selected.display_name||engine.display_name||engine.provider||"AI";
  const model=selected.model||engine.model||"configured";
  const route=selected.data_route||engine.data_route||engine.private_transport||"configured";
  const refreshWarning=state.aiConnectionRefreshWarning
    ?`<small>${escapeHtml(t("aiConnectionRefreshWarning"))}</small>`:"";
  status.innerHTML=`<strong>${escapeHtml(t("aiConnectedStatus").replace("{name}",name))}</strong><small>${escapeHtml(t("aiConnectedModel").replace("{model}",model).replace("{route}",route))}</small>${refreshWarning}`;
}

function renderSources() {
  const ai=state.data.ai_engine||{};
  const aiReady=isAiReady(ai);
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
      const passed=isCompleteAiAnalysis(item);
      const analysisLabel=passed?t(item.ai_selection_bounded?"analysisPassedSelected":"analysisPassed"):(item.raw_retained?t("analysisMissing"):t("reuploadRequired"));
      const countLabel=passed?`${item.fact_count} ${t("claimCandidates")}`:analysisLabel;
      const coverageLabel=analysisCoverageLabel(item);
      const reupload=item.category==="chatgpt_export"&&!item.raw_retained&&!readonly;
      return `<div class="source-entry"><div><strong>${escapeHtml(item.safe_display_name)}</strong><small>${escapeHtml(item.category)} · ${escapeHtml(countLabel)} · ${escapeHtml(coverageLabel)}</small></div><div class="source-actions"><span class="status-chip ${passed?"analysis-passed":"analysis-needed"}">${escapeHtml(analysisLabel)}</span>${item.raw_retained&&!readonly?`<button class="text-action reprocess-source" data-id="${escapeHtml(item.source_id)}"${disabledAttr(!aiReady)}>${escapeHtml(t("reprocess"))}</button>`:""}${reupload?`<button class="text-action reupload-source" data-id="${escapeHtml(item.source_id)}"${disabledAttr(!aiReady)}>${escapeHtml(t("reuploadAndAnalyze"))}</button>`:""}${!readonly?`<button class="text-action danger remove-source" data-id="${escapeHtml(item.source_id)}">${escapeHtml(t("deleteSource"))}</button>`:""}</div></div>`;
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
    const aiPassed=isCompleteAiAnalysis(summary);
    const coverageLabel=analysisCoverageLabel(summary);
    return `<article class="source-preview" data-preview-source="${escapeHtml(preview.source_id)}">
      <header><div><strong>${escapeHtml(meta.safe_display_name||preview.source_id)}</strong><small>${summary.raw_lines||0} lines → ${summary.reconstructed_blocks||0} blocks → ${candidates.length} candidates · ${escapeHtml(coverageLabel)}${summary.ai_repair_attempted?` · ${escapeHtml(t("aiRepairApplied"))}`:""}</small></div><span class="status-chip ${aiPassed?"analysis-passed":"analysis-needed"}">${escapeHtml(aiPassed?t(summary.ai_selection_bounded?"analysisPassedSelected":"analysisPassed"):t("analysisMissing"))}</span></header>
      ${candidates.length?`<div class="preview-bulk-actions"><button class="text-action select-all-preview" data-id="${escapeHtml(preview.source_id)}">${escapeHtml(t("selectAllClaims"))}</button><button class="text-action clear-all-preview" data-id="${escapeHtml(preview.source_id)}">${escapeHtml(t("clearAllClaims"))}</button></div>`:""}
      ${Number(summary.filtered_candidate_count||0)>0?`<p class="preview-filter-notice">${escapeHtml(t("filteredCandidateNotice").replace("{count}",String(Number(summary.filtered_candidate_count||0))))}</p>`:""}
      <div class="preview-candidates">${candidates.length?candidates.map(item=>`<label class="preview-candidate"><span class="preview-choice"><input class="preview-select" type="checkbox" data-candidate="${escapeHtml(item.candidate_id)}" ${item.selected?"checked":""}><small>${escapeHtml(t("includeAsClaim"))}</small></span><span>${item.entity?`<strong class="entity-label">${escapeHtml(categoryLabel(item.category))} · ${escapeHtml(entityLabel(item))}</strong>`:""}<textarea class="preview-statement" aria-label="${escapeHtml(t("editText"))}">${escapeHtml(item.statement)}</textarea><span class="preview-meta"><select class="preview-category" aria-label="${escapeHtml(t("category"))}">${categoryOptions(item.category,Boolean(item.entity))}</select><small>${escapeHtml(item.selection_reason==="AI_DERIVED_REQUIRES_CONFIRMATION"?t("selectedByDefault"):t("needsReview"))} · lines ${item.provenance?.line_start||"—"}–${item.provenance?.line_end||"—"}${item.provenance?.numeric_format_normalizations?` · ${escapeHtml(t("numericFormatReview"))}`:""}${item.provenance?.citation_adjustment==="ADJACENT_WRAPPED_LINES"?` · ${escapeHtml(t("adjacentWrapReview"))}`:""}</small></span></span></label>`).join(""):`<p class="preview-empty">${escapeHtml(t("previewEmpty"))}</p>`}</div>
      <footer><button class="secondary discard-preview" data-id="${escapeHtml(preview.source_id)}">${escapeHtml(t("discardPreview"))}</button><button class="secondary commit-preview" data-id="${escapeHtml(preview.source_id)}"${disabledAttr(!aiPassed)}>${escapeHtml(t("confirmSource"))}</button><button class="primary include-all-preview" data-id="${escapeHtml(preview.source_id)}"${disabledAttr(!aiPassed||!candidates.length)}>${escapeHtml(t("includeAllClaims"))}</button></footer>
    </article>`;
  }).join("");
  const candidateById=new Map(previews.flatMap(preview=>(preview.candidates||[]).map(item=>[item.candidate_id,item])));
  target.querySelectorAll(".preview-select").forEach(input=>{
    const item=candidateById.get(input.dataset.candidate);
    if(item?.provenance?.classification_review_required!==true)return;
    const meta=input.closest(".preview-candidate")?.querySelector(".preview-meta small");
    if(!meta)return;
    const marker=document.createElement("strong");
    marker.className="classification-review-note";
    marker.textContent=` · ${t("classificationNormalized")}`;
    meta.append(marker);
  });
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
        <div class="claim-split-editor hidden"><small>${escapeHtml(t("splitHelp"))}</small><textarea class="split-statements" aria-label="${escapeHtml(t("splitInputLabel"))}"></textarea><button type="button" class="secondary apply-split" data-id="${escapeHtml(item.claim_id)}">${escapeHtml(t("applySplit"))}</button></div>
      </div>
      <select class="claim-decision" data-claim="${escapeHtml(item.claim_id)}" aria-label="${escapeHtml(t("claimDecisionLabel"))}"${disabledAttr(isReadonly()||item.deleted)}><option value="PENDING" ${item.decision==="PENDING"?"selected":""}>${escapeHtml(t("pending"))}</option><option value="CONFIRMED" ${item.decision==="CONFIRMED"?"selected":""}>${escapeHtml(t("confirmed"))}</option><option value="REJECTED" ${item.decision==="REJECTED"?"selected":""}>${escapeHtml(t("rejected"))}</option></select>
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
  const aiReady=isAiReady(state.data?.ai_engine);
  const demo=state.data?.demo_mode===true;
  const intakeNotice=document.querySelector("#sourceIntakeNotice");
  const intakeRevision=document.querySelector("#sourceStartRevision");
  const intakeGate=demo?"Demo":readonly?"Readonly":!aiReady?"Ai":null;
  banner.classList.toggle("hidden",!readonly);
  intakeNotice.classList.toggle("hidden",!intakeGate);
  intakeRevision.classList.toggle("hidden",intakeGate!=="Readonly");
  if(intakeGate){
    document.querySelector("#sourceIntakeTitle").textContent=t(`sourceIntake${intakeGate}Title`);
    document.querySelector("#sourceIntakeBody").textContent=t(`sourceIntake${intakeGate}Body`);
  }
  document.querySelector("#demoBanner").classList.toggle("hidden",!demo);
  document.body.classList.toggle("is-demo",demo);
  document.querySelector("#aiConnectButton").disabled=demo;
  document.querySelector("#officialCompanyDomain").disabled=demo;
  document.querySelector("#officialCareersUrl").disabled=demo;
  document.querySelector("#officialSnapshotFile").disabled=demo;
  document.querySelector("#analyzeOfficialSnapshot").disabled=demo;
  document.querySelector("#documentFile").disabled=demo||readonly||!aiReady;
  document.querySelector("#aiFile").disabled=demo||readonly||!aiReady;
  document.querySelector("#documentType").disabled=demo||readonly||!aiReady;
  document.querySelector("#aiType").disabled=demo||readonly||!aiReady;
  document.querySelector("#saveAnswers").disabled=readonly;
  document.querySelector("#saveReview").disabled=readonly;
  document.querySelector("#profileReview").disabled=readonly;
  document.querySelector("#finalConfirm").disabled=readonly;
  document.querySelector("#completeOnboarding").disabled=readonly;
  document.querySelector("#mergeClaims").disabled=readonly;
  document.querySelector("#statusText").textContent=readonly?`${t("readonly")} · v${state.data.revision_number}`:`v${state.data.revision_number} · ${t("draftSaved")}`;
  renderApplicationReadiness();
}

async function refresh(cacheBust=false) {
  const next=await api(`bootstrap${cacheBust?`?refresh=${Date.now()}`:""}`);
  if(["zh","en"].includes(next?.locale)){state.locale=next.locale;applyLocale();}
  assertUiCompatibility(next);
  state.data=next;
  state.tailoringProposal=null;
  state.answerDraft=JSON.parse(JSON.stringify(state.data.answers));
  state.claimDraft={}; state.claimEditDraft={}; state.conflictDraft={}; state.selectedClaims=new Set();
  applyLocale(); renderDashboard(); renderSources(); renderQuestions(); renderClaims(); updateProgress(); renderStateMode(); renderWorkflowNow();
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
  if(!sourceType.startsWith("chatgpt_export")&&file.size>MAX_RETAINED_SOURCE_BYTES){showToast(t("sourceSizeInvalid"),true);return;}
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
  const cancelGuided=event.target.closest("#cancelGuidedIntake");
  if(cancelGuided){
    const session=state.guidedIntakeSession||state.data?.guided_intake, intakeId=String(session?.intake_id||"");
    if(!intakeId||!window.confirm(t("cancelGuidedIntakeConfirm")))return;
    const pairing=state.companionPairing?.kind==="guided"?state.companionPairing:null;
    try{
      let companionCleared=true;
      await withActivity("cancellingGuidedIntake",async()=>{
        await api("cancel-guided-intake",{
          method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({intake_id:intakeId,user_confirmed:true})
        });
        companionCleared=await releaseGuidedCompanionBinding(pairing);
      });
      clearCompanionPairing();state.guidedIntakeSession=null;
      state.aiOperatorPlan=null;state.aiOperatorExecution=null;
      document.querySelector("#guidedOfficialUrl").value="";
      if(state.data)state.data.guided_intake={status:"IDLE",active:false,real_external_actions:0};
      renderGuidedIntake();
      try{await refreshLatest();}catch(_error){/* The local cancellation already succeeded; the next refresh reconciles state. */}
      showToast(t(companionCleared?"guidedCancelled":"guidedCancelledCompanionReload"),!companionCleared,12000);
    }catch(error){handleUiError(error);renderGuidedIntake();}
    return;
  }
  const guidedSearchChoice=event.target.closest(".guided-search-choice");
  if(guidedSearchChoice){
    const record=state.companionPairing,session=state.guidedIntakeSession||state.data?.guided_intake;
    if(record?.kind!=="guided"||!session?.intake_id)return;
    try{
      let selected;
      await withActivity("guidedSearchChoiceSaving",async()=>{
        selected=await api("select-guided-search-candidate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
          intake_id:session.intake_id,candidate_ref:guidedSearchChoice.dataset.candidateRef,user_confirmed:true
        })});
        state.guidedIntakeSession={...session,...selected,active:true,paired:true};
        record.session={...state.guidedIntakeSession};persistCompanionPairing(record);renderGuidedIntake();
        const paired=await companionExternalMessage({type:"JOBFLOW_PAIR",pairing:record.pairing},4200);
        acceptCompanionPairResult(paired);
      },{estimatedSeconds:8});
    }catch(error){handleUiError(error);renderGuidedIntake();}
    return;
  }
  const startGuided=event.target.closest("#startGuidedIntake");
  if(startGuided){
    if(companionModeConflict("guided")){handleUiError(makeUiError("BROWSER_COMPANION_SESSION_ACTIVE"));return;}
    const ready=state.data?.application_readiness?.status==="READY_FOR_OFFLINE_APPLICATION_PREPARATION";
    const officialUrl=jobUrlFromOperatorInputs();
    const command=(document.querySelector("#aiOperatorCommand")?.value||t("aiOperatorCommandDefault")).trim();
    const retryRecord=state.companionPairing?.kind==="guided"&&!state.guidedIntakeSession?.paired?state.companionPairing:null;
    if(!ready){showToast(t("guidedReadinessRequired"),true);return;}
    if(!retryRecord&&command.length<3){showToast(t("guidedUrlRequired"),true);return;}
    try{
      await withActivity("startingGuidedIntake",async()=>{
        await requireCurrentCompanion();
        let record=retryRecord;
        if(!record){
          const operated=await api("start-job-with-ai",{
            method:"POST",headers:{"Content-Type":"application/json"},
            body:JSON.stringify({command,official_url:officialUrl,user_confirmed:true})
          });
          state.aiOperatorPlan=operated.operator_plan;state.aiOperatorExecution=operated.operator_execution||null;
          const result=operated.guided_intake;
          if(!result){
            renderGuidedIntake();
            showToast(state.aiOperatorPlan?.summary||t("localRequestFailed"),true,12000);
            return;
          }
          state.guidedIntakeSession={...result,active:true,paired:false};
          record={kind:"guided",paired:false,expires_epoch:Date.parse(result.expires_at),session:{...state.guidedIntakeSession},pairing:{protocol_version:result.protocol_version,base_url:location.origin,assist_path:result.intake_path}};
          persistCompanionPairing(record);renderGuidedIntake();
        }
        await beginCompanionPairing(record);
      });
    }catch(error){document.querySelector("#guidedIntakeMessage").classList.remove("working");handleUiError(error);renderGuidedIntake();}
    return;
  }
  const chooseBrowserAssist=event.target.closest(".browser-assist-start");
  if(chooseBrowserAssist){
    if(companionModeConflict("assist")||browserCompanionActive()){handleUiError(makeUiError("BROWSER_COMPANION_SESSION_ACTIVE"));return;}
    const applicationId=chooseBrowserAssist.dataset.id;
    const item=(state.data?.dashboard?.recent_applications||[]).find(value=>value.application_id===applicationId);
    if(!item||item.status!=="APPROVED"){showToast(t("reviewDecisionUnavailable"),true);return;}
    clearCompanionPairing();state.browserAssistSelection={...item};state.browserAssistSession=null;state.aiOperatorPlan=null;state.aiOperatorExecution=null;
    document.querySelector("#browserAssistConfirm").checked=false;
    document.querySelector("#browserAssistMessage").textContent="";
    renderBrowserAssist(state.data?.dashboard?.recent_applications||[]);
    document.querySelector("#browserAssistPanel").scrollIntoView({behavior:"smooth",block:"start"});
    return;
  }
  const startBrowserAssist=event.target.closest("#startBrowserAssistNow");
  if(startBrowserAssist){
    if(!state.browserAssistSelection||!document.querySelector("#browserAssistConfirm").checked){showToast(t("browserAssistConfirmFirst"),true);return;}
    const existingRecord=state.companionPairing?.kind==="assist"&&state.companionPairing.paired!==true&&state.companionPairing.session?.application_id===state.browserAssistSelection.application_id;
    if(companionModeConflict("assist")||(browserCompanionActive()&&!existingRecord)){handleUiError(makeUiError("BROWSER_COMPANION_SESSION_ACTIVE"));return;}
    try{
      await withActivity("startingBrowserAssist",async()=>{
        await requireCurrentCompanion();
        let record=state.companionPairing?.kind==="assist"&&!state.browserAssistSession?.paired?state.companionPairing:null;
        if(!record){
          const operated=await api("start-application-with-ai",{
            method:"POST",headers:{"Content-Type":"application/json"},
            body:JSON.stringify({application_id:state.browserAssistSelection.application_id,user_confirmed:true})
          });
          state.aiOperatorPlan=operated.operator_plan;state.aiOperatorExecution=operated.operator_execution||null;renderBrowserAssist(state.data?.dashboard?.recent_applications||[]);
          const result=operated.browser_assist;
          if(!result){
            showToast(state.aiOperatorPlan?.summary||t("localRequestFailed"),true,12000);
            return;
          }
          state.browserAssistSession={...result,paired:false};
          record={kind:"assist",paired:false,expires_epoch:Date.parse(result.expires_at),session:{...state.browserAssistSession},pairing:{protocol_version:result.protocol_version,base_url:location.origin,assist_path:result.assist_path}};
          persistCompanionPairing(record);
          document.querySelector("#browserAssistMessage").textContent=t("browserAssistPairing");renderBrowserAssist(state.data?.dashboard?.recent_applications||[]);
        }
        await beginCompanionPairing(record);
      });
    }catch(error){handleUiError(error);}
    return;
  }
  const resolveUnknown=event.target.closest(".resolve-browser-unknown");
  if(resolveUnknown){
    if(!window.confirm(t("resolveUnknownConfirm")))return;
    const submitted=resolveUnknown.dataset.submitted==="true";
    try{
      await withActivity("resolvingSubmission",()=>api("resolve-browser-assist-unknown",{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({application_id:resolveUnknown.dataset.id,submitted,user_confirmed:true})
      }));
      state.browserAssistSelection=null;state.browserAssistSession=null;
      await refreshLatest();showToast(t("browserAssistResolved"));
    }catch(error){handleUiError(error);}
    return;
  }
  const runSyntheticRehearsal=event.target.closest("#runSyntheticRehearsal");
  if(runSyntheticRehearsal){
    if(!document.querySelector("#demoRehearsalConfirm").checked){showToast(t("demoRehearsalConfirmFirst"),true);return;}
    const panel=document.querySelector("#demoExecutionPanel");
    try{
      await withActivity("demoPreparingRehearsal",async()=>{
        await api("prepare-synthetic-execution",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({application_id:panel.dataset.applicationId,user_confirmed:true})});
        await refreshLatest();
      },{estimatedSeconds:8});
      showToast(t("demoRehearsalPrepared"));
      document.querySelector("#executionRunsTitle").scrollIntoView({behavior:"smooth",block:"start"});
    }catch(error){handleUiError(error);}
    return;
  }
  const completeSyntheticRehearsal=event.target.closest("#completeSyntheticRehearsal");
  if(completeSyntheticRehearsal){
    if(!document.querySelector("#demoFinalConfirm").checked){showToast(t("demoFinalConfirmFirst"),true);return;}
    const panel=document.querySelector("#demoExecutionPanel");
    try{
      await withActivity("demoCompletingRehearsal",async()=>{
        await api("complete-synthetic-execution",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({application_id:panel.dataset.applicationId,run_id:panel.dataset.runId,user_confirmed:true})});
        await refreshLatest();
      },{estimatedSeconds:8});
      showToast(t("demoRehearsalComplete"));
    }catch(error){handleUiError(error);}
    return;
  }
  const dashboardRefresh=event.target.closest("#refreshDashboard");
  if(dashboardRefresh){try{await withActivity("refreshingDashboard",()=>refreshLatest());showToast(t("dashboardRefreshed"));}catch(error){handleUiError(error);}return;}
  const emergencyStop=event.target.closest("#emergencyStop");
  if(emergencyStop){
    if(!window.confirm(t("emergencyStopConfirm")))return;
    try{
      await withActivity("stoppingExternalActions",async()=>{
        await api("external-action-kill-switch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user_confirmed:true})});
        await refreshLatest();
      });
      showToast(t("emergencyStopped"));
    }catch(error){handleUiError(error);}
    return;
  }
  const approveExternalClaims=event.target.closest("#approveExternalClaims");
  if(approveExternalClaims){
    const approval=state.data?.external_claim_approval||{};
    if(!document.querySelector("#externalClaimConfirm").checked){showToast(t("externalClaimConfirmFirst"),true);return;}
    try{
      await withActivity("approvingExternalClaims",async()=>{
        await api("approve-external-claims",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
          user_confirmed:true, expected_review_hash:approval.review_hash, allowed_uses:approval.allowed_uses||[]
        })});
        await refreshLatest();
      });
      showToast(t("externalClaimsApproved"));
    }catch(error){handleUiError(error);}
    return;
  }
  const openTailoring=event.target.closest("#openTailoringManifest");
  if(openTailoring){
    try{
      let proposal=null;
      await withActivity("loadingTailoringManifest",async()=>{proposal=await api("tailoring-manifest-proposal",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});});
      state.tailoringProposal=proposal;renderTailoringProposal();
      document.querySelector("#tailoringManifestProposal").scrollIntoView({behavior:"smooth",block:"center"});
    }catch(error){handleUiError(error);}
    return;
  }
  const selectTailoring=event.target.closest("#selectRecommendedTailoring");
  if(selectTailoring){
    (state.tailoringProposal?.candidates||[]).forEach(item=>{const row=document.querySelector(`[data-tailoring-block="${CSS.escape(item.block_ref)}"]`);if(row)row.querySelector(".tailoring-select").checked=item.recommended===true;});
    updateTailoringApprovalButton();return;
  }
  const approveTailoring=event.target.closest("#approveTailoringManifest");
  if(approveTailoring){
    if(!document.querySelector("#tailoringManifestConfirm").checked){showToast(t("tailoringConfirmFirst"),true);return;}
    const selections=[...document.querySelectorAll(".tailoring-candidate")].filter(row=>row.querySelector(".tailoring-select").checked).map(row=>({block_ref:row.dataset.tailoringBlock,category:row.querySelector("select").value}));
    if(!selections.length){showToast(t("tailoringSelectOne"),true);return;}
    try{
      await withActivity("approvingTailoringManifest",async()=>{
        await api("approve-tailoring-manifest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user_confirmed:true,expected_proposal_hash:state.tailoringProposal?.proposal_hash,selections})});
        state.tailoringProposal=null;await refreshLatest();
      });
      showToast(t("tailoringManifestApproved"));
    }catch(error){handleUiError(error);}
    return;
  }
  const prepareOfflineApplication=event.target.closest("#prepareOfflineApplication");
  if(prepareOfflineApplication){
    const ready=state.data?.application_readiness?.status==="READY_FOR_OFFLINE_APPLICATION_PREPARATION";
    if(!ready){showToast(t("offlineApplicationNeedsReadiness"),true);return;}
    const officialUrl=document.querySelector("#applicationOfficialUrl").value.trim();
    const applicationUrl=document.querySelector("#applicationFormUrl").value.trim();
    const evidenceExcerpt=document.querySelector("#applicationEvidenceExcerpt").value.replace(/\s+/g," ").trim();
    const jdFile=document.querySelector("#applicationJdFile").files[0];
    const officialFile=document.querySelector("#applicationOfficialFile").files[0];
    const formFile=document.querySelector("#applicationFormFile").files[0];
    const extensions={jd:[".txt",".html",".htm",".pdf",".json"],official:[".html",".htm",".txt"],form:[".html",".htm",".json"]};
    const validFiles=jdFile&&officialFile&&formFile
      &&extensions.jd.includes(fileExtension(jdFile))&&jdFile.size>=1&&jdFile.size<=MAX_APPLICATION_JD_BYTES
      &&extensions.official.includes(fileExtension(officialFile))&&officialFile.size>=1&&officialFile.size<=MAX_APPLICATION_OFFICIAL_BYTES
      &&extensions.form.includes(fileExtension(formFile))&&formFile.size>=1&&formFile.size<=MAX_APPLICATION_FORM_BYTES;
    if(!isHttpsUrl(officialUrl)||!isHttpsUrl(applicationUrl)||!validFiles||evidenceExcerpt.length<12||evidenceExcerpt.length>2000){
      showToast(t("offlineApplicationInputsRequired"),true);return;
    }
    const guestChoice=document.querySelector("#applicationGuestMode").value;
    const guestAvailable=guestChoice==="yes"?true:guestChoice==="no"?false:null;
    const parts=[{key:"jd",file:jdFile},{key:"official",file:officialFile},{key:"form",file:formFile}];
    const bundle=buildOfflineApplicationBundle({
      official_url:officialUrl,application_url:applicationUrl,
      guest_available:guestAvailable,evidence_excerpt:evidenceExcerpt
    },parts);
    try{
      let result=null, openedPacket=null;
      await withActivity("preparingOfflineApplication",async activityId=>{
        result=await uploadApi("prepare-offline-application",bundle,{
          onProgress:(loaded,total)=>updateActivity(activityId,{phase:"uploading",loadedBytes:loaded,totalBytes:total||bundle.size}),
          onUploaded:()=>updateActivity(activityId,{phase:"processing",phaseStarted:Date.now(),loadedBytes:bundle.size,totalBytes:bundle.size,estimatedSeconds:300})
        });
        await refreshLatest();
        if(result?.application_id){
          openedPacket=await api("review-packet",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({application_id:result.application_id})});
        }
      },{estimatedSeconds:300});
      if(result?.application_id){
        state.reviewPacket=openedPacket;
        state.reviewDecision="";renderReviewPacket();
        document.querySelector("#reviewPacketPanel").scrollIntoView({behavior:"smooth",block:"start"});
        showToast(t("offlineApplicationPrepared"));
      }else{showToast(t("offlineApplicationDeferred"));}
      ["#applicationJdFile","#applicationOfficialFile","#applicationFormFile"].forEach(selector=>{document.querySelector(selector).value="";});
    }catch(error){showToast(localizedErrorMessage(error),true,9000);}
    return;
  }
  const analyzeSnapshot=event.target.closest("#analyzeOfficialSnapshot");
  if(analyzeSnapshot){
    clearOfficialDiscovery();
    const companyDomain=document.querySelector("#officialCompanyDomain").value.trim();
    const officialUrl=document.querySelector("#officialCareersUrl").value.trim();
    const file=document.querySelector("#officialSnapshotFile").files[0];
    if(!companyDomain||!officialUrl||!file){showToast(t("officialInputsRequired"),true);return;}
    const extension=(file.name.match(/\.[^.]+$/)||[""])[0].toLowerCase();
    if(![".html",".htm",".json"].includes(extension)){showToast(t("sourceTypeUnsupported"),true);return;}
    if(file.size<1||file.size>MAX_OFFICIAL_SNAPSHOT_BYTES){showToast(t("sourceSizeInvalid"),true);return;}
    const sourceFormat=extension===".json"?"auto":"html";
    try{
      let report=null;
      await withActivity("discoveringJobs",async()=>{
        const query=`discover-official-jobs?official_url=${encodeURIComponent(officialUrl)}&company_domain=${encodeURIComponent(companyDomain)}&source_format=${encodeURIComponent(sourceFormat)}`;
        report=await uploadApi(query,file);
      });
      state.officialDiscovery=report;
      renderOfficialDiscovery(state.officialDiscovery);
      showToast(t("officialDiscoveryComplete"));
    }catch(error){showToast(localizedErrorMessage(error),true);}
    return;
  }
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
      state.reviewDecision="";
      renderReviewPacket();
      document.querySelector("#reviewPacketPanel").scrollIntoView({behavior:"smooth",block:"start"});
    }catch(error){state.reviewPacket=null;document.querySelector("#reviewPacketPanel").classList.add("hidden");renderWorkflowNow();handleUiError(error);}
    return;
  }
  const closePacket=event.target.closest("#closeReviewPacket");
  if(closePacket){state.reviewPacket=null;state.reviewDecision="";document.querySelector("#reviewPacketBody").replaceChildren();document.querySelector("#applicationFieldResolutionList").replaceChildren();document.querySelector("#applicationFieldResolutionPanel").classList.add("hidden");document.querySelector("#reviewPacketPanel").classList.add("hidden");renderWorkflowNow();return;}
  const saveApplicationFields=event.target.closest("#saveApplicationFieldResolutions");
  if(saveApplicationFields){
    const packet=state.reviewPacket?.packet,resolutionDraft=collectApplicationFieldResolutions();
    if(!packet||!resolutionDraft||(resolutionDraft.fields.length+resolutionDraft.nonForm.length)<1||!document.querySelector("#applicationFieldResolutionConfirm").checked){showToast(t("packetFieldInvalid"),true);return;}
    try{
      let refreshedPacket=null;
      await withActivity("savingApplicationFields",async()=>{
        await api("resolve-application-fields",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
          application_id:state.reviewPacket.application_id,expected_packet_hash:packet.content_hash,
          resolutions:resolutionDraft.fields,non_form_resolutions:resolutionDraft.nonForm,user_confirmed:true
        })});
        document.querySelectorAll("#applicationFieldResolutionList .application-field-value").forEach(input=>{input.value="";});
        refreshedPacket=await api("review-packet",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({application_id:state.reviewPacket.application_id})});
        await refreshLatest();
      });
      state.reviewPacket=refreshedPacket;state.reviewDecision="";renderReviewPacket();
      document.querySelector("#reviewPacketPanel").scrollIntoView({behavior:"smooth",block:"start"});showToast(t("packetFieldsSaved"));
    }catch(error){handleUiError(error);}
    return;
  }
  const confirmPacketDecision=event.target.closest("#confirmPacketDecision");
  if(confirmPacketDecision){
    if(!state.reviewDecision){showToast(t("chooseDecision"),true);return;}
    const decision=state.reviewDecision,packet=state.reviewPacket?.packet;
    if(!packet){showToast(t("reviewPacketUnavailable"),true);return;}
    try{
      let decisionResult;
      const waitingCount=Number(state.data?.dashboard?.queue?.deferred_intake||0);
      await withActivity("savingQueueDecision",async()=>{
        if(decision==="APPROVE"){
          await requireCurrentCompanion();
          const job=packet.job||{};
          const resolutionDraft=collectApplicationFieldResolutions();
          if(!resolutionDraft){throw Object.assign(new Error(t("packetFieldInvalid")),{code:"APPLICATION_FIELD_RESOLUTIONS_INCOMPLETE"});}
          decisionResult=await api("approve-and-start-application",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
            application_id:state.reviewPacket.application_id,expected_packet_hash:packet.content_hash,
            resolutions:resolutionDraft.fields,non_form_resolutions:resolutionDraft.nonForm,user_confirmed:true
          })});
          const result=decisionResult.browser_assist;
          if(!result)throw Object.assign(new Error(decisionResult.operator_plan?.summary||t("localRequestFailed")),{code:"AI_OPERATOR_NEEDS_USER_INPUT"});
          clearCompanionPairing();
          state.browserAssistSelection={application_id:decisionResult.application_id,title:job.title||"",company:job.company||"",status:"APPROVED"};
          state.aiOperatorPlan=decisionResult.operator_plan||null;state.aiOperatorExecution=decisionResult.operator_execution||null;
          state.browserAssistSession={...result,paired:false};
          const record={kind:"assist",paired:false,expires_epoch:Date.parse(result.expires_at),session:{...state.browserAssistSession},pairing:{protocol_version:result.protocol_version,base_url:location.origin,assist_path:result.assist_path}};
          persistCompanionPairing(record);await beginCompanionPairing(record);
        }else{
          decisionResult=await api("queue-decision",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
            application_id:state.reviewPacket.application_id,decision,expected_packet_hash:packet.content_hash,user_confirmed:true
          })});
        }
        state.reviewPacket=null;state.reviewDecision="";
        document.querySelector("#reviewPacketBody").replaceChildren();document.querySelector("#reviewPacketPanel").classList.add("hidden");
        await refreshLatest();
      },{estimatedSeconds:waitingCount>0?180:7});
      const baseMessage=t(decision==="APPROVE"?"decisionApprovedAndStarted":decision==="REVISE"?"decisionRevised":"decisionRejected");
      const queueDecision=decision==="APPROVE"?decisionResult?.decision:decisionResult;
      const continued=Number(queueDecision?.continued_intake?.prepared_count||0)>0
        ?` ${t("nextApplicationPrepared")}`
        :Number(queueDecision?.continued_intake?.failed_count||0)>0?` ${t("nextApplicationNeedsRepair")}`:"";
      showToast(baseMessage+continued);
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
    let refreshFailed=false;
    try{
      await withActivity(activity,async()=>{
        const connectionResult=await api("connect-ai",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode})});
        state.aiConnectionErrorCode=null;
        state.aiConnectionRefreshWarning=false;
        if(state.data&&connectionResult?.ai_engine&&connectionResult?.ai_connection){
          state.data.ai_engine=connectionResult.ai_engine;
          state.data.ai_connection=connectionResult.ai_connection;
          renderDashboard();renderSources();renderReadiness();renderStateMode();
        }
        try{await refresh(true);}
        catch(_refreshError){
          refreshFailed=true;
          state.aiConnectionRefreshWarning=true;
          renderAiConnection();
        }
      });
      showToast(t(refreshFailed?"aiConnectionRefreshWarning":"aiConnectionSucceeded"),false,refreshFailed?12000:4200);
    }catch(error){
      state.aiConnectionErrorCode=error?.code||"AI_CONNECTION_NOT_FOUND";
      state.aiConnectionRefreshWarning=false;
      renderAiConnection();
      showToast(aiConnectionErrorMessage(error),true,12000);
    }
    return;
  }
  const locale=event.target.closest("[data-locale]");
  if (locale) {
    const draft=collectAnswerDraft();
    collectClaimEdits();
    Object.assign(state.data.answers,draft);
    state.locale=locale.dataset.locale; applyLocale(); renderDashboard(); renderSources(); renderQuestions(); renderClaims(); renderReadiness(); renderStateMode(); renderWorkflowNow();
    if(state.officialDiscovery)renderOfficialDiscovery(state.officialDiscovery);
    if(!isReadonly()){try { await withActivity("savingLanguage",()=>api("save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({locale:state.locale,answers:{}})})); } catch(e) { handleUiError(e); }}
    return;
  }
  const revision=event.target.closest("[data-start-revision]");
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

window.addEventListener("message",async event=>{
  if(event.source!==window||event.origin!==location.origin||event.data?.protocol_version!==2)return;
  const type=event.data?.type,result=event.data?.result;
  if(type==="JOBFLOW_COMPANION_READY"){postPendingCompanionPairing();return;}
  if(!result||typeof result!=="object")return;
  if(type==="JOBFLOW_PAIR_RESULT"){
    try{acceptCompanionPairResult(result);}catch(error){handleUiError(error);}
    return;
  }
  if(type==="JOBFLOW_INTAKE_STATUS"){await handleGuidedCompanionStatus(result);return;}
  if(type==="JOBFLOW_ASSIST_STATUS")await handleBrowserCompanionStatus(result);
});

document.querySelector("#documentFile").addEventListener("change",e=>{upload(e.target.files[0],document.querySelector("#documentType").value);e.target.value="";});
document.querySelector("#aiFile").addEventListener("change",e=>{upload(e.target.files[0],document.querySelector("#aiType").value);e.target.value="";});
function syncAiFileType(){document.querySelector("#aiFile").accept=document.querySelector("#aiType").value==="ai_summary"?".txt,.md,.json":".zip";}
document.querySelector("#aiType").addEventListener("change",syncAiFileType);
syncAiFileType();
document.addEventListener("change",event=>{
  if(event.target.matches("#guidedOfficialUrl,#aiOperatorCommand")){renderGuidedIntake();return;}
  if(event.target.matches("#officialCompanyDomain,#officialCareersUrl,#officialSnapshotFile")){
    clearOfficialDiscovery();
    return;
  }
  if(event.target.matches('input[name="packetDecision"]')){state.reviewDecision=event.target.value;updatePacketDecisionButton();}
  if(event.target.matches("#applicationFieldResolutionConfirm,.application-field-decision,.application-field-value")){updateApplicationFieldResolutionButton();}
  if(event.target.matches("#externalClaimConfirm")){renderApplicationReadiness();}
  if(event.target.matches("#browserAssistConfirm")){
    const selected=state.browserAssistSelection;
    const canRepeatPairingHelp=state.companionPairing?.kind==="assist"&&state.companionPairing.paired!==true&&state.companionPairing.session?.application_id===selected?.application_id;
    document.querySelector("#startBrowserAssistNow").disabled=guidedCompanionActive()||(browserCompanionActive()&&!canRepeatPairingHelp)||!event.target.checked||!selected||selected.status!=="APPROVED";
  }
  if(event.target.matches("#tailoringManifestConfirm,.tailoring-select")){updateTailoringApprovalButton();}
  if(event.target.matches(".answer-input")){
    const row=event.target.closest("[data-question]"), status=row?.querySelector(".status-select");
    const hasValue=Array.isArray(event.target.value)?event.target.value.length>0:String(event.target.value||"").trim().length>0;
    if(status && hasValue && status.value==="UNKNOWN") status.value="CONFIRMED";
  }
  if(event.target.matches(".claim-decision")){state.claimDraft[event.target.dataset.claim]=event.target.value;updateReviewProgress();}
  if(event.target.matches(".conflict-resolution")){state.conflictDraft[event.target.dataset.conflict]=event.target.value;updateReviewProgress();}
});
document.addEventListener("input",event=>{
  if(event.target.matches("#guidedOfficialUrl,#aiOperatorCommand")){renderGuidedIntake();return;}
  if(event.target.matches(".application-field-value")){updateApplicationFieldResolutionButton();return;}
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

document.querySelector("#workflowNowAction").addEventListener("click",focusWorkflowNow);

withActivity("loadingInitial",async()=>{
  await refresh();
  await probeCompanionAvailability();
  const record=restoreCompanionPairing();
  if(record){renderGuidedIntake();renderBrowserAssist(state.data?.dashboard?.recent_applications||[]);await resumeCompanionPairing(record);}
}).catch(handleUiError);
