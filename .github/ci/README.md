# Browser CI image

The Dockerfile in this folder is the source of truth for the Linux browser E2E
environment. Git tracks the small definition; GitHub Container Registry (GHCR)
stores the actual image. **Do not commit image tarballs or browser binaries.**

The `browser-image` job in `ci.yml` computes a tag from this Dockerfile and
`requirements.txt`/`requirements-dev.txt`, checks GHCR, and only builds and
pushes a missing image. All browser matrix jobs wait for that single preparation
job and then run in parallel with Python 3.13, Node.js 22, Playwright 1.57.0,
Chromium and WebKit preinstalled. The image's Python/Node dependency versions
must change alongside their inputs; changing those files rotates the tag.

Registry package: `ghcr.io/shm-white/xtyf-manager-ci-browser`.
The publishing job uses only `GITHUB_TOKEN` with `packages: write` and runs
only for branches within this repository, never fork pull requests. The image
includes an OCI source label linking it to this repository. The browser jobs
authenticate with their read-only token to pull a private package. If GHCR
reports package-access errors, grant this repository Actions read/write access
under the container package's settings. No personal access token is needed.

For external/fork PRs, browser jobs fall back to Microsoft's Playwright image
and install Python/JS project dependencies without publishing anything. This
still avoids downloading browsers from scratch.

Local reproduction after the image has been published:

```sh
docker run --rm -it --init -v "$PWD:/work" -w /work \
  ghcr.io/shm-white/xtyf-manager-ci-browser:<content-hash> \
  bash -lc 'ln -s /opt/xtyf-node/node_modules node_modules && node tests/browser/assets.cjs'
```

The browser test script starts its own temporary test server. Replace the tag
with the content-hash tag shown in the `Resolve CI image tag` Actions step.
On an existing working tree, remove the `node_modules` symlink after testing.
