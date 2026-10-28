# Desktop, extension and recovery compatibility

Verified on **2026-10-27** against GitHub release metadata, the v0.6.0 manifest, current source and both official store pages. These observations are not release approval.

| Desktop | Extension | Protocol | Distribution | Current bootstrap |
|---|---|---|---|---|
| v0.6.0, latest stable | exactly 0.9.1 | 2 | Legacy source ZIP and schema-v1 signed manifest | Rejected: requires schema v2 and a complete runtime |
| v0.6.0 | store 0.9.2 | 2 | Published extension | Cannot pair; desktop migration required |
| v0.7.0 source | exactly 0.9.2 | 2 | Source prerelease; protected signed runtime pending | No installable stable runtime yet |
| Any other version/protocol pair | Unknown | Unknown | Unverified | Fail closed; review before use |

The [machine-readable matrix](../config/compatibility-matrix.json) is checked with `python -m jobops.compatibility`. It checks Python/package/lockfile versions, extension manifest, backend, UI and popup constants, plus the changelog. `PASS` means source consistency only. It does not validate a downloaded artifact or authorize publication. Exact authenticated pairing remains required; semantic version ordering is used only to explain which component needs updating.

## Recovery

1. If the installed desktop expects an older extension, preserve the installation and data and wait for the matching signed desktop migration. Reinstalling the store extension, spoofing its version or disabling checks cannot repair the mismatch.
2. If the extension is older than the desktop, review the official release notes and update from the official store. Reload the local UI and pair again; old expiring sessions are not migration authority.
3. If the installer reports a missing schema-v2 runtime, nothing is activated. Use the synthetic source demo described in README for evaluation. Source development does not replace the installed application or its data.
4. A failed installed-update health check must restore the previous verified application pointer. The data directory remains separate. Use **Check JobFlow** and **Roll Back JobFlow** for an existing schema-v2 installation; do not manually copy a source tree over an installed runtime.
5. Before any real upgrade, back up the existing data under the same Windows account with JobFlow stopped. DPAPI data is account-bound. Test restoration in the approved acceptance environment. Do not delete data as an installation repair.

## Next release acceptance

The exact candidate commit, complete runtime, signed schema-v2 manifest, detached signature, publisher evidence and clean-Windows observation must agree. Test install, launch, extension pairing in Chrome and Edge, migration from the actual v0.6.0 layout, retained encrypted records, failed-health rollback, manual rollback and a second launch. Run each on synthetic data in an isolated Windows profile. A source demo, unit test or existing developer workstation is not clean-Windows signed-install acceptance.

Both stores currently publish 0.9.2 ([Chrome](https://chromewebstore.google.com/detail/pgcnlkfakkacphkdojdbphccjnbbefic), [Edge](https://microsoftedge.microsoft.com/addons/detail/cebejbohadiofomfiplljnpdefjeiccp)). A future store release must be tested against every supported public desktop before approval, because stores update independently of desktop installations. No extension version was changed by this work.
