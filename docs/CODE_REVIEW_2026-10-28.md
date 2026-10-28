# Code and contribution review — 2026-10-28

This maintainer review follows the 0.7.3 desktop update. It is a code review with
regression tests, not an independent security audit or penetration test.

## Scope and method

Reviewed desktop lifecycle/status rendering, configuration migration, SSH identity
and stale-result handling, music/UPnP request scheduling, page navigation, login
autostart, language discovery, package staging and contributor CI. Rechecked the
bounded SSH/MPD/UPnP caller boundaries and privileged setup/exit assumptions
against the existing security/installer reviews. No Pi helper, sudo permission,
audio service or web-authentication protocol change is required by these fixes.

Findings were checked against current code and converted into focused behavior
regressions. Existing tests for host-key verification, request/path injection,
credential handling, pairing approval, root installers and web-session security
remain part of the full suite. No claim is made that reading code exhaustively
proves every hardware or third-party client behavior.

## Findings fixed

| Finding | Resulting behavior and coverage |
| --- | --- |
| Tray menu repeated all system metrics and could exceed a screen | Four service-status rows remain. Music, pairing, profiles, settings, maintenance and shutdown remain available; full statistics stay in the window. Reusable Qt menus use the active monitor work area and scrolling. Root action-count, submenu bounds/style and geometry regressions cover this. |
| Opening a compact menu still requested detailed CPU/etc. sampling; light replies did not update its service rows | Menu open requests fresh health only. Basic service/pairing rows render before the light/full branch; hidden polling remains at least a minute apart. Tests check both Qt-visible and native exported-menu signal behavior. |
| Language/refresh/web preferences behaved like a receiver change | Named SSH identity fields distinguish receiver/auth changes from presentation preferences. Global preferences can be saved before entering a Pi connection. Preference-only edits retain active views, accepted controls, generation and in-flight health reads. |
| Hiding a music window could silently drop an accepted playback/source command | Hidden views discard pending reads, preserve accepted commands, and start no new periodic discovery/status work. A receiver generation change still discards unsent old-Pi work. Local and network command-delivery regressions cover the distinction. |
| A next-page flag could carry across a tab/folder change | Local page availability is tied to the returned page context; network navigation clears it until the next valid reply. Tests prevent skipping into an unconfirmed page. |
| USB rescan success text implied the asynchronous scan had completed | The UI states that scanning was requested and asks the user to refresh after it finishes. This works with the existing helper; no unsupported scan-completion capability is claimed. |
| Quit/close could bypass the wizard's root-operation close guard | App Quit and a no-tray window close refuse to interrupt provisioning/helper installation. OS termination remains outside this guarantee. Tests exercise both paths. |
| A failed first profile migration could make an unsaved new Pi effective on restart | The previous authoritative registry is established before updating the compatibility export. Fault-injection tests preserve the old selected connection after the second write fails. Existing-registry failures and symlink refusal remain covered. |
| Linux autostart disablement could expose a lower-priority system entry | Effective XDG priority is read and disabling writes a user `Hidden=true` mask when needed. It neither deletes nor modifies the system entry. A priority/disable/re-enable regression covers this. |
| A new language file worked at runtime but an exact test registry rejected additional languages | Tests require the approved base languages while validating every discovered catalog. A contributed language needs data/flag assets, not an app registry/test-code edit. |

Shared connection identity and read-action constants make scheduler intent more
explicit. Tooltip escaping is shared, and status rows open details rather than
appearing as faint disabled text. Basic status rendering is no longer duplicated
in separate response branches. These are small boundary clarifications rather
than a framework rewrite.

## Contribution readiness

The repository is public, MIT-licensed, permits forks and has issues enabled.
Before this review it lacked a contribution guide and PR template, and its native
build workflow was manual/tag-only. The follow-up adds:

- `CONTRIBUTING.md` with isolated setup, focused/headless checks, screenshots,
  language changes, packaging responsibilities and private-data guidance.
- `docs/ARCHITECTURE.md` with module ownership, identity/generation invariants,
  read/command lifecycle, protocols and deliberate limits.
- Small issue and PR templates for reproducible reports and reviewable changes.
- Native PR tests on Linux, Windows and both Mac architectures. PR jobs use
  read-only permissions and no receiver/signing credentials; package creation and
  upload are skipped for PRs and remain manual/tag steps.
- Automatically discovered language catalogs and regenerated fictional UI images.

There are no protected-branch rules as of this review. Enabling required checks,
restricting force pushes and obtaining a second review for privileged/auth changes
are recommendations; repository access and protection policy were not changed.
The project also lacks a formal multi-maintainer release/support policy. That can
be introduced when there are additional maintainers rather than inventing roles
or commitments now.

`app.py` and `core.py` remain the largest mixed-responsibility modules. Future
features should extract a cohesive controller/config/transport boundary when it
helps a concrete change, with existing public helpers/protocols preserved. This
review deliberately avoids a broad rewrite or a premature plugin system.

## Validation and remaining limits

The full local suite passes 190 tests, with three existing environment/platform
skips. All 19 catalogs cover 283 extracted templates with validated formatting
placeholders. Product source was parsed for syntax and accidental duplicate class
methods. Workflow syntax and native platform/package checks are recorded by the
release PR/Actions runs; source inspection alone is not reported as a native pass.

The regenerated Qt tray menu is about 318 logical pixels high with the English
demo data; this computer reports a 1920 × 1080 logical work area. Native shell
exporters retain their own fonts/positioning; compact structure benefits them,
while the explicit screen bounds/scrolling apply to Qt-rendered menus. Native
menu/SSH-agent behavior still needs user validation on other desktops.

Physical Pi/DAC/Bluetooth coverage, a complete fresh-Pi installer run on spare
hardware, macOS Developer ID/notarization, Windows signing and native-speaker
proofreading remain the previously documented limits. Scans cover known
advisories, not every possible dependency flaw. Initial translations and technical
backend messages retain the scope described in `TRANSLATIONS.md`.
