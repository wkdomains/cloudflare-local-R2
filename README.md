# Cloudflare Local R2 Filesystem Facade

Cloudflare R2 is S3-compatible in production, which makes it easy to use tools
like the AWS CLI:

```sh
aws s3 sync ./assets s3://my-bucket/assets \
  --endpoint-url https://<account-id>.r2.cloudflarestorage.com
```

Local Wrangler R2 is different. When you run `wrangler dev`, R2 data is persisted
under `.wrangler/state/v3/r2/`, but that directory is not a normal bucket-shaped
filesystem. Object contents live as opaque blobs, while object keys and metadata
live in Miniflare-managed SQLite databases.

That means this:

```txt
.wrangler/state/v3/r2/wk-prod/blobs/
  4970d99f3e9a9787ef7fcb9db4e29f78a4d417e118c9178943a78d34b4d11dbb0000019df09449f2
  6215a941f34f2a74ff058027ee67ba2ab4f401a3f1ce19592a03cbb39aba04ff0000019df0944af6
  9c5dd5ca92cec997328c613dfc8ae636d422ca1a5faccb0f572e8ef013b00e1c0000019df0944e07
```

is not meant to be edited like this:

```txt
wk-prod/
  blog/2026/may/foo.json
  images/logo.png
  assets/app.css
```

This project exists to provide that missing developer experience.

## Goal

Let developers keep using Wrangler exactly the way they already do:

```sh
cd my-worker
npm run dev
```

Wrangler should continue to own and write local R2 data in:

```txt
./.wrangler/state/v3/r2/
```

Beside that, this tool provides a normal folder on disk:

```txt
~/R2/wk-prod/
  blog/2026/may/foo.json
  images/logo.png
  assets/app.css
```

Files copied into that folder are uploaded into local Wrangler R2. Objects written
by the Worker are mirrored back into the folder. Deleted files are deleted from
local R2.

No Worker code changes. No Wrangler config changes. No moving `.wrangler`.

## Why Not Edit `.wrangler/state/v3/r2` Directly?

Wrangler local R2 is backed by Miniflare persistence internals. In current
Wrangler state, the object table contains fields like:

```txt
key
blob_id
version
size
etag
uploaded
checksums
http_metadata
custom_metadata
```

The `blob_id` points at files in the bucket's `blobs/` directory. The visible
filename is not the R2 object key.

Directly writing those SQLite rows and blob files would be brittle and risky,
especially while `wrangler dev` is running. Cloudflare can change that internal
format, and concurrent writes could corrupt local state.

The safer approach is to let Wrangler own its state and talk to it through the
same local interface the browser explorer uses.

## The Clean Interface: Local Explorer API

When `wrangler dev` is running, Wrangler exposes Local Explorer at:

```txt
http://localhost:8787/cdn-cgi/explorer/
```

The browser UI uses a local API under:

```txt
http://localhost:8787/cdn-cgi/explorer/api
```

For R2 buckets, the useful endpoints are:

```txt
GET    /cdn-cgi/explorer/api/r2/buckets
GET    /cdn-cgi/explorer/api/r2/buckets/{bucket}/objects
GET    /cdn-cgi/explorer/api/r2/buckets/{bucket}/objects/{key}
PUT    /cdn-cgi/explorer/api/r2/buckets/{bucket}/objects/{key}
DELETE /cdn-cgi/explorer/api/r2/buckets/{bucket}/objects
```

That API is enough to build a filesystem facade without touching Wrangler's
private persistence format.

## Proposed UX

First terminal:

```sh
cd my-worker
npm run dev
```

Second terminal:

```sh
r2-local-fs init
r2-local-fs on
```

The init command should discover the running Local Explorer endpoint, list local
R2 buckets, and write a small config file:

```json
{
  "endpoint": "http://localhost:8787",
  "buckets": {
    "wk-prod": "~/R2/wk-prod"
  },
  "localDebounceMs": 300,
  "remotePollMs": 5000,
  "fullScanMs": 60000
}
```

Manual mode should also be supported:

```sh
r2-local-fs watch \
  --endpoint http://localhost:8787 \
  --bucket wk-prod \
  --dir ~/R2/wk-prod
```

## Implementation Language

This project is written in Python.

Python is a good fit because the main jobs are:

- watching a directory tree
- calling Wrangler's Local Explorer API over HTTP
- maintaining a local manifest
- running upload/download/delete queues
- optionally serving a small local S3-compatible HTTP endpoint

The CLI can be distributed as a normal Python package:

```sh
pipx install r2-local-fs
```

or run from a checkout during development:

```sh
PYTHONPATH=src python -m r2_local_fs watch \
  --endpoint http://localhost:8787 \
  --bucket wk-prod \
  --dir ~/R2/wk-prod
```

The current implementation uses only the Python standard library. Future
versions may add:

- `httpx` for Local Explorer API calls
- `watchfiles` or `watchdog` for filesystem events
- `typer` or `click` for the CLI
- `pydantic` for config and manifest validation
- `rich` for readable status output
- `fastapi` or `starlette` only if the S3-compatible endpoint needs an HTTP
  framework

The core sync loop does not need a web framework.

## Sync Behavior

The local folder is a facade over local R2.

When a file is created or changed:

```txt
~/R2/wk-prod/blog/foo.json
```

the tool uploads it to:

```txt
R2 key: blog/foo.json
```

When a file is deleted from the folder, the corresponding local R2 object is
deleted. There is no confirmation prompt and no trash mode. The folder is the
source of intent for local filesystem changes.

When the Worker writes to R2:

```ts
await env.BUCKET.put("blog/bar.json", body);
```

the tool notices the remote change and writes:

```txt
~/R2/wk-prod/blog/bar.json
```

## Bulk Copy Safety

The watcher should not rely on file events as the only source of truth. File
events are a fast signal; reconciliation is what guarantees convergence.

For example, if a developer copies 500 files into the facade directory over two
minutes, the expected behavior is:

```txt
copy 500 files into ~/R2/wk-prod/assets/
  -> file watcher queues changes quickly
  -> files are uploaded only after they appear stable
  -> uploads run with bounded concurrency
  -> failures retry with backoff
  -> a reconciliation scan catches any missed watcher events
  -> local folder and local R2 converge
```

Recommended defaults:

```json
{
  "localDebounceMs": 300,
  "stableFileMs": 1000,
  "remotePollMs": 5000,
  "fullScanMs": 60000,
  "uploadConcurrency": 6
}
```

The CLI should show honest status:

```txt
Queued: 500
Uploading: 6
Uploaded: 381
Retrying: 2
Failed: 0
Pending verification: 111
```

## AWS CLI Compatibility Mode

A second mode can expose a small local S3-compatible endpoint that forwards AWS
CLI operations into Wrangler local R2 through the Local Explorer API.

```sh
r2-local-fs s3 \
  --endpoint http://localhost:8787 \
  --bucket wk-prod \
  --port 9000
```

Then:

```sh
AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local \
aws s3 sync ./assets s3://wk-prod/assets \
  --endpoint-url http://localhost:9000
```

The first implementation does not need the entire S3 API. It only needs the
subset used by common AWS CLI object workflows:

- `ListObjectsV2`
- `HeadObject`
- `GetObject`
- `PutObject`
- `DeleteObject`
- `CopyObject`

Multipart upload support can come later if needed for large local fixtures.

## Non-Goals

- Do not replace Wrangler.
- Do not require changes to Worker code.
- Do not require changes to `wrangler.toml`.
- Do not move or rewrite `.wrangler/state/v3/r2`.
- Do not write directly to Miniflare SQLite/blob internals during normal sync.
- Do not try to emulate all of S3 in the first version.

## Implementation Notes

The facade should maintain a local manifest, for example:

```txt
~/R2/wk-prod/.r2-local-fs/manifest.json
```

The manifest should track enough state to detect drift and recover after
restarts:

```json
{
  "bucket": "wk-prod",
  "endpoint": "http://localhost:8787",
  "objects": {
    "blog/foo.json": {
      "etag": "3a134f8ae04aae02b05fce3b77550e64",
      "size": 326,
      "lastModified": "2026-05-04T01:22:23.884Z",
      "localMtimeMs": 1777857743884
    }
  }
}
```

Local writes should use stable-file detection before upload. Remote writes should
be discovered by polling Local Explorer object listings and comparing object
metadata to the manifest.

Conflict handling should be explicit. If the same key changes locally and
remotely before either side has been synced, the tool should preserve both
versions and report the conflict instead of silently overwriting data.

## Status

This repository now contains the first Python implementation of the local R2
filesystem facade.

Implemented:

1. Local Explorer API client.
2. Bucket discovery.
3. `init` config generation.
4. One-way `pull` from local R2 to a normal folder.
5. One-way `push` from a normal folder to local R2.
6. Continuous `watch`/`on` reconciliation.
7. Direct remote delete when a locally mirrored file is deleted.
8. Stable-file detection before upload.
9. Manifest-based drift detection.

Not implemented yet:

1. Native filesystem event acceleration.
2. Bounded concurrent upload/download workers.
3. S3-compatible AWS CLI shim.
4. Packaged release on PyPI.
