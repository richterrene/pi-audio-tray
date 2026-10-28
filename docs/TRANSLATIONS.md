# Desktop translations

In Settings, choose a language by its native name and flag, then Save. Quit the
tray app from its menu and reopen it to apply the change. Closing the status
window with X normally only hides it. No Pi or computer reboot is required.
The language is a preference for this desktop account, shared across Pi profiles;
changing it does not change the Pi OS, Spotify, or the separate myMPD web player.

The initial set contains English, German, French, Spanish, Italian, Portuguese,
Dutch, Polish, Czech, Romanian, Hungarian, Greek, Swedish, Danish, Norwegian
Bokmål, Finnish, Ukrainian, Turkish and Russian. Portuguese initially uses the
Portugal flag. Flags are visual aids for a representative region; they do not
imply that a language is exclusive to a country. Native names and language codes
remain visible so users can identify their language independently of the icon.
The small SVG flag illustrations are repository-owned assets and work without
OS emoji fonts.

## Add or improve a language

1. Copy `translations/en.json` to `translations/LANGUAGE.json`, for example
   `translations/sk.json`.
2. Set `language` to the filename's language code, `name` to its native name,
   and `country` to a two-letter uppercase country code. Codes use two or three
   lowercase letters, optionally a hyphen and uppercase region, such as `pt-BR`.
3. Translate the **values** in `messages`, keeping the English keys unchanged.
   Preserve named placeholders such as `{host}` and `{seconds}`, technical
   commands, paths, product names and safety information. Preserve leading and
   trailing spaces/newlines in sentence fragments. Prefer concise, neutral count
   wording where the source uses `server(s)`; there is no plural-rule API yet.
4. Add a simple self-contained `assets/flags/COUNTRY.svg` (no external references)
   and review its license. Existing flags can be reused. Keep native names short.
5. Run `python tools/check_translations.py` and
   `python -m unittest discover -s tests -v`. The tests automatically check every discovered catalog and flag; no language
   registry or test-code edit is needed for an additional language.
6. Launch the app in that language and check Settings, status, music, profiles,
   setup, confirmations and tooltip text. Check native menu behavior on each
   desktop platform where possible. Submit corrections as a normal pull request.

Catalogs are automatically discovered; adding a language does not require editing
an application language registry. Shared package staging includes all catalogs
and flag assets on Linux, macOS and Windows.

## Add translatable UI text

Wrap fixed desktop UI text with `tr('English source')`. For dynamic values, use
`tr('Connect to {host}', host=hostname)` rather than an f-string inside `tr`.
Device names, track/album metadata and user profile names must remain values,
not translation keys. Use simple named placeholders only; changing formatting
specifiers, conversions, or attribute/index lookup in a translation is refused.

Run `python tools/check_translations.py --update-template` to refresh `en.json`,
then add the new keys to every other catalog. The checker rejects missing or
obsolete entries; runtime uses the source English text for a missing message.
An unknown language or malformed contributed catalog safely falls back to
English. Catalogs are UTF-8 JSON, data only, size-bounded and validated for
placeholders and control characters. Basic standard buttons are translated by
the app; other Qt/native dialogs use the available Qt/OS translations.

## Scope and quality

All 350 extracted desktop templates have entries in each initial language.
Initial translations are machine-assisted, with manual terminology corrections
and German review. Other languages still need native-speaker proofreading;
entry and placeholder checks establish coverage, not linguistic correctness.
Technical messages from SSH, operating systems, remote setup scripts and backend
exceptions may remain English. The web player's localization is managed by
myMPD separately. No translation service, language model, account or internet
connection is needed by the distributed app for localization.
