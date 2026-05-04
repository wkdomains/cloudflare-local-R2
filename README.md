# Cloudflare Local R2 Filesystem Facade

`r2-local-fs` gives Wrangler local R2 buckets a normal filesystem facade.

Cloudflare R2 is S3-compatible in production, so tools like the AWS CLI work
well against a real bucket:

```sh
aws s3 sync ./assets s3://my-bucket/assets \
  --endpoint-url https://<account-id>.r2.cloudflarestorage.com
```

Local Wrangler R2 is not stored that way. When you run `wrangler dev`, local R2
data is persisted under:

```txt
.wrangler/state/v3/r2/
```

That directory is Miniflare persistence state, not a bucket-shaped filesystem.
Object bodies live as opaque blob files, while object keys and metadata live in
SQLite databases.

This tool leaves `.wrangler` alone and mirrors a local R2 bucket into a normal
folder such as:

```txt
~/R2/wk-prod/
  blog/2026/may/foo.json
  images/logo.png
  assets/app.css
```

Files copied into that folder are uploaded into Wrangler local R2. Objects
written by the Worker are mirrored back into the folder. Files deleted from the
folder are deleted from local R2.

No Worker code changes. No Wrangler config changes. No moving `.wrangler`.

## How It Works

Wrangler exposes Local Explorer while `wrangler dev` is running:

```txt
http://localhost:8787/cdn-cgi/explorer/
```

The browser UI uses a local API under:

```txt
http://localhost:8787/cdn-cgi/explorer/api
```

`r2-local-fs` talks to that API instead of writing directly into Miniflare's
SQLite/blob internals. The useful R2 endpoints are:

```txt
GET    /cdn-cgi/explorer/api/r2/buckets
GET    /cdn-cgi/explorer/api/r2/buckets/{bucket}/objects
GET    /cdn-cgi/explorer/api/r2/buckets/{bucket}/objects/{key}
PUT    /cdn-cgi/explorer/api/r2/buckets/{bucket}/objects/{key}
DELETE /cdn-cgi/explorer/api/r2/buckets/{bucket}/objects
```

## Current Status

This repository contains a working Python implementation using only the Python
standard library.

Implemented:

1. Local Explorer API client.
2. Bucket discovery.
3. Project config generation with `init`.
4. `pull` from local R2 to a normal folder.
5. `push` from a normal folder to local R2.
6. Continuous `watch`/`on` reconciliation.
7. Direct remote delete when a mirrored local file is deleted.
8. Stable-file detection before upload.
9. Manifest-based drift detection.

Not implemented:

1. Native filesystem event acceleration.
2. Concurrent upload/download workers.
3. S3-compatible AWS CLI endpoint.
4. Packaged PyPI release.

## Requirements

- Python 3.11+
- A running Wrangler dev server
- Local Explorer available at the Wrangler dev endpoint

## Run From This Checkout

Start your Worker as usual:

```sh
cd /path/to/your-worker
npm run dev
```

In this repository, list local R2 buckets:

```sh
PYTHONPATH=src python3 -m r2_local_fs buckets \
  --endpoint http://localhost:8787
```

Initialize a facade folder:

```sh
PYTHONPATH=src python3 -m r2_local_fs init \
  --endpoint http://localhost:8787 \
  --bucket wk-prod \
  --dir ~/R2/wk-prod
```

`init` creates:

```txt
.r2-local-fs.json
~/R2/wk-prod/.r2-local-fs/manifest.json
```

After `init`, run commands from the same directory without repeating `--bucket`,
`--dir`, or `--endpoint`:

```sh
PYTHONPATH=src python3 -m r2_local_fs pull
PYTHONPATH=src python3 -m r2_local_fs push
PYTHONPATH=src python3 -m r2_local_fs on
```

You can also run without config by passing all options:

```sh
PYTHONPATH=src python3 -m r2_local_fs watch \
  --endpoint http://localhost:8787 \
  --bucket wk-prod \
  --dir ~/R2/wk-prod
```

## Commands

### `buckets`

Lists local R2 buckets exposed by the running Wrangler dev server.

```sh
PYTHONPATH=src python3 -m r2_local_fs buckets \
  --endpoint http://localhost:8787
```

### `init`

Creates the facade directory, creates an empty manifest if needed, and writes
`.r2-local-fs.json` in the current directory.

If there is exactly one local R2 bucket, `--bucket` can be omitted.

```sh
PYTHONPATH=src python3 -m r2_local_fs init \
  --endpoint http://localhost:8787 \
  --dir ~/R2/wk-prod
```

### `pull`

Downloads local R2 objects into the facade directory.

```sh
PYTHONPATH=src python3 -m r2_local_fs pull
```

### `push`

Uploads files from the facade directory into local R2.

```sh
PYTHONPATH=src python3 -m r2_local_fs push
```

### `watch` / `on`

Continuously reconciles the facade directory and local R2.

```sh
PYTHONPATH=src python3 -m r2_local_fs on
```

`on` is an alias for `watch`.

By default, watch mode reconciles every 5 seconds:

```sh
PYTHONPATH=src python3 -m r2_local_fs on --remote-poll-ms 1000
```

## Sync Behavior

The facade folder is treated as the developer-facing filesystem view of the
bucket.

When a file is created or changed:

```txt
~/R2/wk-prod/blog/foo.json
```

the tool uploads it as:

```txt
blog/foo.json
```

When a file is deleted from the facade folder, the corresponding local R2 object
is deleted. There is no confirmation prompt and no trash mode.

When the Worker writes to R2:

```ts
await env.BUCKET.put("blog/bar.json", body);
```

the tool downloads it to:

```txt
~/R2/wk-prod/blog/bar.json
```

Directory-marker objects ending in `/` are ignored as files.

## Bulk Copy Behavior

Current watch mode is reconciliation polling, not native filesystem events.
Every reconciliation scans the local folder, lists local R2 objects, compares
both sides to the manifest, and applies the needed changes.

Before upload, a changed local file must be stable. The default is 1000ms:

```sh
PYTHONPATH=src python3 -m r2_local_fs on --stable-file-ms 1000
```

If hundreds of files are copied into the facade folder over a few minutes, each
poll sees the files that are present, waits for changed files to stop changing,
and uploads them. Later polls catch files that were still being copied or missed
by an earlier reconciliation.

Uploads and downloads are currently sequential.

## Manifest

The manifest lives inside the facade folder:

```txt
~/R2/wk-prod/.r2-local-fs/manifest.json
```

It records the last synced remote metadata and local file metadata for each key:

```json
{
  "bucket": "wk-prod",
  "endpoint": "http://localhost:8787",
  "objects": {
    "blog/foo.json": {
      "etag": "3a134f8ae04aae02b05fce3b77550e64",
      "size": 326,
      "last_modified": "2026-05-04T01:22:23.884Z",
      "local_mtime_ns": 1777857743884000000,
      "local_size": 326,
      "synced_at": 1777935600.0
    }
  }
}
```

The manifest is what lets the tool distinguish:

- a new local file that should be uploaded
- a new remote object that should be downloaded
- a local deletion that should delete remote
- a remote deletion that should delete local
- a conflict where both sides changed since the last sync

Conflict remote copies are preserved under:

```txt
~/R2/wk-prod/.r2-local-fs/conflicts/
```

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
blob filename is not the R2 object key.

Directly writing those SQLite rows and blob files would be brittle, especially
while `wrangler dev` is running. Cloudflare can change that internal format, and
concurrent writes could corrupt local state.

`r2-local-fs` lets Wrangler own `.wrangler/state/v3/r2/` and talks through the
same local interface used by Local Explorer.

## Development

Run tests:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Compile-check the package:

```sh
python3 -m compileall -q src tests
```

The package exposes a console script in `pyproject.toml`:

```txt
r2-local-fs = "r2_local_fs.cli:main"
```

Editable install works with standard Python packaging tools:

```sh
python3 -m pip install -e .
r2-local-fs --help
```

## Non-Goals

- Replace Wrangler.
- Require changes to Worker code.
- Require changes to `wrangler.toml`.
- Move or rewrite `.wrangler/state/v3/r2`.
- Write directly to Miniflare SQLite/blob internals during normal sync.
- Emulate the full S3 API.
