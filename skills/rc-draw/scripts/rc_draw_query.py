#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

import rc_draw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query or poll an rc_draw task."
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--harness", default="auto")
    parser.add_argument("--api-key")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--download", action="store_true", default=True)
    parser.add_argument("--no-download", dest="download", action="store_false")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Query exactly once and exit without waiting for completion.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    auth = rc_draw.resolve_auth(args)
    if args.once:
        result = rc_draw.get_json(
            f"{rc_draw.TASK_BASE_URL}/v1/tasks/{args.task_id}",
            auth.api_key,
        )
        rc_draw.print_progress_event(
            {
                "task_id": result.get("task_id", args.task_id),
                "status": result.get("status"),
                "progress": result.get("progress"),
                "model": result.get("model"),
                "created_at": result.get("created_at"),
            }
        )
        print(json.dumps(sanitize_result(result), ensure_ascii=False, indent=2))
        return 0

    result = rc_draw.poll_task(
        task_id=args.task_id,
        api_key=auth.api_key,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )
    downloads = []
    if args.download:
        output_dir = rc_draw.build_output_dir(args.output_dir)
        downloads = rc_draw.download_outputs(result, output_dir)
        rc_draw.print_final_paths(downloads)
        output_dir_str = str(output_dir)
    else:
        output_dir_str = str(Path(args.output_dir).expanduser())
    print(
        json.dumps(
            {
                "task_id": args.task_id,
                "harness": auth.harness,
                "auth_source": auth.source,
                "output_dir": output_dir_str,
                "downloads": downloads,
                "result": sanitize_result(result),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def sanitize_result(result: dict) -> dict:
    if "data" in result and isinstance(result["data"], list):
        sanitized = dict(result)
        sanitized["data"] = [{} for _ in result["data"]]
        return sanitized
    if "candidates" in result and isinstance(result["candidates"], list):
        sanitized = dict(result)
        sanitized["candidates"] = [{"content": {"parts": []}} for _ in result["candidates"]]
        return sanitized
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except rc_draw.RcDrawError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True, indent=2))
        raise SystemExit(1)
