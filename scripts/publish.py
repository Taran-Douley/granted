"""Publish a run to S3, so the dashboard updates itself.

The dashboard is a static page that fetches `run.json` from its own origin. It
never calls Find a grant or 360Giving directly, because neither sets CORS headers
and a browser fetch would fail. The agent does the fetching; the page reads what
the agent wrote.

    scheduled run  ->  run.json in S3  ->  dashboard fetches same-origin

Usage:
    python scripts/publish.py --bucket granted-dashboard-2026 --run data/run.json
    python scripts/publish.py --bucket granted-dashboard-2026 --dashboard dist/index.html
    python scripts/publish.py --bucket granted-dashboard-2026 --setup     # first time

Cache headers matter. `run.json` is written no-cache so the poll actually sees new
data; without that S3 serves a stale copy and the page looks frozen while the agent
is working fine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def setup_bucket(s3, bucket: str, region: str) -> None:
    """Create a public static site bucket. Idempotent."""
    try:
        kw = {"Bucket": bucket}
        if region != "us-east-1":
            kw["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kw)
        print(f"created bucket {bucket}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
        print(f"bucket {bucket} already exists")

    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": False, "IgnorePublicAcls": False,
            "BlockPublicPolicy": False, "RestrictPublicBuckets": False,
        },
    )
    s3.put_bucket_website(
        Bucket=bucket,
        WebsiteConfiguration={"IndexDocument": {"Suffix": "index.html"}},
    )
    s3.put_bucket_policy(
        Bucket=bucket,
        Policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "PublicRead",
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket}/*",
            }],
        }),
    )
    print(f"site: http://{bucket}.s3-website.{region}.amazonaws.com")


def redact(record: dict) -> dict:
    """What a public page may show of a run record written for a private folder.

    Two things come out. The names of files the archive refused to read: the
    refusal is the point, and "Safeguarding log - J Smith.docx" must not become
    something anyone with the link can read. And the workspace's full path,
    which carries a user name. Counts and reasons stay, so the page can still
    say "7 skipped, and why". The record passed in is left untouched.
    """
    out = json.loads(json.dumps(record))
    scope = out.get("scope") or {}
    if scope.get("skipped_detail"):
        scope["skipped_detail"] = [{"name": "(name withheld)", "reason": s.get("reason", "")}
                                   for s in scope["skipped_detail"]]
    if out.get("workspace"):
        out["workspace"] = Path(out["workspace"]).name
    out["_redacted"] = "skipped file names withheld; workspace path trimmed to its folder name"
    return out


def put(s3, bucket: str, key: str, body: bytes, content_type: str, cache: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type, CacheControl=cache)
    print(f"put {key}  ({len(body)} bytes, cache: {cache})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--run", type=Path, help="path to run.json")
    ap.add_argument("--dashboard", type=Path, help="path to the built dashboard html")
    ap.add_argument("--setup", action="store_true", help="create and configure the bucket")
    ap.add_argument("--no-redact", action="store_true",
                    help="publish the run record exactly as written (private buckets only)")
    args = ap.parse_args()

    if not (args.run or args.dashboard or args.setup):
        ap.error("nothing to do: pass --setup, --run and/or --dashboard")

    s3 = boto3.client("s3", region_name=args.region)
    try:
        return publish(s3, args)
    except ClientError as e:
        err = e.response.get("Error", {})
        action = e.operation_name
        print(f"S3 refused {action}: {err.get('Code')}. {err.get('Message', '')}", file=sys.stderr)
        if err.get("Code") in ("AccessDenied", "AllAccessDisabled"):
            if action == "PutBucketPolicy":
                print("  The account blocks public bucket policies. Turn that off in the S3 "
                      "console under 'Block Public Access settings for this account', then "
                      "run this again.", file=sys.stderr)
            else:
                print("  This IAM user lacks S3 permissions on the bucket. Attach "
                      "scripts/publish-policy.json to it in the IAM console (it grants only "
                      "what this script uses, on that one bucket; edit the name inside if you "
                      "publish elsewhere), then run this again.", file=sys.stderr)
        return 1


def publish(s3, args: argparse.Namespace) -> int:
    if args.setup:
        setup_bucket(s3, args.bucket, args.region)

    if args.dashboard:
        if not args.dashboard.exists():
            print(f"missing: {args.dashboard}", file=sys.stderr)
            return 1
        # The page itself can cache briefly; it changes rarely.
        put(s3, args.bucket, "index.html", args.dashboard.read_bytes(), "text/html", "max-age=300")

    if args.run:
        if not args.run.exists():
            print(f"missing: {args.run}", file=sys.stderr)
            return 1
        data = json.loads(args.run.read_text(encoding="utf-8"))
        if data.get("schema_version") != 2:
            print(f"warning: schema_version {data.get('schema_version')}, dashboard expects 2",
                  file=sys.stderr)
        # Never cached. The dashboard polls this; a cached copy looks like a
        # frozen agent.
        if not args.no_redact:
            data = redact(data)
        put(s3, args.bucket, "run.json",
            json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
            "application/json", "no-cache, no-store")
        print(f"  run {data['run_date']} {data['run_time']}, "
              f"{data['calls_considered']} considered, "
              f"{len(data.get('surfaced', []))} surfaced")

    print(f"\nhttp://{args.bucket}.s3-website.{args.region}.amazonaws.com")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
