#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DRAW_BASE_URL = "https://www.right.codes/draw"
TASK_BASE_URL = "https://www.right.codes"
POLL_INTERVAL_SECONDS = 1.0
DEFAULT_OUTPUT_DIR = "."
IMAGE_MODELS = {"gpt-image-2"}
GEMINI_MODELS = {
    "nano-banana",
    "nano-banana-2",
    "nano-banana-pro",
    "nano-banana-2-lite",
}
MODEL_SIZES = {
    "gpt-image-2": {"1K"},
    "nano-banana": {"1K"},
    "nano-banana-2": {"1K", "2K", "4K"},
    "nano-banana-pro": {"1K", "2K", "4K"},
    "nano-banana-2-lite": {"1K"},
}
ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3"}
SUPPORTED_HARNESSES = {
    "auto",
    "codex",
    "claude-code",
    "gemini-cli",
    "opencode",
    "paseo",
}


class RcDrawError(RuntimeError):
    pass


@dataclass
class ResolvedAuth:
    harness: str
    api_key: str
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate images with Right Code rc_draw."
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--image-size", default="1K")
    parser.add_argument("--aspect-ratio", default="1:1")
    parser.add_argument("--reference-image")
    parser.add_argument("--harness", default="auto")
    parser.add_argument("--api-key")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL_SECONDS)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--download", action="store_true", default=True)
    parser.add_argument("--no-download", dest="download", action="store_false")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_args(args)
    auth = resolve_auth(args)
    output_dir = build_output_dir(args.output_dir)

    submit_response = submit_generation(args, auth.api_key)
    task_id = submit_response.get("task_id")
    if not task_id:
        raise RcDrawError(f"Missing task_id in submit response: {submit_response}")
    print_progress_event(
        {
            "task_id": task_id,
            "status": submit_response.get("status", "submitted"),
            "progress": submit_response.get("progress", 0),
            "model": args.model,
        }
    )

    result = poll_task(
        task_id=task_id,
        api_key=auth.api_key,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )
    downloads = []
    if args.download:
        downloads = download_outputs(result, output_dir)
    print_final_paths(downloads)

    payload = {
        "task_id": task_id,
        "model": args.model,
        "branch": branch_for_model(args.model),
        "harness": auth.harness,
        "auth_source": auth.source,
        "image_size": args.image_size,
        "aspect_ratio": args.aspect_ratio,
        "reference_image": args.reference_image,
        "output_dir": str(output_dir),
        "downloads": downloads,
        "result": result,
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


def validate_args(args: argparse.Namespace) -> None:
    if args.model not in MODEL_SIZES:
        supported = ", ".join(sorted(MODEL_SIZES))
        raise RcDrawError(f"Unsupported model '{args.model}'. Supported: {supported}")
    if args.image_size not in MODEL_SIZES[args.model]:
        allowed = ", ".join(sorted(MODEL_SIZES[args.model]))
        raise RcDrawError(
            f"Unsupported image size '{args.image_size}' for model "
            f"'{args.model}'. Allowed: {allowed}"
        )
    if args.aspect_ratio not in ASPECT_RATIOS:
        allowed = ", ".join(sorted(ASPECT_RATIOS))
        raise RcDrawError(
            f"Unsupported aspect ratio '{args.aspect_ratio}'. Allowed: {allowed}"
        )
    if args.harness not in SUPPORTED_HARNESSES:
        allowed = ", ".join(sorted(SUPPORTED_HARNESSES))
        raise RcDrawError(
            f"Unsupported harness '{args.harness}'. Allowed: {allowed}"
        )
    if args.reference_image:
        ref_path = Path(args.reference_image).expanduser()
        if not ref_path.is_file():
            raise RcDrawError(f"Reference image not found: {args.reference_image}")


def resolve_auth(args: argparse.Namespace) -> ResolvedAuth:
    if args.api_key:
        return ResolvedAuth(
            harness=args.harness if args.harness != "auto" else "explicit",
            api_key=args.api_key,
            source="--api-key",
        )

    env_key = first_env(
        "RIGHT_CODES_API_KEY",
        "RC_API_KEY",
    )
    if env_key:
        return ResolvedAuth(
            harness=args.harness if args.harness != "auto" else "env",
            api_key=env_key,
            source="environment",
        )

    harnesses = (
        [args.harness] if args.harness != "auto" else detect_harness_order()
    )
    errors = []
    for harness in harnesses:
        try:
            return resolve_auth_for_harness(harness)
        except RcDrawError as exc:
            errors.append(f"{harness}: {exc}")
    raise RcDrawError(
        "Unable to resolve a Right Code API key. "
        "Tried: " + "; ".join(errors)
    )


def detect_harness_order() -> list[str]:
    detected = []
    if Path.home().joinpath(".codex", "auth.json").is_file():
        detected.append("codex")
    if Path.home().joinpath(".claude", "settings.json").is_file():
        detected.append("claude-code")
    if Path.home().joinpath(".config", "opencode", "opencode.json").is_file():
        detected.append("opencode")
    if Path.home().joinpath(".gemini", "settings.json").is_file():
        detected.append("gemini-cli")
    detected.extend(["paseo", "codex", "claude-code", "gemini-cli", "opencode"])
    return dedupe(detected)


def resolve_auth_for_harness(harness: str) -> ResolvedAuth:
    if harness == "codex":
        env_key = first_env("OPENAI_API_KEY")
        if env_key:
            return ResolvedAuth(harness, env_key, "OPENAI_API_KEY")
        auth = read_json_file(Path.home() / ".codex" / "auth.json")
        key = auth.get("OPENAI_API_KEY") or auth.get("api_key")
        if key:
            return ResolvedAuth(harness, key, "~/.codex/auth.json")
        raise RcDrawError("missing OPENAI_API_KEY in ~/.codex/auth.json")

    if harness == "claude-code":
        env_key = first_env("ANTHROPIC_AUTH_TOKEN")
        if env_key:
            return ResolvedAuth(harness, env_key, "ANTHROPIC_AUTH_TOKEN")
        settings = read_json_file(Path.home() / ".claude" / "settings.json")
        env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
        key = env.get("ANTHROPIC_AUTH_TOKEN")
        if key:
            return ResolvedAuth(harness, key, "~/.claude/settings.json")
        raise RcDrawError("missing ANTHROPIC_AUTH_TOKEN in ~/.claude/settings.json")

    if harness == "gemini-cli":
        env_key = first_env("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if env_key:
            return ResolvedAuth(harness, env_key, "gemini env")
        settings = read_optional_json(Path.home() / ".gemini" / "settings.json")
        key = find_api_key(settings)
        if key:
            return ResolvedAuth(harness, key, "~/.gemini/settings.json")
        raise RcDrawError("missing GEMINI_API_KEY or ~/.gemini/settings.json")

    if harness == "opencode":
        env_key = first_env("OPENCODE_API_KEY")
        if env_key:
            return ResolvedAuth(harness, env_key, "OPENCODE_API_KEY")
        settings = read_json_file(Path.home() / ".config" / "opencode" / "opencode.json")
        key = find_api_key(settings)
        if key:
            return ResolvedAuth(harness, key, "~/.config/opencode/opencode.json")
        raise RcDrawError("missing API key in ~/.config/opencode/opencode.json")

    if harness == "paseo":
        underlying = detect_harness_order()
        for candidate in underlying:
            if candidate == "paseo":
                continue
            try:
                auth = resolve_auth_for_harness(candidate)
                return ResolvedAuth("paseo", auth.api_key, f"paseo->{auth.source}")
            except RcDrawError:
                continue
        raise RcDrawError("could not infer the underlying harness config")

    raise RcDrawError(f"unsupported harness '{harness}'")


def build_output_dir(raw_output_dir: str) -> Path:
    ensure_not_running_from_skill_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(raw_output_dir).expanduser() / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def ensure_not_running_from_skill_dir() -> None:
    cwd = Path.cwd().resolve()
    skill_root = Path(__file__).resolve().parent.parent
    if cwd == skill_root or skill_root in cwd.parents:
        raise RcDrawError(
            "Refusing to write output inside the rc-draw skill directory. "
            "Run the script from the project directory."
        )


def submit_generation(args: argparse.Namespace, api_key: str) -> dict[str, Any]:
    if args.model in IMAGE_MODELS:
        path = "/v1/images/generations"
        body = build_images_payload(args)
    elif args.model in GEMINI_MODELS:
        path = f"/v1beta/models/{urllib.parse.quote(args.model)}:generateContent"
        body = build_gemini_payload(args)
    else:
        raise RcDrawError(f"Unsupported model branch: {args.model}")

    url = f"{DRAW_BASE_URL}{path}"
    return post_json(url, api_key, body)


def build_images_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": args.prompt,
        "n": 1,
        "size": args.aspect_ratio,
        "imageSize": args.image_size,
        "async": True,
    }
    if args.reference_image:
        payload["image"] = [to_data_url(Path(args.reference_image).expanduser())]
    return payload


def build_gemini_payload(args: argparse.Namespace) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": args.prompt}]
    if args.reference_image:
        ref_path = Path(args.reference_image).expanduser()
        parts.append(
            {
                "inline_data": {
                    "mime_type": guess_mime_type(ref_path),
                    "data": read_base64(ref_path),
                }
            }
        )
    return {
        "async": True,
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "imageConfig": {
                "aspectRatio": args.aspect_ratio,
                "imageSize": args.image_size,
            }
        },
    }


def poll_task(
    task_id: str,
    api_key: str,
    poll_interval: float,
    timeout: int,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    url = f"{TASK_BASE_URL}/v1/tasks/{urllib.parse.quote(task_id)}"

    while True:
        result = get_json(url, api_key)
        status = result.get("status")
        print_progress_event(
            {
                "task_id": result.get("task_id", task_id),
                "status": status,
                "progress": result.get("progress"),
                "model": result.get("model"),
                "created_at": result.get("created_at"),
            }
        )
        if status == "failed":
            error = result.get("error") or {}
            message = error.get("message") or "unknown rc_draw error"
            raise RcDrawError(f"Task failed: {message}")
        if status == "completed" or is_completed_payload(result):
            return result
        if time.time() >= deadline:
            raise RcDrawError(f"Timed out while waiting for task {task_id}")
        time.sleep(poll_interval)


def is_completed_payload(payload: dict[str, Any]) -> bool:
    return "data" in payload or "candidates" in payload


def download_outputs(result: dict[str, Any], output_dir: Path) -> list[str]:
    urls = extract_urls(result)
    if not urls:
        return []

    downloads = []
    for index, url in enumerate(urls, start=1):
        suffix = guess_suffix_from_url(url)
        target = output_dir / f"image-{index}{suffix}"
        download_file(url, target)
        downloads.append(str(target))
    return downloads


def print_progress_event(event: dict[str, Any]) -> None:
    compact = {
        key: value
        for key, value in event.items()
        if value is not None
    }
    print(json.dumps(compact, ensure_ascii=False), flush=True)


def print_final_paths(downloads: list[str]) -> None:
    if not downloads:
        return
    for path in downloads:
        print(f"saved_image_path={path}", flush=True)


def extract_urls(result: dict[str, Any]) -> list[str]:
    urls = []

    for item in result.get("data", []):
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str):
                urls.append(url)

    for candidate in result.get("candidates", []):
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []):
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and re.match(r"^https?://", text):
                urls.append(text)

    return urls


def post_json(url: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        delete=False,
    ) as handle:
        handle.write(json.dumps(body, ensure_ascii=False))
        payload_path = handle.name
    try:
        return run_curl_json(
            [
                "-X",
                "POST",
                url,
                "-H",
                f"Authorization: Bearer {api_key}",
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                f"@{payload_path}",
            ]
        )
    finally:
        try:
            os.unlink(payload_path)
        except FileNotFoundError:
            pass


def get_json(url: str, api_key: str) -> dict[str, Any]:
    return run_curl_json(
        [
            url,
            "-H",
            f"Authorization: Bearer {api_key}",
        ]
    )


def run_curl_json(args: list[str]) -> dict[str, Any]:
    command = ["curl", "-sS", "--fail-with-body", *args]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RcDrawError(f"curl request failed: {message}") from exc
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RcDrawError(f"invalid JSON from curl: {completed.stdout}") from exc


def download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request) as response:
            target.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise RcDrawError(f"Failed to download {url}: {exc}") from exc


def read_response(request: urllib.request.Request) -> str:
    try:
        with urllib.request.urlopen(request) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RcDrawError(
            f"HTTP {exc.code} from {request.full_url}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RcDrawError(f"Request failed for {request.full_url}: {exc}") from exc


def branch_for_model(model: str) -> str:
    if model in IMAGE_MODELS:
        return "images"
    if model in GEMINI_MODELS:
        return "gemini"
    return "unknown"


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RcDrawError(f"config file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RcDrawError(f"invalid JSON in {path}: {exc}") from exc


def read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return read_json_file(path)


def find_api_key(node: Any) -> str | None:
    if isinstance(node, str) and node.startswith("sk-"):
        return node
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in {"api_key", "apikey", "key", "auth_token"}:
                found = find_api_key(value)
                if found:
                    return found
            found = find_api_key(value)
            if found:
                return found
    if isinstance(node, list):
        for item in node:
            found = find_api_key(item)
            if found:
                return found
    return None


def first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def to_data_url(path: Path) -> str:
    mime_type = guess_mime_type(path)
    return f"data:{mime_type};base64,{read_base64(path)}"


def read_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def guess_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    return mime_type or "image/png"


def guess_suffix_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix
    return suffix if suffix else ".png"


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RcDrawError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True, indent=2))
        raise SystemExit(1)
