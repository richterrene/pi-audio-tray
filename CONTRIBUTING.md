# Contributing

Bug reports, focused fixes, documentation and translation improvements are
welcome. The project is experimental: keep claims about supported hardware tied
to the evidence in the README. The project uses the MIT license; preserve notices
and review the license of any new dependency, image or font.

## Set up a development environment

Use Python 3.12 or newer; CI uses 3.12. Clone your fork, create a branch and work
inside the repository. No Pi, Spotify key, phone, USB library or sudo access is
needed for normal desktop development or the unit/GUI suite.

Linux/macOS:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip==26.2.0
python -m pip install -r requirements.txt
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
python tools/check_translations.py
```

On Ubuntu, Qt may need `libegl1` and `libopengl0` from the OS package manager.
`apache2-utils` enables password-verifier tests that otherwise skip. The real
D-Bus pairing harness additionally needs OS `python3-dbus`, `python3-gi` and
`dbus-run-session`; see the security workflow. Do not install Pi services or
change your desktop audio system to run tests.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip==26.2.0
.venv\Scripts\python -m pip install -r requirements.txt
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python tools\check_translations.py
```

To test the interactive app, remove the offscreen environment variable
(`unset QT_QPA_PLATFORM` in a POSIX shell, or
`Remove-Item Env:\QT_QPA_PLATFORM` in PowerShell) and run `python app.py --show` from the activated environment. Use an isolated temporary
`XDG_CONFIG_HOME` if you do not want to read or change your installed app's
profiles/host trust. Empty connection settings are supported for UI work. Never
put real settings, keys or screenshots containing private device data in Git.

## Choose the right layer

See [the architecture map](docs/ARCHITECTURE.md) before changing request handling,
configuration, packaging or privileged receiver code. Follow the existing Python
style, use explicit imports and named helpers, and keep a fix focused. Do not
introduce a framework, plugin API or general refactor for a small behavior change.

For a behavior/security bug, add a regression that reproduces the observable
failure, then fix it. Minor copy, icon or documentation edits generally do not
need a new test. Tests should check behavior and safety boundaries rather than mirror
private implementation details. GUI tests use Qt offscreen and mocked SSH;
remote/helper tests use temporary files and fake command runners. Do not make
ordinary tests depend on your home LAN, credentials or physical devices.

Useful focused commands:

```sh
python -m unittest tests.test_music tests.test_upnp -v
python -m unittest tests.test_profiles tests.test_gui_security -v
python -m unittest tests.test_i18n tests.test_layout -v
python tools/check_translations.py
python tools/generate_screenshots.py
```

The screenshot tool renders actual widgets with fictional data, blocks SSH and
uses temporary settings. Inspect changed images before committing them. For
translations, follow [the catalog guide](docs/TRANSLATIONS.md): adding a complete
JSON catalog and a reusable/self-contained SVG flag needs no app language-registry
edit. Keep technical terms, formatting placeholders and safety warnings intact.

## Checks and builds

Pull requests run Linux security checks plus native desktop tests on Linux,
Windows, Apple Silicon macOS and Intel macOS. Fork PRs use read-only permissions
and no Pi credentials or signing keys. GitHub may require maintainer approval for
a first-time contributor's workflow. Fix failures or explain a platform-specific
limitation; a skipped test is not evidence that the behavior works.

Packaged builds run on manual dispatch or version tags, using the same workflow.
They additionally build/smoke-test packages and upload artifacts. They do not
publish a GitHub release. Follow the [README build commands](README.md#build-appimage)
and platform guides for local builds. No macOS cross-build is promised.

Optional virtual ARM tests are separate, heavier checks requiring an existing
Docker installation and downloaded OS images. See [virtual tests](docs/VIRTUAL_TESTS.md).
They do not establish real Bluetooth/audio behavior or a complete physical
fresh-Pi install. Privileged receiver changes need maintainer review and manual
hardware validation; do not run installers against a personal Pi accidentally.

## Open a pull request

Describe the concrete problem, the resulting behavior and how it was verified.
Include relevant UI screenshots, platform limits and any receiver migration or
permission changes. Do not bump a version or publish packages just for a normal
contribution; release preparation belongs in a separate maintainer step.

Keep root commands, remote protocol allowlists, host-key checks and credential
handling easy to review. Follow [SECURITY.md](SECURITY.md) for vulnerabilities;
public issues should contain a minimal redacted reproduction rather than real
keys, passwords, cookies, complete configuration or private logs.

The `main` branch is protected, including for administrators. Changes go through
a pull request that is up to date with `main`, passes the `review`, `linux`,
`windows`, `macos (macos-15, arm64)` and `macos (macos-15-intel, x86_64)` GitHub
Actions checks, and resolves review conversations. Force pushes and branch
deletion are blocked. Squash/rebase merges preserve linear history.

Independent approval is currently optional so the sole maintainer can merge
their own PRs after the checks pass. Obtain a second review for privileged
installer/auth changes when another reviewer is available. This guide does not
grant repository access. Discussions and a formal multi-maintainer/release policy
can be added when needed; they are not prerequisites for a focused first PR.

Receiver audio source builds have a separate native ARM64 workflow. Changes to
its pinned sources, root deployment or timing service need that build/harness in
addition to desktop tests. Source-policy fixtures are not a substitute for
physical cross-source playback tests; document those limits in release notes.
