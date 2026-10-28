# Architecture and contribution map

The desktop controls one selected Pi over verified SSH. Pi audio playback and the
optional HTTPS player keep running independently of the desktop app.

| Area | Main files | Boundary |
| --- | --- | --- |
| Main window, tray and lifecycle | `app.py` | UI-thread state, operation guards and connection generation |
| SSH and validation | `core.py` | Fixed commands, bounded stdin/output, strict host-key trust; public config facade |
| Named connections | `profiles.py` | Private validated registry, legacy migration, one active profile |
| Desktop integration | `platform_support.py` | Private files/Windows ACLs, instance lock, opt-in login autostart |
| Presentation and language | `ui_style.py`, `i18n.py`, `translations/` | Fixed-size status, bounded menus, data-only translations and flags |
| Local/network music UI | `music_ui.py`, `network_ui.py` | Serialized requests, read coalescing, explicit command delivery |
| Fresh Pi wizard | `setup_ui.py`, `setup_plan.py`, `provision.py` | Read-only probe, explicit plan, masked sudo input, fixed staged file manifest |
| Receiver status/pairing | `pi_helper.py` | Allowlisted health/stat requests and bounded pairing window |
| Receiver music | `pi_music.py`, `pi_upnp.py` | MPD Unix socket; validated media paths and opt-in pinned LAN sources |
| Receiver services/installers | `pi/` | Root-owned deployment, updates, conditional reboot, TLS/cookie login |
| Packaging | `packaging_support.py`, `build_*.py` | Neutral staging, pinned runtime verification, native build/smoke tests |
| Evidence | `tests/`, `docs/*REVIEW*`, `docs/testing/` | Regression checks and explicit validation limits |

## Connection identity and UI state

A connection generation changes when the selected receiver or SSH identity
changes. Jobs retain that generation and their original config. Late replies and
unsent commands for another generation are discarded. `connection_key()` names
the SSH fields; language, refresh interval and web URL are presentation/settings
changes, so they do not erase the library or discard accepted playback controls.

Music/network reads may coalesce or be canceled when a view hides. A playback or
source-management command already accepted from the visible UI is preserved and
can finish in the tray. Switching receivers cancels unsent old-connection work;
already-sent work can finish on the original Pi. Quitting the whole app ends its
lifecycle; it is different from hiding a window. Root setup cannot be interrupted
through the app's Quit/close controls, although OS/process termination remains
outside that guarantee.

## Polling and menus

Only the visible, non-minimized status window requests CPU/thermal/RAM/disk/load
statistics at the configured interval. A hidden app uses health-only polls at
least a minute apart. The compact tray menu requests a fresh health snapshot on
open and shows Pi, Spotify, Bluetooth and pairing status; it does not enable
continuous detailed sampling. System metrics remain in the main window.

Maintenance and profile choices are submenus. Qt-rendered menus use the pointer's
monitor work area and [Qt's scrollable-menu style hint](https://doc.qt.io/qt-6/qstyle.html#StyleHint-enum)
to constrain large menus. Native OS-exported menus retain OS rendering/positioning;
the compact root also reduces their height. Tooltips escape markup and retain
full text when a long label cannot fit.

## Storage and protocols

Do not turn user data into shell commands or translation keys. Device names,
track metadata and profile names are untrusted display values. Settings and
host trust live outside the repository. The selected profile registry is
authoritative; the legacy config file is a compatibility export. First migration
establishes the previous registry state before exporting a new connection, so a
failed save cannot silently select the new receiver on restart.

Protocol action names, schema keys, systemd unit names and installer paths remain
stable English identifiers. UI text uses `tr` with named placeholders. Add
capabilities through explicit bounded request/response validation, not arbitrary
remote commands, URLs or plugins. Privileged helper changes require reviewing
both the caller and exact sudo rules.

## Deliberate limits and next refactors

The project uses small modules and dictionaries rather than a plugin framework.
`app.py` still combines presentation with the main controller, and `core.py`
still combines config primitives and SSH; these are the main candidates for
incremental extraction when a feature needs it. Avoid a broad rewrite solely to
introduce abstraction. Keep public helpers and protocol compatibility stable,
use the connection/scheduler regressions, and document any new module in shared
package staging and the security scan.

The suite does not prove every Pi/DAC, native shell menu or desktop credential
agent combination. Read the platform/receiver guides before claiming new
compatibility. Branch protection, independent review and native hardware coverage
are process improvements rather than properties of unit-test counts.
