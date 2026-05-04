from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .client import LocalExplorerClient
from .config import CONFIG_FILE, ProjectConfig
from .errors import R2LocalFSError
from .paths import expand_path
from .sync import SyncEngine, SyncOptions, format_stats


DEFAULT_ENDPOINT = "http://localhost:8787"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "buckets":
            return cmd_buckets(args)
        if args.command == "init":
            return cmd_init(args)
        if args.command == "pull":
            return cmd_pull(args)
        if args.command == "push":
            return cmd_push(args)
        if args.command in {"watch", "on"}:
            return cmd_watch(args)
        parser.print_help()
        return 2
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130
    except R2LocalFSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="r2-local-fs",
        description="Filesystem facade for Wrangler local R2 buckets",
    )
    subparsers = parser.add_subparsers(dest="command")

    buckets = subparsers.add_parser("buckets", help="List local R2 buckets")
    add_endpoint_arg(buckets)

    init = subparsers.add_parser("init", help="Create a facade directory and manifest")
    add_common_args(init)

    pull = subparsers.add_parser("pull", help="Download local R2 objects into a folder")
    add_common_args(pull)
    add_dry_run_arg(pull)

    push = subparsers.add_parser("push", help="Upload folder files into local R2")
    add_common_args(push)
    add_stable_arg(push)
    add_dry_run_arg(push)

    watch = subparsers.add_parser("watch", help="Continuously reconcile folder and R2")
    add_common_args(watch)
    add_stable_arg(watch)
    watch.add_argument(
        "--remote-poll-ms",
        type=int,
        default=5000,
        help="Remote reconciliation interval in milliseconds",
    )
    add_dry_run_arg(watch)

    on = subparsers.add_parser("on", help="Alias for watch")
    add_common_args(on)
    add_stable_arg(on)
    on.add_argument(
        "--remote-poll-ms",
        type=int,
        default=5000,
        help="Remote reconciliation interval in milliseconds",
    )
    add_dry_run_arg(on)

    return parser


def add_endpoint_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Wrangler dev endpoint, not including /cdn-cgi/explorer/api",
    )


def add_common_args(parser: argparse.ArgumentParser) -> None:
    add_endpoint_arg(parser)
    parser.add_argument("--bucket", help="Local R2 bucket name")
    parser.add_argument(
        "--dir",
        help="Normal filesystem directory to mirror",
    )


def add_stable_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--stable-file-ms",
        type=int,
        default=1000,
        help="Wait for file size and mtime to stop changing before upload",
    )


def add_dry_run_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes without writing files or R2 objects",
    )


def cmd_buckets(args: argparse.Namespace) -> int:
    client = LocalExplorerClient(endpoint=args.endpoint or DEFAULT_ENDPOINT)
    for bucket in client.list_buckets():
        print(bucket)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    endpoint = args.endpoint or DEFAULT_ENDPOINT
    bucket = args.bucket
    client = LocalExplorerClient(endpoint=endpoint)

    if not bucket:
        buckets = client.list_buckets()
        if not buckets:
            raise R2LocalFSError(
                "no local R2 buckets found; is wrangler dev running?"
            )
        if len(buckets) > 1:
            names = ", ".join(buckets)
            raise R2LocalFSError(
                f"multiple buckets found ({names}); pass --bucket"
            )
        bucket = buckets[0]

    root = expand_path(args.dir or f"~/R2/{bucket}")
    options = SyncOptions(
        endpoint=endpoint,
        bucket=bucket,
        root=root,
        stable_file_ms=getattr(args, "stable_file_ms", 1000),
        remote_poll_ms=getattr(args, "remote_poll_ms", 5000),
        dry_run=getattr(args, "dry_run", False),
    )
    engine = make_engine(options)
    engine.init()
    config = ProjectConfig(endpoint=endpoint, buckets={bucket: root})
    config.save(Path.cwd() / CONFIG_FILE)
    print(f"wrote {CONFIG_FILE}")
    print(f"{bucket}: {root}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    engine = make_engine(options_from_args(args))
    stats = engine.pull()
    print(format_stats(stats))
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    engine = make_engine(options_from_args(args))
    stats = engine.push()
    print(format_stats(stats))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    engine = make_engine(options_from_args(args))
    engine.watch()
    return 0


def options_from_args(args: argparse.Namespace) -> SyncOptions:
    config = ProjectConfig.load(Path.cwd() / CONFIG_FILE)
    endpoint = args.endpoint or DEFAULT_ENDPOINT
    bucket = args.bucket
    root_arg = args.dir

    if config:
        endpoint = args.endpoint or config.endpoint
        if not bucket:
            if len(config.buckets) == 1:
                bucket = next(iter(config.buckets))
            else:
                raise R2LocalFSError("config has multiple buckets; pass --bucket")
        if not root_arg and bucket in config.buckets:
            root_arg = str(config.buckets[bucket])

    if not bucket:
        raise R2LocalFSError("missing --bucket; run r2-local-fs init first")
    if not root_arg:
        raise R2LocalFSError("missing --dir; run r2-local-fs init first")

    return SyncOptions(
        endpoint=endpoint,
        bucket=bucket,
        root=expand_path(root_arg),
        stable_file_ms=getattr(args, "stable_file_ms", 1000),
        remote_poll_ms=getattr(args, "remote_poll_ms", 5000),
        dry_run=getattr(args, "dry_run", False),
    )


def make_engine(options: SyncOptions) -> SyncEngine:
    client = LocalExplorerClient(endpoint=options.endpoint)
    return SyncEngine(client, options)
