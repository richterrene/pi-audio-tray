# UPnP feature review · 26 October 2026

Scope: 0.6 network-source discovery, storage, browsing, enqueue, desktop window,
helper installation and packaging. This is a maintainer review, not an independent
audit. Existing MPD, Rygel, Python/Expat and media-decoder internals are outside it.

## Checks and evidence

- 111 tests pass on Linux, including endpoint escape/credentials/control-character
  rejection, private-address enforcement, redirect rejection, XML DTD/entity and
  UTF-16 bypass rejection, XML size limits, SOAP escaping, source enable/expiry,
  disabled-source rejection, album bounds, queue rollback, private registry files,
  stale connection replies/commands and hidden-window behavior. Two Windows-only
  tests are skipped on Linux; native desktop builds run their platform tests too.
- Live Pi test: discover a Rygel ContentDirectory v3 server, browse its Music and
  Album containers with paging, add a track to real MPD with title/artist tags,
  then delete only the inserted queue ID. Existing playback state and queue length
  were preserved. This did not start playback or constitute a listening test.
- Root-owned deployed bridge/module hashes matched the reviewed sources; bridge
  discovery over verified SSH succeeded without restarting audio services.
- GitHub dependency audit: zero known advisories in the recorded Linux dependency
  set and exact Windows build set. No new dependency was introduced.

## Static findings reviewed

Bandit B405/B314 identify ElementTree and parsing untrusted XML. The shared parser
caps input at 1 MiB, requires UTF-8, rejects NUL/UTF-16/32 and DTD/entity declarations
before invoking ElementTree. The same checks apply to embedded DIDL-Lite XML.
Tests include an entity document encoded as UTF-16. These findings remain visible;
they are not suppressed or represented as an independent parser security proof.

Bandit B110 identifies best-effort deletion of newly inserted MPD IDs after an
enqueue/tagging failure. The original operation error is re-raised. Cleanup must
not mask it or clear someone else's queue. A connection loss may prevent complete
rollback, and the documented guarantee is limited accordingly.

## Boundaries and limitations

UPnP discovery is unauthenticated and enabled media servers are trusted. Only
same-address numeric private IPv4 HTTP(S) resources are accepted during browsing;
MPD's later HTTP transport may follow redirects. This does not sandbox MPD or
protect against a hostile enabled server. Removing a source does not revoke
queued URLs. See [UPnP security](../SECURITY.md#upnp-network-music) and
[compatibility and usage](LOCAL_MUSIC.md#upnp--dlna-music-servers-06).

Phone control uses the existing authenticated HTTPS/myMPD player. UPnP source
browsing is desktop-only. Native Windows/macOS interactive network and audio
testing is not claimed by successful packaging or unit tests.
