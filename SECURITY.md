# Security / 安全

## Supported version / 支持版本

Security fixes currently target the latest commit on `main`. JobFlow is alpha software. Its only real recruiting-site capability is a user-present, per-application Browser Companion on a bound company/ATS origin: per-page structure inspection, approved fill/material attachment, and one-use authorization for an explicit non-final Next/Continue control. Final Submit and unattended operation are not implemented.

安全修复目前只面向 `main` 的最新提交。JobFlow 仍处于 Alpha 阶段；唯一真实招聘网站能力是用户在场、逐岗位授权的浏览器伴侣，只能在已绑定公司/ATS 来源逐页检查结构、填写获批字段、挂载获批材料，并以单次授权通过明确的非最终 Next/Continue。最终 Submit 与无人值守运行未实现。

## Report a vulnerability / 报告漏洞

Use GitHub private vulnerability reporting after it is enabled for the repository. Do not open a public issue containing a resume, Candidate Profile, Answer Bank, Claim evidence, API key, cookie, token, absolute user path, database, DPAPI ciphertext or screenshot with personal data. Before the repository exists, keep the report local and provide only a redacted reproduction summary to the maintainer.

仓库启用 GitHub 私密漏洞报告后，请通过该入口报告。不要在公开 Issue 中放入简历、Candidate Profile、Answer Bank、Claim 证据、API Key、Cookie、Token、用户绝对路径、数据库、DPAPI 密文或含个人信息的截图。仓库建立前，请先在本地保留报告，只向维护者提供脱敏复现摘要。

Include the affected version, a minimal synthetic reproduction, expected and actual behavior, and whether any private or external-action boundary was crossed. Never attach real applicant data.

请提供受影响版本、最小化合成复现、预期与实际行为，以及是否越过私人数据或外部动作边界；切勿附上真实求职者资料。

## Security boundaries / 安全边界

- Private values must stay behind Windows DPAPI `secure-ref` values outside the project tree.
- Knowledge bases are read-only and must remain unchanged.
- The Browser Companion accepts only the fixed extension origin and bound HTTPS provider origin. It never reads cookies, tokens, passwords, existing input values or the page body; sanitized form structure is revalidated on every page.
- Inspection, approved fill/material attachment and non-final forward navigation require short-lived, scoped authorization. Each page rotates the action session; files use one-use localhost byte streams and are never staged in the project.
- Final Submit, automatic retry, account creation, email, recruiter contact and scheduler transports are absent. CAPTCHA, MFA and login trigger human handoff; cross-origin forms/iframes, unsupported controls and uncertain results fail closed.
- Every personal Claim remains unapproved until an explicit human decision.
- CAPTCHA, MFA, OTP, login and account creation always stop the workflow.

- 私人值必须通过项目目录外的 Windows DPAPI `secure-ref` 保存。
- 知识库只读且必须保持不变。
- 浏览器伴侣只接受固定扩展来源与已绑定 HTTPS 提供商来源；不读取 Cookie、Token、密码、既有输入值或页面正文，每一页都重新核验脱敏表单结构。
- 表单检查、获批填写/材料挂载与非最终前进都需要短时、限定授权；每页轮换动作会话，文件只通过本机一次性字节流传递，不在项目中暂存。
- 最终 Submit、自动重试、账号创建、邮件、招聘者联系与定时器传输均不存在；CAPTCHA、MFA 与登录进入人工接管，跨域表单/iframe、未知控件与不确定结果失败关闭。
- 每条个人 Claim 在人工明确决定前均不得视为已批准。
- CAPTCHA、MFA、验证码、登录与账号创建始终停止流程。
