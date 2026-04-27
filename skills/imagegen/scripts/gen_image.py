#!/usr/bin/env python3
"""gen_image.py — codex responses 엔드포인트를 통해 이미지를 생성한다.

공용 codex_client 가 auth/HTTP/SSE 처리를 담당하고, 이 스크립트는
image_generation 도구 페이로드 빌드와 결과 추출/저장만 담당한다.

전제 조건
  - `codex login` 이 완료되어 ~/.codex/auth.json 의 auth_mode = "chatgpt"
  - access_token 이 유효할 것 (만료 시 401 → EXIT_AUTH, `codex login` 안내)
"""

from __future__ import annotations

import argparse
import base64
import binascii
import pathlib
import sys

# 플러그인 루트의 scripts/ 를 sys.path 에 올려 codex_client 를 찾는다.
# parents[3] hard-coding 대신 .claude-plugin/plugin.json 가 있는 디렉토리를
# walk 해서 찾으면 디렉토리가 한 단계 옮겨져도 견고하다.
def _find_plugin_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".claude-plugin" / "plugin.json").is_file():
            return parent
    raise RuntimeError(
        f"could not locate codex-util plugin root (.claude-plugin/plugin.json) "
        f"from {here}. is the script outside the plugin layout?"
    )


_PLUGIN_ROOT = _find_plugin_root()
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))

from codex_client import (  # noqa: E402  -- after sys.path mutation
    CodexError,
    CodexToolError,
    EXIT_OK,
    load_codex_auth,
    stream_responses,
)


def build_payload(args: argparse.Namespace) -> dict:
    tool = {
        "type": "image_generation",
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
    }
    if args.action:
        tool["action"] = args.action
    return {
        "model": args.model,
        "instructions": (
            "Use the image_generation tool to create the requested image. "
            "Return the image generation result."
        ),
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": args.prompt}],
            }
        ],
        "tools": [tool],
        "tool_choice": {"type": "image_generation"},
        "store": False,
        "stream": True,
    }


def extract_image_b64(events, *, drain: bool) -> str:
    """SSE 이벤트 stream 에서 image_generation_call result(base64) 를 추출한다.

    drain=True (events 파일 보존 모드) 이면 stream 을 끝까지 소비하고 마지막
    result 를 반환한다(다중 결과는 경고). drain=False 이면 첫 결과 즉시 반환.
    """
    image_b64: str | None = None
    saw_multiple = False
    for event in events:
        if event.get("type") != "response.output_item.done":
            continue
        item = event.get("item") or {}
        if item.get("type") != "image_generation_call":
            continue
        result = item.get("result")
        if not result:
            continue
        if image_b64 is not None:
            saw_multiple = True
        image_b64 = result
        if not drain:
            break
    if saw_multiple:
        sys.stderr.write(
            "warning: multiple image_generation_call results — kept the last one.\n"
        )
    if not image_b64:
        raise CodexToolError("No image_generation_call result found.")
    return image_b64


def write_image(image_b64: str, output_path: pathlib.Path) -> None:
    try:
        data = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise CodexToolError(f"image base64 decode failed: {e}")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)
    except OSError as e:
        raise CodexToolError(f"failed to write image to {output_path}: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an image via the codex responses endpoint (direct HTTP, no Node CLI)."
    )
    parser.add_argument("prompt", help="Image prompt")
    parser.add_argument("-o", "--output", default="image.png", help="Output image path")
    parser.add_argument("--model", default="gpt-5.5", help="Mainline model used to call the tool")
    parser.add_argument("--size", default="1024x1024", help="Image size, for example 1024x1024")
    parser.add_argument(
        "--quality",
        default="high",
        choices=("auto", "low", "medium", "high"),
        help="Image quality",
    )
    parser.add_argument(
        "--background",
        default="auto",
        choices=("auto", "opaque", "transparent"),
        help="Image background",
    )
    parser.add_argument(
        "--action",
        choices=("auto", "generate", "edit"),
        default="generate",
        help="Image tool action",
    )
    parser.add_argument(
        "--events",
        help="Optional path to save raw SSE event text",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="HTTP timeout seconds (default: 240)",
    )
    args = parser.parse_args()

    try:
        access, account = load_codex_auth()
        payload = build_payload(args)
        events_path = pathlib.Path(args.events) if args.events else None
        events = stream_responses(
            payload,
            access,
            account,
            timeout=args.timeout,
            events_path=events_path,
        )
        image_b64 = extract_image_b64(events, drain=events_path is not None)
        output_path = pathlib.Path(args.output)
        write_image(image_b64, output_path)
        print(f"Saved {output_path}")
        return EXIT_OK
    except CodexError as e:
        sys.stderr.write(f"{e}\n")
        if isinstance(e, CodexToolError) and not args.events:
            sys.stderr.write("Re-run with --events sse.log to inspect raw events.\n")
        return e.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
