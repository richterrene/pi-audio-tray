# Desktop interaction and code review — 2026-10-29

This review covers the desktop UI on top of the optional web-shutdown work. It
adds no receiver permissions or new features. It uses isolated settings, mocked
SSH responses, actual Qt widgets/event loops and real worker threads; it never
plays audio, installs services or shuts down a receiver.

## Findings and fixes

| Finding | Result |
| --- | --- |
| Return in a music list could activate a track and the dialog's default Previous button. Other lists lacked keyboard activation. | Shared `ActivatedListWidget` consumes Return/Enter and emits exactly one row activation. Library, queue, UPnP and profile lists use it. Mouse activation stays connected through Qt's item-activation signal. |
| UPnP server/folder/page navigation left previous items clickable until its response arrived. An old object ID could be sent to the newly selected server. | Clear click targets immediately when navigation context changes; reject late responses using the existing context/generation checks. Same-page refresh preserves selection and scroll. |
| Local-library/page navigation similarly retained stale click targets. Queue Up silently changed the hidden library path. | Clear only changed page contexts and ignore Up on the queue tab. Background refresh retains existing same-page identity/selection. |
| MPD's volume input sent a write on every editingFinished signal, including focus changes without an edit. Its initial value could be sent before reading the Pi's volume. | Require an actual pending edit; block change signals during status updates/reset and enable the field only after a valid volume arrives. An uncommitted edit survives background reads. |
| Failed EQ/policy/update refreshes retained an old snapshot and re-enabled actions based on stale capabilities. | Invalidate the maintenance snapshot on failure; Refresh and Close remain available. A successful retry restores controls. |
| Nested component/setup dialogs could start a deferred read after exec returned. | Shared `exec_dialog` cancels the initial callback on normal return or exception and schedules dialog disposal. |
| Settings, profile/setup dialogs, repeated message boxes and pairing confirmations accumulated as hidden children. | Dispose of one-shot modal dialogs consistently and delete completed asynchronous pairing prompts. Persistent music/network windows remain reusable. |
| The setup constructor could fail after the global operation guard was acquired, permanently blocking later operations. | Construct inside the existing try/finally that releases the guard and resumes polling. |
| Closing setup retained an unused administrator password; input prompts retained canceled/accepted key text. | Clear setup's password on all permitted close paths and clear/dispose text prompts. This reduces lifetime; Python/Qt do not guarantee secure memory erasure. |
| Saving invalid connection settings closed the editor before validation. | Validate before accepting, keeping the user's edits visible for correction. Persistence still validates independently. |
| Setup/component host-trust persistence failures could escape a Qt callback. | Show the error in the existing plain-text log without silently trusting the host or leaving the UI broken. |

## Interaction coverage

| Surface | Exercised behavior |
| --- | --- |
| Status/tray | Hide to tray; no-tray quit guard; hidden/minimized light polling; visible detailed polling; compact menu bounds; stable update indicator geometry. |
| Settings/profiles | Cancel/save; validation retry; autostart errors; language notification; switching Pi during late replies; mutation/pairing guards; repeated-dialog disposal; Enter to select a profile. |
| Setup/components | Additive component selection/dependencies; plan validation and canceled confirmation; constructor failure; late initial reads; host-trust persistence failure; password clearing and busy-operation close guards. |
| AirPlay | Initial control gating; status failure; unsaved name preservation; canceled installation; enable worker lifecycle; protected installer close behavior. |
| EQ | Local presets/custom edits; explicit Apply; absent/unsupported helper state; worker lifecycle; failed-refresh recovery and repeat opening. |
| Source policy | Confirmed write, worker completion, close guards and failed-refresh recovery. |
| System updates | Cached/fresh distinction, operation followed by inventory recheck, failure recovery, close guards and existing update-policy boundaries. |
| Master volume | Debounce/coalescing; volume plus mute; queued writes drain before button/X/Escape closure; failed reads release guards. |
| Local music/queue | Background reads preserve responsiveness, selection and pending edits; accepted commands survive hiding; obsolete reads coalesce; old Pi/folder replies are ignored; Enter sends only the intended track; navigation clears stale targets. |
| UPnP | Hidden views do not poll; accepted playback survives hiding; connection reset; page context; server switch cannot reuse old track IDs; same-page selection; keyboard source activation. |
| Pairing/power | Stale/canceled pairing approval is rejected; confirmations default to No; shutdown captures the confirmed connection; receiver switch/update/pairing guards; request is distinguished from verified power-off. |

`tests/test_ui_interactions.py` adds 20 regression tests, several parameterized
across dialogs/close paths, alongside the existing lifecycle/security/scheduler
suite. Reproduced failures preceded the corresponding fixes. A prior polling
regression now establishes an initial valid volume before asserting that a later
background refresh keeps the volume input enabled.

Local validation on Ubuntu with Python 3.14 / Qt 6.12:

- Full suite: **320 tests, OK, 3 skips**. Temporary Unix sockets require execution
  outside the restricted sandbox; the first restricted run failed on socket bind,
  then the permitted baseline and final runs passed.
- GUI self-test passed. All 19 catalogs contain all 377 source templates.
- Rendered and visually inspected a contact sheet of 17 fixture-only views,
  including English/German settings, setup/components, playback, maintenance,
  confirmation and tray states. Images are generated with the existing screenshot
  tool without replacing README assets solely for invisible behavior changes.
- No new dependency, protocol action, sudo rule or receiver service change.

## Clean-code assessment and contribution guidance

Keep the existing UI/transport/receiver boundaries. `exec_dialog` owns one-shot
modal lifetime and initial-read cancellation; `ActivatedListWidget` owns the
Return-key propagation rule. Reuse them rather than copying timer/cleanup or
keyboard workarounds. Do not shadow `QDialog.finished(int)` with a method.

Worker lifecycle duplication remains in setup/audio/component/maintenance views:
these have distinct installation, recheck and close policies. A common task
controller could help when another workflow needs it, but a generic base-class
rewrite here would make those safety differences harder to assess. Keep all
privileged operations explicit. Any future controller extraction should retain
these real-QThread regressions and the generation-bound scheduler tests.

Prefer validated snapshots and captured connection configuration over reading
mutable widgets in workers. An invalid snapshot must disable dependent actions;
UI validation complements rather than replaces transport validation. Never add
real network credentials or require a physical Pi for normal GUI regressions.

## Limits and remaining manual coverage

This is broad automated interaction coverage, not proof of every input sequence
on every desktop. Offscreen rendering does not validate native window-manager
focus, OS-exported tray menus, assistive technology, touch or native SSH agents.
Native CI exercises platform runtimes, but is not a human desktop usability test.
The tall setup form finding was subsequently fixed during
[0.10.1 candidate preparation](RELEASE_READINESS.md): a scrollable form, wrapped
choices and pinned identity/progress/Close pass 19-language small-screen tests
at multiple Qt scaling factors. Rendered fixtures still do not establish every
native desktop/translation combination.

No physical audio-source handover, real installation/update, or power-off is
performed by this audit. Those require separate, explicit receiver validation.
The web shutdown was independently confirmed by the user before this review.
