# Modernization checkpoint - 27 October 2026

Baseline: `b5cf5e73ed0378374dfe0d97964ef1d3cd8455e0`. Main and existing feature
branches were inspected before editing; those branches contain no commits ahead
of main. No existing PR or uncommitted source change was overwritten.

## Verified release state

- Stable desktop v0.6.0 requires Companion 0.9.1; both official stores serve 0.9.2.
- Its signed schema-1 manifest points to a source archive. The current bootstrap
  requires a complete runtime and schema 2, and correctly rejects that payload.
- v0.7.0 remains a source prerelease. No complete signed stable Windows runtime is
  confirmed. See the [compatibility matrix and recovery](compatibility.md).
- [Run 34006054439](https://github.com/ValerianXXX/JobFlow/actions/runs/34006054439)
  passed regression, browser E2E, tree-boundary and wheel stages before candidate
  construction failed with `PUBLIC_REPOSITORY_IDENTITY_REVIEW_REQUIRED`.
  This was not a business-unit-test failure.

## Changes

Source/package/extension versions now have a machine-checked matrix and CI step.
Pairing rejects malformed versions/protocol types and explains which component
needs updating. Authentication and exact-version checks remain enforced. Source
venv instructions are separated from signed-runtime installation and include
data-preserving recovery guidance.

The identity-policy diagnostic counts only unapproved historical identities.
Explicit `PUBLIC_EMAIL_HASHES_APPROVED` can represent an owner's specific approval
without adding a plaintext personal address to source. Its unique canonical hash
allowlist rejects unknown identities, malformed logs and invalid policies.
**The repository policy remains `NOREPLY_ONLY`; no identity approval was invented.**
Changing the current author cannot authorize an existing author/committer.

Chrome's consumer listing contains old submission/reviewer material. A
[replacement description](store-description.txt) is prepared, not published.
No extension version was changed.

## Validation

- Windows Python 3.11 source environments install; `pip check` passes.
- Targeted compatibility, release policy/readiness/candidate and companion checks:
  75 passed; the subsequently added HTTP mismatch regression brings compatibility
  coverage to 4 passing tests. All 10 public-release identity-policy tests pass.
- JavaScript/browser suites: 8/8 passed on baseline and candidate checkouts.
- Full Python baseline: 1,144 tests, 4 skipped, no failures (3,683 seconds).
  The frozen release candidate ran 1,149 tests, 4 skipped, with two failures
  (3,747 seconds): a README installer-link regression and Windows ACL handling
  of a deep source-archive path. Both are repaired. All six focused documentation
  and deep-path regressions pass after repair; a second full run has not been
  claimed. ACL handling now uses the existing extended-path conversion without
  weakening owner-only permissions or reparse-point checks.
- The synthetic Windows demo started, switched Chinese/English and displayed a
  source-backed claim conflict for review. No real profile was used.
- Existing WSL Hermes completed a real synthetic zero-tool handshake with configured
  `gpt-5.6-sol` / `openai-codex`. This does not establish direct API access,
  document-understanding gains or mailbox integration.

## CI follow-up

[Run 40603996127](https://github.com/ValerianXXX/JobFlow/actions/runs/40603996127)
tested commit `6ec383627a942dd4e9c9e06b36b3c88247f8d871`. Python 3.11 and 3.12
each passed all 1,149 regression tests and all eight browser tests. The 3.13 job
was still advancing through launcher tests when its 120-minute job cap expired;
its full result is **incomplete**, not passed. The cap is now 180 minutes, with
the full interpreter matrix and all assertions retained.

The completed jobs exposed a separate public-tree scanner false positive:
an escaped control-byte separator in a test fixture was consumed as part of a
GitHub noreply address. Normalize only escaped control separators before the
existing email rule; do not allow additional addresses. All 11 focused public
boundary tests pass, including a real private-address rejection case. A local
full tree/history check found zero findings across 526 tracked files, 277
commits and 2,403 unique historical blobs. Author review remains required for
one unapproved non-noreply identity under the unchanged `NOREPLY_ONLY` policy.
These results do not approve that identity or a production release. The next CI
run must separately confirm the scanner fix and complete Python 3.13.

## Runtime preparation and remaining gates

Governed acquisition from pinned official sources produced 21 runtime-input files.
Bundle SHA-256: `fed48832aeb52ea0f80b653374ed3bbd3350a3edd569db0ba02e08b94ef9fff7`.
Tree SHA-256: `96e42c679abf929cbec71b4947a3321c226a858387fa713c05860de6afe20e74`.
The ignored local bundle contains no publisher key.

Complete runtime built from checkpoint `a7af3e57835e771df4d7d163471751fc00f07d34`
using the official CI-pinned MinGit 2.53.0.3, whose archive/executable digest and
Authenticode publisher match the existing trust policy. The host-bundled Git
executable passed its own signature check but its runtime-file closure did not;
the first attempt correctly stopped with `JOBFLOW_RUNTIME_SOURCE_GIT_CLOSURE_UNTRUSTED`.
No trust allowlist was weakened. The isolated pinned Git resolved that mismatch.

`JobFlow-v0.7.0-windows-x64-complete.zip` SHA-256:
`33bf83c1f69ef825fe75deaa81ffe0672ddbad0ca2f04ee7a885e178d3c54a03`.
Two deterministic rebuilds matched, including offline smoke checks. Build evidence
SHA-256: `1547df7ca1707f19b748a6e5b4b77209c3d1bb514864a72e05ff71dc500110b1`.
The builder reports `BUILT_UNATTESTED`, `psf_sigstore_verified=false`,
`outer_signing_ready=false`, and `public_release_blocked=true`. This local artifact
is **not** a production-signed installer and was not uploaded as a release.

The GitHub `jobflow-protected-release` environment exists with reviewer and branch
policy. No environment secret or protected signing workflow was available in the
inspected configuration. Use the existing [release checklist](release-checklist.md),
exact-commit builder, protected-publisher request/response validation and clean
Windows receipt. Do not substitute a developer signature for the production key.

Publication still needs actual historical-identity authorization, protected signing
availability and clean signed-install/upgrade/rollback acceptance. This workstation
can validate source and synthetic integration tests, but is not a clean Windows
signed-install acceptance machine. The actual v0.6.0 retained-data layout must be
checked in an isolated Windows profile. Keep the prior health-checked runtime and
same-account encrypted-data backup; roll back the version pointer on failed health
and test restoration before publication. No production release, installation, data
store or update pointer was changed.

## Run

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
.\.venv\Scripts\python.exe -m jobops.compatibility
.\.venv\Scripts\python.exe -m jobops.cli demo
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Multimodal comparison and a read-only synthetic mail-status adapter are subsequent
work, not delivered by this release checkpoint. Final submission remains a user action.
