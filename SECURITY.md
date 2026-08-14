# Security / 安全

## Supported version / 支持版本

Security fixes currently target the latest commit on `main`. JobFlow is alpha software. Its only real recruiting-site capability is a user-present, per-application Browser Companion for same-page guest form inspection, approved fill and approved material attachment; final submit and unattended operation are not implemented.

安全修复目前只面向 `main` 的最新提交。JobFlow 仍处于 Alpha 阶段；唯一真实招聘网站能力是用户在场、逐岗位授权的浏览器伴侣，只能检查同页访客表单、填写获批字段并挂载获批材料。最终提交与无人值守运行未实现。

## Report a vulnerability / 报告漏洞

Use GitHub private vulnerability reporting after it is enabled for the repository. Do not open a public issue containing a resume, Candidate Profile, Answer Bank, Claim evidence, API key, cookie, token, absolute user path, database, DPAPI ciphertext or screenshot with personal data. Before the repository exists, keep the report local and provide only a redacted reproduction summary to the maintainer.

仓库启用 GitHub 私密漏洞报告后，请通过该入口报告。不要在公开 Issue 中放入简历、Candidate Profile、Answer Bank、Claim 证据、API Key、Cookie、Token、用户绝对路径、数据库、DPAPI 密文或含个人信息的截图。仓库建立前，请先在本地保留报告，只向维护者提供脱敏复现摘要。

Include the affected version, a minimal synthetic reproduction, expected and actual behavior, and whether any private or external-action boundary was crossed. Never attach real applicant data.

请提供受影响版本、最小化合成复现、预期与实际行为，以及是否越过私人数据或外部动作边界；切勿附上真实求职者资料。

## Security boundaries / 安全边界

- Private values must stay behind Windows DPAPI `secure-ref` values outside the project tree.
- Knowledge bases are read-only and must remain unchanged.
- The Browser Companion accepts only the fixed extension origin and exact approved HTTPS page. It never reads cookies, tokens, existing input values or the page body; sanitized form structure is revalidated before any fill.
- Real form inspection, approved fill and approved material attachment require a short-lived one-use per-application user-present authorization. Files use one-use localhost byte streams and are never staged in the project.
- Final submit, automatic retry, account, email, recruiter contact and scheduler transports are absent. CAPTCHA, MFA, login, cross-origin forms/iframes, unsupported controls and uncertain results fail closed.
- Every personal Claim remains unapproved until an explicit human decision.
- CAPTCHA, MFA, OTP, login and account creation always stop the workflow.

- 私人值必须通过项目目录外的 Windows DPAPI `secure-ref` 保存。
- 知识库只读且必须保持不变。
- 浏览器伴侣只接受固定扩展来源与精确获批 HTTPS 页面；不读取 Cookie、Token、既有输入值或页面正文，任何填写前都要重新核验脱敏表单结构。
- 真实表单检查、获批字段填写与获批材料挂载必须取得短时、一次性、逐岗位、用户在场授权；文件只通过本机一次性字节流传递，不在项目中暂存。
- 最终提交、自动重试、账号、邮件、招聘者联系与定时器传输均不存在；CAPTCHA、MFA、登录、跨域表单/iframe、未知控件与不确定结果全部失败关闭。
- 每条个人 Claim 在人工明确决定前均不得视为已批准。
- CAPTCHA、MFA、验证码、登录与账号创建始终停止流程。
