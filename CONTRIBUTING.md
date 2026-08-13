# Contributing to JobFlow / 参与 JobFlow

JobFlow welcomes small, reviewable changes that preserve its local-first privacy and truthfulness guarantees. The project is Windows-first because private storage uses DPAPI; synthetic and platform-independent units may also run elsewhere when explicitly supported.

JobFlow 欢迎小而可审阅的改动，但必须保持本地优先、隐私与真实性约束。由于私人存储使用 DPAPI，项目以 Windows 为主；只有明确标注的平台无关与合成测试才可在其他系统运行。

## Development / 开发

1. Create a branch from the latest `main`.
2. Install with `Install JobFlow.cmd` or `scripts/install-jobflow.ps1`.
3. Use only synthetic fixtures under `tests/fixtures`; never commit a real resume, export, profile, answer, account identifier or saved recruiting page containing user data.
4. Run `python -m unittest discover -s tests -v` and `python -m jobops.public_release` from the installed project environment.
5. Keep every change independently testable and explain its privacy and external-action impact in the pull request.

1. 从最新 `main` 建立分支。
2. 使用 `Install JobFlow.cmd` 或 `scripts/install-jobflow.ps1` 安装。
3. 只使用 `tests/fixtures` 中的合成测试件；不得提交真实简历、导出资料、个人档案、答案、账号标识或含用户数据的招聘页面。
4. 在已安装的项目环境中运行 `python -m unittest discover -s tests -v` 与 `python -m jobops.public_release`。
5. 每项改动都应可独立测试，并在 Pull Request 中说明隐私与外部动作影响。

## Required pull-request checks / PR 必查项

- Tests and strict runtime Schemas pass.
- Public current-tree and full-history scans report zero content findings.
- Knowledge fingerprints are unchanged and write operations are 0.
- Private staging residue and plaintext leakage are 0.
- Real external actions are 0.
- Chinese and English UI behavior remains equivalent for user-facing changes.
- New AI output is provenance-bound, fail-closed and never auto-approved.

Do not weaken a stop gate to make a test pass. Do not add a live adapter, website access, telemetry, analytics or background scheduling in an ordinary pull request.

不得为了让测试通过而放宽停止门。普通 PR 不得加入真实站点适配器、网站访问、遥测、分析统计或后台调度。

## Commit identity / 提交身份

The default public-release policy requires a GitHub noreply identity. A maintainer may deliberately change `config/public-release.json` to `PUBLIC_EMAIL_APPROVED` only after deciding that the author email may be public. The scanner never prints identity values.

默认发布策略要求使用 GitHub noreply 身份。只有维护者明确决定作者邮箱可以公开后，才可把 `config/public-release.json` 改为 `PUBLIC_EMAIL_APPROVED`；扫描器不会输出身份原值。
