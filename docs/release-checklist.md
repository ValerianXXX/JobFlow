# Public release checklist / 正式发布清单

Run the following only on the final clean commit. Nothing in this checklist authorizes an upload or a real recruiting action.

只在最终干净提交上执行以下清单。本清单不授权上传，也不授权任何真实招聘动作。

## Automated gates / 自动门禁

- [ ] `python .agents/skills/job-application-operator/scripts/run-release-verification.py` passes all tests, Schemas, leakage, knowledge and external-action checks.
- [ ] `python -m jobops.public_release` reports 0 current-tree findings and 0 full-history findings.
- [ ] `python -m jobops.release_candidate` builds two identical archives and passes the isolated local UI smoke.
- [ ] `python -m jobops.release_readiness` has no blockers.

## Human decisions / 人工决定

- [ ] Confirm whether the existing Git author identity may be public; prefer a GitHub noreply identity.
- [ ] Freeze the exact commit and run fresh independent QA on a clean copy.
- [ ] Review sanitized Chinese and English screenshots; confirm they contain no personal data or user paths.
- [ ] Confirm repository owner, name, description, topics, visibility and private vulnerability reporting.
- [ ] Create an annotated or signed `v0.1.0` tag only after all evidence is fresh.
- [ ] Obtain explicit user authorization before creating a GitHub repository, pushing, uploading a candidate or publishing a Release.

## Release facts / 发布事实

- The source ZIP is the complete Windows application candidate. The wheel is a CI code-build smoke artifact, not the standalone desktop distribution.
- `0.1.0` is an Alpha release candidate and does not claim live ATS compatibility.
- Real external actions must remain 0 in every published report and artifact.
