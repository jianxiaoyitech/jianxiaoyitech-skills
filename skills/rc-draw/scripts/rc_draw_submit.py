#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import rc_draw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit an rc_draw image generation task."
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--image-size", default="1K")
    parser.add_argument("--aspect-ratio", default="1:1")
    parser.add_argument("--reference-image")
    parser.add_argument("--harness", default="auto")
    parser.add_argument("--api-key")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rc_draw.validate_args(
        SimpleNamespace(
            model=args.model,
            image_size=args.image_size,
            aspect_ratio=args.aspect_ratio,
            reference_image=args.reference_image,
            harness=args.harness,
        )
    )
    auth = rc_draw.resolve_auth(args)
    submit_response = rc_draw.submit_generation(args, auth.api_key)
    task_id = submit_response.get("task_id")
    if not task_id:
        raise rc_draw.RcDrawError(
            f"Missing task_id in submit response: {submit_response}"
        )
    rc_draw.print_progress_event(
        {
            "task_id": task_id,
            "status": submit_response.get("status", "submitted"),
            "progress": submit_response.get("progress", 0),
            "model": args.model,
        }
    )
    print(
        json.dumps(
            {
                "task_id": task_id,
                "model": args.model,
                "branch": rc_draw.branch_for_model(args.model),
                "harness": auth.harness,
                "auth_source": auth.source,
                "submit_response": submit_response,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except rc_draw.RcDrawError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True, indent=2))
        raise SystemExit(1)
