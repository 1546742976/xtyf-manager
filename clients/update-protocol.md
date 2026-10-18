# Client UI update protocol v1

This contract is shared by the Python publisher, Rust desktop and Java Android hosts.
Business API protocol stays **1**. Native bridge version is **2** and capabilities are
`ui_updates` and `image_preview`. Platform keys: `windows-x86_64`, `ubuntu-aarch64`,
`fedora-x86_64`, `android`.

## Signed release

`GET /api/client/updates` is public, no-store. No release: `{ok:true, available:false}`.
Available: `{ok:true, available:true, payload:"BASE64", signature:"BASE64"}`.
Signature is RSA-3072 PKCS#1 v1.5 / SHA-256 over the exact decoded payload bytes.
Pinned SPKI PEM: `clients/update-public-key.pem`; never obtain a trust key from the server.
Private release key stays in ignored `clients/signing/ui-update-private.pem`.

Payload JSON:
```json
{
  "format":1,
  "sequence":1792252800000,
  "bundle":{
    "id":"64 lowercase hex SHA256 of asset-manifest.json",
    "version":"source commit",
    "url":"/api/client/ui/<id>.zip",
    "size":123,
    "sha256":"64 lowercase hex",
    "unpackedSize":456,
    "fileCount":7,
    "protocolMin":1,"protocolMax":1,"bridgeMin":2,
    "minClientVersion":"1.0.10",
    "capabilities":["ui_updates","image_preview"]
  },
  "installers":{}
}
```
Installer entries by platform: `{version, url, sha256}`; only same-server absolute
paths (one leading slash, no credentials, fragment or backslash), no shell execution.
No matching entry means explain unavailable. The signed sequence is a positive safe
integer; lower than highest verified sequence is rejected, same sequence must have
identical payload bytes. Rollback release uses a NEW sequence pointing to old assets.

ZIP contains only files listed in `asset-manifest.json` plus that manifest itself.
Manifest retains `protocolVersion`, `version`, `templates`, `files` (path -> SHA256),
and adds `sizes` (path -> bytes). Reject absolute/parent/backslash paths, duplicate
entries, symlinks, unlisted/missing files. Require index.html, shell.js, bridge.js,
page-runtime.js, templates.js. Bounds: metadata 256 KiB, ZIP 128 MiB, unpacked 256 MiB,
4096 files. Check actual totals as well as declarations. No merging versions.

## Native operations and lifecycle

`get_config` adds `bridgeVersion:2`, `capabilities:[...]`, `platform`.
`check_updates({retry:false})` checks and downloads, coalesces concurrent checks, returns
`{status, currentVersion, pendingVersion, message, installer}`. Status is one of
`current`, `pending`, `unavailable`, `incompatible`, `failed`. `installer` is null or
the verified current-platform entry. `retry:true` allows retry of a quarantined bundle.
`update_status({})` returns current local state without network.
`apply_update({})` activates the validated pending bundle and recreates/reloads the
main view; callers must obtain a user reload confirmation first.
`ui_ready({viewToken})` marks locally rendered UI + bridge initialization healthy (no network
success required). Desktop injects an immutable `window.__XTYF_VIEW_TOKEN__` per view
and rejects acknowledgements from a previous view; Android binds calls to the current
view's message port and accepts a null token. Startup trial watchdog: 15 seconds, then try previous successful
version once, then bundled UI. Persist unconfirmed trial so a crash also rolls back.

Native host owns per-normalized-server state, highest sequence, current/previous/
pending/quarantined IDs and cancellation generation. Downloads stage privately;
fully validate before publishing pending. Pin one complete resource root for each
view lifetime. Retain bundled, current and previous; never remove an active root.
Switching server cancels downloads, clears file handles and loads that server's root.
Only the main trusted local view may call native operations. Remote views have no
business bridge. An explicit installer download uses the existing native save dialog
with `download({path,filename,expectedSha256})`; verify the signed SHA256 before making
the saved file available. This does not launch or install the downloaded package.

`read_selected_image({handle})` returns `{body:"BASE64", mime:"image/png", size:123}`.
Only previously picked current-session handles, max 10 MiB; inspect JPEG/PNG/WebP
magic, reject others. UI decodes original bytes with EXIF orientation and enforces
pixel limits. Multipart upload uses the ORIGINAL handle, not the preview blob.

## Server compatibility

Bootstrap adds `uiCompatibility:{protocolMin:1,protocolMax:1}` and user `avatar_url`.
The shell permits existing protocol 1 servers without this optional field. An
incompatible server disables business calls but leaves native settings/update UI.
Checks: startup, 30 min while foreground, foreground return if last check >5 min,
manual. Download does not reload an active page; cold startup applies pending.
Runtime failures never clear server preferences, sessions, drafts or user files.
