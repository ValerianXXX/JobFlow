# Public release checklist / 正式发布清单

Run the following only on the final clean commit. Nothing in this checklist authorizes an upload or a real recruiting action.

只在最终干净提交上执行以下清单。本清单不授权上传，也不授权任何真实招聘动作。

Double-click `Check Release Readiness.cmd` at any time for a bilingual, local-only list of remaining gates. A `BLOCKED` result is expected until every automated check and human decision below is genuinely complete. The checker does not create a repository or tag and performs no upload or network action.

可随时双击 `Check Release Readiness.cmd` 查看中英双语的本地剩余门禁。只有下列自动检查与人工决定真实完成后才会变为 `PASS`；此前显示 `BLOCKED` 是正常安全状态。该检查不会创建仓库或标签，也不会上传或联网。

## Automated gates / 自动门禁

- [ ] `python .agents/skills/job-application-operator/scripts/run-release-verification.py` passes all tests, Schemas, leakage, knowledge and external-action checks.
- [ ] `python -m jobops.public_release` reports 0 current-tree findings and 0 full-history findings.
- [ ] `python -m jobops.release_candidate` builds two identical archives and passes the isolated local UI smoke.
- [ ] `Start JobFlow Demo.cmd` opens the temporary synthetic-only tour; file intake and real AI connection remain blocked.
- [ ] `python -m jobops.release_readiness` has no blockers.

## Human decisions / 人工决定

- [ ] Review `config/github-release.json`; set a gate to `true` only after the named decision or evidence actually exists. / 审阅该配置；只有对应决定或证据真实存在后才可设为 `true`。
- [ ] Confirm whether the existing Git author identity may be public; prefer a GitHub noreply identity.
- [ ] Freeze the exact commit and run fresh independent QA on a clean copy.
- [ ] Review sanitized Chinese and English screenshots; confirm they contain no personal data or user paths.
- [ ] Confirm repository owner, name, description, topics, visibility and private vulnerability reporting.
- [ ] Create an annotated or signed `v0.2.0` tag only after all evidence is fresh.
- [ ] Obtain explicit user authorization before creating a GitHub repository, pushing, uploading a candidate or publishing a Release.

## Release facts / 发布事实

- The source ZIP is the complete Windows application candidate. The wheel is a CI code-build smoke artifact, not the standalone desktop distribution.
- `0.2.0` is an Alpha release candidate and does not claim blanket live ATS compatibility.
- Real external actions must remain 0 throughout build, test and release QA. The product claim is limited to separately user-authorized, user-present Browser Companion sessions on bound company/Greenhouse/Lever/Workday origins: per-page inspection, approved fill/material attachment, and one-use non-final Next/Continue navigation. Final Submit and automatic retry remain absent.
