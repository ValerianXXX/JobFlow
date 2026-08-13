# Security / 安全

## Supported version / 支持版本

Security fixes currently target the latest commit on `main`. JobFlow is alpha software and has not enabled real recruiting-site actions.

安全修复目前只面向 `main` 的最新提交。JobFlow 仍处于 Alpha 阶段，真实招聘网站动作尚未启用。

## Report a vulnerability / 报告漏洞

Use GitHub private vulnerability reporting after it is enabled for the repository. Do not open a public issue containing a resume, Candidate Profile, Answer Bank, Claim evidence, API key, cookie, token, absolute user path, database, DPAPI ciphertext or screenshot with personal data. Before the repository exists, keep the report local and provide only a redacted reproduction summary to the maintainer.

仓库启用 GitHub 私密漏洞报告后，请通过该入口报告。不要在公开 Issue 中放入简历、Candidate Profile、Answer Bank、Claim 证据、API Key、Cookie、Token、用户绝对路径、数据库、DPAPI 密文或含个人信息的截图。仓库建立前，请先在本地保留报告，只向维护者提供脱敏复现摘要。

Include the affected version, a minimal synthetic reproduction, expected and actual behavior, and whether any private or external-action boundary was crossed. Never attach real applicant data.

请提供受影响版本、最小化合成复现、预期与实际行为，以及是否越过私人数据或外部动作边界；切勿附上真实求职者资料。

## Security boundaries / 安全边界

- Private values must stay behind Windows DPAPI `secure-ref` values outside the project tree.
- Knowledge bases are read-only and must remain unchanged.
- Real HTTP, browser modification, upload, submit, account, email, recruiter contact and scheduler transports are closed.
- Every personal Claim remains unapproved until an explicit human decision.
- CAPTCHA, MFA, OTP, login and account creation always stop the workflow.

- 私人值必须通过项目目录外的 Windows DPAPI `secure-ref` 保存。
- 知识库只读且必须保持不变。
- 真实 HTTP、浏览器修改、上传、提交、账号、邮件、招聘者联系与定时器传输均关闭。
- 每条个人 Claim 在人工明确决定前均不得视为已批准。
- CAPTCHA、MFA、验证码、登录与账号创建始终停止流程。
