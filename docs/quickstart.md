# JobFlow quick start / JobFlow 快速开始

This guide gets a Windows user from an extracted JobFlow source package to a safe local tour without entering a project path, API key, resume, or personal answer.

本指南帮助 Windows 用户从解压后的 JobFlow 源码包进入安全的本机体验；无需输入项目路径、API Key、简历或个人答案。

## 60-second synthetic tour / 60 秒合成体验

1. Extract the complete source ZIP to a normal Windows folder. Do not run it inside the ZIP preview. / 将完整源码 ZIP 解压到普通 Windows 文件夹，不要直接在压缩包预览中运行。
2. Double-click `Install JobFlow.cmd` once. Keep the window open until it says the installation is ready. / 第一次双击 `Install JobFlow.cmd`，窗口提示安装完成前不要关闭。
3. Double-click `Start JobFlow Demo.cmd`. / 双击 `Start JobFlow Demo.cmd`。
4. In the browser, use “View AI and conflict review” and “View pending application.” Open the fictional packet to inspect its field-by-field redacted prefill proposal, then approve it. / 在浏览器中点击“查看 AI 与冲突审阅”和“查看待审批申请”；打开虚构审阅包，检查逐字段脱敏预填提案，然后批准。
5. In “Automatic application execution status,” confirm and run the synthetic rehearsal. It stops before the fake final submission. Use the separate final confirmation to create the verified fake receipt. / 在“自动投递执行状态”中确认并运行合成演练；它会停在假的最终提交前，再通过独立最终确认生成经验证的假回执。
6. Close the launch window or press `Ctrl+C`. The demo server, port, database, DPAPI directory and synthetic ciphertext are removed. / 关闭启动窗口或按 `Ctrl+C`；演示服务、端口、数据库、DPAPI 目录和合成密文都会清理。

The demo rejects file intake and real AI connections at the server boundary. Do not type real personal information into it. Its “submission” and receipt are built-in fictional adapters: it never opens a recruiting site, uploads a file, submits a real application, sends a message, creates an account, or starts a scheduler.

演示模式在服务端拒绝文件接入和真实 AI 连接。请勿在演示中填写真实个人信息。“提交”和回执均来自内置假适配器；它不会打开招聘网站、上传文件、提交真实申请、发送消息、创建账号或启动定时任务。

## Start the real local workspace / 启动真实本机工作区

After installation, double-click `Start JobFlow.cmd`. The local page opens on `127.0.0.1` with a session token. Private values are encrypted with Windows DPAPI outside the project; ordinary project files keep only opaque `secure-ref` values.

安装后双击 `Start JobFlow.cmd`。页面只在带会话令牌的 `127.0.0.1` 打开。私人值通过 Windows DPAPI 在项目外加密；普通项目文件只保留不透明的 `secure-ref`。

Use the page in this order:

1. Connect an already configured Agent or local model. The page unlocks intake only after a non-private structured capability test passes; a simple connection response is not enough. / 连接已经配置好的 Agent 或本地模型；页面只有在不含私人资料的结构化能力测试通过后才开放接入，简单连接响应不算完成。
2. Add a resume, project material, portfolio file, AI summary, or ChatGPT official export. PDF text quality is checked locally before the AI receives it. / 添加简历、项目材料、作品集文件、AI 总结或 ChatGPT 官方导出；PDF 会先在本机检查提取质量。
3. Answer the 25 required one-time questions, including explicit unknown or prefer-not-to-answer choices; GitHub and portfolio URLs are optional. / 一次完成 25 个必答问题；可明确选择未知或不愿披露，GitHub 与作品集链接为可选项。
4. Review every AI-proposed entity, Claim and conflict. Nothing becomes externally approved automatically. / 审阅每个 AI 候选实体、Claim 与冲突；系统不会自动批准对外使用。
5. Complete the local profile and review future offline application packets in the bounded queue. / 完成本机资料，并在有上限的队列中审阅后续离线申请包。

After the Application readiness panel has no blockers, enter one instruction such as **Find and handle the best matching credit-risk role in New York**. A known official-company job URL is optional. JobFlow performs a visible browser search, asks the connected AI to rank only the supplied redacted result references, verifies the selected company job page, follows its company-published Apply URL, reads the sanitized form structure, prepares the job-specific materials, and opens one encrypted review packet. Ambiguous results stop for a redacted choice instead of guessing.

“自动投递准备度”没有阻挡项后，只需输入一次求职目标，例如“在纽约寻找并处理最匹配的信用风险岗位”；已有公司官网岗位链接时也可直接提供。JobFlow 会在可见浏览器中搜索，仅让已连接 AI 对脱敏候选编号排序，再核验公司官网岗位、跟随官网公布的 Apply 网址、读取脱敏申请表结构、准备岗位材料并打开唯一加密审阅包。结果不唯一时只展示脱敏候选，绝不猜测。

The former three-file JD/official-page/form workflow is still available under **Advanced diagnostics and offline QA** for development or when guided browser capture cannot be used; it is not part of the normal path. / 原来的 JD、官网页、申请表三文件流程只保留在“高级诊断与离线 QA”，用于开发或浏览器读取不可用时的排障，不属于普通流程。

When PDF extraction changes only numeric presentation or an obvious sentence wrap, the Claim preview labels that adjustment and still leaves the Claim unchecked for the user. A candidate with new, calculated, rounded, scaled or unrelated-line numbers is filtered and never becomes a Claim, while the readable source can still be securely retained for user review. / PDF 仅出现数字显示格式或明显同句换行差异时，预览会标出调整且 Claim 仍保持未选择；包含新增、计算、四舍五入、单位缩放或无关行数字的候选会被过滤且绝不进入 Claim，但可读原文件仍可安全保留供用户审阅。

For each offline application packet, JobFlow derives a job-specific DOCX/PDF from the same approved Master Resume and leaves the master unchanged. For a normal DOCX without JobFlow placeholders, first use the readiness panel to review AI-mapped original paragraphs and approve the exact positions that may be tailored; the encrypted manifest stores hashes and structure rather than paragraph text. A Cover Letter is generated only when the saved form contains a matching upload field. Confirmed GitHub/portfolio URLs and an encrypted portfolio file are bound only when corresponding fields exist; the ordinary packet shows hashes/statuses instead of the URL value or file body. / 每份离线申请包都从同一份已批准 Master Resume 派生岗位版 DOCX/PDF，母版保持不变。普通 DOCX 无需手工添加占位符：先在准备度面板审阅 AI 映射的原段落，并一次性批准可改写位置；加密清单只保存哈希和结构，不保存段落原文。只有保存的表单含对应字段时才生成求职信或绑定 GitHub/作品集链接及加密作品集文件，普通审阅包只显示哈希与状态。

Run `Install JobFlow.cmd` once. It installs the local runtime, prepares the Local AppData Browser Companion, and opens the browser's extension page and runtime folder. The browser still requires the user to choose **Load unpacked** once; first automation may also require one explicit `J` click to grant Search/HTTPS access. Those browser security gestures cannot be bypassed. After the single packet approval, the already-bound application tab continues automatically: the extension reclassifies every page, fills only approved values, attaches matching approved materials, and advances only through freshly verified, explicitly non-final Next/Continue controls. A submit-like Next/Continue must be clicked by the user, but JobFlow automatically resumes on the new stable page without another `J` click. Login, CAPTCHA, MFA, legal/signature, sensitive, and unknown gates pause for the user. JobFlow stops at `AWAITING_USER_SUBMIT`; only the user clicks final Submit, and ambiguous outcomes are never retried.

第一次只需运行 `Install JobFlow.cmd`。它会安装本机运行环境、准备 Local AppData 浏览器伴侣，并打开扩展页和运行目录。浏览器仍要求用户一次选择“加载解压缩的扩展”；首次自动化还可能要求打开一次 `J` 并批准 Search/HTTPS 权限，这些浏览器安全动作不能绕过。唯一审阅包批准后，刚才绑定的申请标签页会自动继续：扩展逐页重新分类，只填写获批值、挂载匹配材料，并且只通过每页重新核验的明确非最终 Next/Continue。提交型 Next/Continue 仍由用户亲自点击，但新页面稳定后 JobFlow 会自动恢复，无需再次打开 `J`。登录、CAPTCHA、MFA、法律/签名、敏感或未知问题会暂停交还用户。JobFlow 停在 `AWAITING_USER_SUBMIT`，最终 Submit 只能由用户点击；结果不明确时绝不自动重试。

Login, existing-account checks, CAPTCHA, and MFA are user handoffs: complete them yourself and resume from the companion; JobFlow never reads credentials or bypasses verification. Cross-origin forms, account creation, final Submit, automatic retry, email, recruiter contact, and scheduling remain unavailable. A local approval is not a submission. / 登录、已有账号验证、CAPTCHA 与 MFA 会交给用户亲自完成后再恢复；JobFlow 不读取凭据也不绕过验证。跨域表单、账号创建、最终 Submit、自动重试、邮件、招聘者联系与定时任务仍不可运行；批准本机审阅包不等于已经投递。

## Saved careers pages / 已保存招聘页

In the local dashboard, **Parse a saved company careers page / 解析已保存的公司招聘页** accepts a saved UTF-8 `.html`, `.htm`, or JobFlow page-snapshot `.json` file up to 32 MB. Enter the matching official company domain and original careers-page HTTPS URL. JobFlow parses the selected file in memory, does not execute its scripts, does not retain the snapshot, and does not add results to the application queue. Results are offline candidates only and still require separately authorized live freshness and route verification.

在本地主界面中，“解析已保存的公司招聘页”可读取不超过 32 MB 的 UTF-8 `.html`、`.htm` 或 JobFlow 页面快照 `.json`。请同时填写相符的公司官网域名和原始招聘页 HTTPS 地址。JobFlow 只在内存中解析所选文件，不执行其中的脚本、不保留快照、不把结果加入申请队列。解析结果只是离线候选；以后如需实时复验，仍须取得单独授权。

## If the page says `Failed to fetch` / 页面显示 `Failed to fetch`

1. Close the stale browser tab. / 关闭旧浏览器标签页。
2. Double-click `Check JobFlow.cmd`. / 双击 `Check JobFlow.cmd`。
3. Follow the first failed bilingual check. Usually this means running `Install JobFlow.cmd` again or restoring an incomplete extracted package. / 按第一条失败项的中英文提示操作；通常只需重新安装或重新解压完整源码包。
4. When all checks pass, double-click `Start JobFlow.cmd` and use the newly opened page. Old session URLs intentionally stop working after restart. / 全部通过后重新启动并使用新页面；旧会话链接在重启后按设计失效。

The health check does not read private values, enumerate private files, connect to a network, or print the project path.

一键自检不会读取私人值、枚举私人文件、联网或显示项目路径。

## Windows versus WSL / Windows 与 WSL

Run JobFlow itself from Windows by double-clicking the `.cmd` files. PowerShell syntax such as `$env:USERPROFILE` does not work in an Ubuntu shell. WSL is used only when JobFlow detects an already configured WSL Agent or local model; you do not need to move the project into WSL.

JobFlow 本身应从 Windows 双击 `.cmd` 文件运行。`$env:USERPROFILE` 之类的 PowerShell 写法不能在 Ubuntu 终端执行。只有自动检测已配置的 WSL Agent 或本地模型时才会使用 WSL；无需把项目移动到 WSL。

## Before reporting a problem / 报告问题前

- Reproduce with the synthetic demo whenever possible. / 尽量使用合成演示复现。
- Never attach a real resume, export, Candidate Profile, Answer Bank, database, DPAPI file, token, absolute path, or private screenshot to a public issue. / 不要在公开 Issue 附上真实简历、导出、Candidate Profile、Answer Bank、数据库、DPAPI 文件、令牌、绝对路径或私人截图。
- Include the non-sensitive JobFlow version shown by the health check (or `jobflow --version`), first failed health-check ID, expected behavior and actual behavior. / 提供一键自检（或 `jobflow --version`）显示的非敏感版本、第一条失败检查 ID、预期行为和实际行为。

See [Security / 安全](../SECURITY.md) for private vulnerability reporting rules.
