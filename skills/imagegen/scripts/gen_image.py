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
import json
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
    guess_image_mime,
    load_codex_auth,
    stream_responses,
)


def _load_input_image(spec: str) -> dict:
    """`--input-image` 인자 → input_image content 객체. 파일 또는 http(s):// URL.

    로컬 파일은 매직 바이트로 mimetype 을 정확히 판정 (PNG silent fallback 회피)."""
    if spec.startswith(("http://", "https://", "data:")):
        return {"type": "input_image", "image_url": spec}
    p = pathlib.Path(spec).expanduser()
    if not p.is_file():
        raise CodexToolError(f"--input-image: not a file: {p}")
    try:
        mime = guess_image_mime(p)
    except ValueError as e:
        raise CodexToolError(f"--input-image: {e}")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"}


def build_payload(args: argparse.Namespace) -> dict:
    tool = {
        "type": "image_generation",
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
    }
    # action 미명시 시 input_image 유무로 자동 판정 (argparse default 와 explicit
    # 값이 구분 가능하도록 default=None 으로 두고 여기서 결정).
    effective_action = args.action
    if effective_action is None:
        effective_action = "edit" if args.input_image else "generate"
    tool["action"] = effective_action

    user_content: list[dict] = []
    for img in (args.input_image or []):
        user_content.append(_load_input_image(img))
    user_content.append({"type": "input_text", "text": args.prompt})

    instructions = (
        "Use the image_generation tool to edit the provided image based on the prompt. "
        "Return the image generation result."
        if args.input_image else
        "Use the image_generation tool to create the requested image. "
        "Return the image generation result."
    )
    return {
        "model": args.model,
        "instructions": instructions,
        "input": [{"role": "user", "content": user_content}],
        "tools": [tool],
        "tool_choice": {"type": "image_generation"},
        "store": False,
        "stream": True,
    }


def extract_image_b64(events, *, drain: bool) -> tuple[str, dict | None]:
    """SSE 이벤트 stream 에서 image_generation_call result(base64) 와 usage dict 추출.

    drain=True (events 파일 보존 모드) 이면 stream 을 끝까지 소비하고 마지막
    result 를 반환한다(다중 결과는 경고). drain=False 이면 첫 result 발견 즉시
    break — 단 usage 는 그 시점까진 None 일 수 있다 (response.completed 가 더 뒤).
    """
    image_b64: str | None = None
    usage: dict | None = None
    saw_multiple = False
    for event in events:
        et = event.get("type") or ""
        # ask.py 와 동일한 휴리스틱: stream-level error / response.failed 는 즉시
        # fatal 로 surface (moderation_blocked 같은 backend 에러가 silent 로
        # "no image result" 로 묻히는 걸 막는다).
        if "error" in et or et == "response.failed":
            err = event.get("error") or event.get("response", {}).get("error") or event
            raise CodexToolError(f"stream error event: {json.dumps(err)[:300]}")
        if et == "response.completed":
            usage = (event.get("response") or {}).get("usage") or usage
            continue
        if et != "response.output_item.done":
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
    return image_b64, usage


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
        default=None,
        help="Image tool action. If omitted, picked automatically: `edit` when "
             "--input-image is given, otherwise `generate`. Pass explicitly to override.",
    )
    parser.add_argument(
        "--input-image",
        action="append",
        help="Input image to edit/reference. File path or http(s):// URL "
             "(repeatable). Local files are auto-encoded as base64 data URLs. "
             "Implies --action edit unless --action is set explicitly.",
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
    parser.add_argument(
        "--show-usage",
        action="store_true",
        help="Print response.completed usage to stderr "
             "(input/cached/reasoning/output tokens). Implies a full SSE drain "
             "since response.completed arrives at the very end of the stream.",
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
        # --show-usage 는 response.completed 가 stream 끝에 오므로 drain 필요.
        # 둘 중 하나라도 켜져 있으면 drain.
        drain = (events_path is not None) or args.show_usage
        image_b64, usage = extract_image_b64(events, drain=drain)
        output_path = pathlib.Path(args.output)
        write_image(image_b64, output_path)
        print(f"Saved {output_path}")
        if args.show_usage and usage:
            cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
            sys.stderr.write(
                f"usage: input={usage.get('input_tokens',0)} "
                f"(cached={cached}) "
                f"output={usage.get('output_tokens',0)} "
                f"(reasoning={reasoning}) "
                f"total={usage.get('total_tokens',0)}\n"
            )
        return EXIT_OK
    except CodexError as e:
        sys.stderr.write(f"{e}\n")
        if isinstance(e, CodexToolError) and not args.events:
            sys.stderr.write("Re-run with --events sse.log to inspect raw events.\n")
        return e.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
