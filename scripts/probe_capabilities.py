#!/usr/bin/env python3
"""probe_capabilities — codex responses 엔드포인트의 server-side tool 노출 여부 진단.

ChatGPT 구독 OAuth 로 chatgpt.com/backend-api/codex/responses 를 호출했을 때
어떤 tool 타입이 받아들여지는지 확인한다. imagegen 외에 ask/research/code 등
다음 스킬을 짤 때 추측이 아니라 사실에 기반해 우선순위를 정하기 위해 만들었다.

각 probe 마다:
  - 최소 payload 를 빌드하고 stream_responses 로 SSE 를 끝까지 소비한다.
  - 원시 SSE 라인은 <out>/probe_<name>.sse 로 저장한다.
  - 결과를 classify() 가 enum 으로 분류한다.

stdout 에는 한 줄 요약 + 마지막 표 가 출력되고, JSON 결과는
<out>/probe_results.json 로 저장된다.

실행 예시
---------
    python3 scripts/probe_capabilities.py --out /tmp/codex-probe
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

# 동일 디렉터리의 codex_client 를 import 한다.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from codex_client import (  # noqa: E402  -- after sys.path mutation
    CodexAuthError,
    CodexHTTPError,
    CodexNetworkError,
    EXIT_AUTH,
    EXIT_OK,
    load_codex_auth,
    stream_responses,
)


# Probe 결과 enum.
ACCEPTED = "ACCEPTED"            # 기대한 tool call output item 을 받았다
TOOL_CALL_OTHER = "TOOL_CALL_OTHER"  # tool call 은 있었지만 다른 타입
NO_TOOL_CALL = "NO_TOOL_CALL"    # 스트림 정상 종료, tool call 없음 (텍스트만)
REJECTED_HTTP = "REJECTED_HTTP"  # HTTP 400/422 등 — payload/tool 거절
REJECTED_EVENT = "REJECTED_EVENT"  # 스트림 안에서 error 이벤트
NETWORK = "NETWORK"              # connect 실패 / 타임아웃 / 스트림 중단
AUTH_FAILED = "AUTH_FAILED"      # 401 (mid-stream 만료 포함)
UNKNOWN = "UNKNOWN"              # 분류 불가


@dataclass
class Probe:
    name: str
    description: str
    build_payload: Callable[[], dict]
    expected_call_types: tuple[str, ...] = ()
    # 일부 도구는 별도 timeout 필요 (web_search/code_interpreter 가 길 수 있음)
    timeout: float = 60.0


@dataclass
class ProbeResult:
    name: str
    status: str
    detail: str = ""
    duration_s: float = 0.0
    output_item_types: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    raw_event_count: int = 0


# --- payload builders ------------------------------------------------------


_USER = lambda text: [  # noqa: E731
    {"role": "user", "content": [{"type": "input_text", "text": text}]}
]


def _base(model: str = "gpt-5.5") -> dict:
    return {
        "model": model,
        "store": False,
        "stream": True,
    }


def build_text_baseline() -> dict:
    p = _base()
    p["instructions"] = "Reply with the single word: ok"
    p["input"] = _USER("ping")
    return p


def build_image_generation() -> dict:
    """imagegen 와 같은 모양 — control."""
    p = _base()
    p["instructions"] = "Use the image_generation tool to create the requested image."
    p["input"] = _USER("a tiny red dot on white background")
    p["tools"] = [{
        "type": "image_generation",
        "size": "1024x1024",
        "quality": "low",
        "background": "auto",
    }]
    p["tool_choice"] = {"type": "image_generation"}
    return p


def build_web_search() -> dict:
    p = _base()
    p["instructions"] = (
        "Use the web_search tool to look up the current day's weather in Seoul. "
        "Return one short sentence with the result."
    )
    p["input"] = _USER("Search the web for: weather in Seoul today.")
    p["tools"] = [{"type": "web_search"}]
    p["tool_choice"] = "auto"
    return p


def build_web_search_preview() -> dict:
    """Platform Responses API 의 별칭. codex 쪽이 받는지 확인."""
    p = _base()
    p["instructions"] = (
        "Use the web_search_preview tool to look up: 'site:python.org python 3.13 release'. "
        "Return one short sentence."
    )
    p["input"] = _USER("Search the web for python 3.13 release notes.")
    p["tools"] = [{"type": "web_search_preview"}]
    p["tool_choice"] = "auto"
    return p


def build_code_interpreter() -> dict:
    p = _base()
    p["instructions"] = (
        "Use the code_interpreter tool to compute (2**31) - 1 in Python and return the integer result."
    )
    p["input"] = _USER("Compute (2**31) - 1 with Python and reply with the integer.")
    p["tools"] = [{"type": "code_interpreter", "container": {"type": "auto"}}]
    p["tool_choice"] = "auto"
    return p


def build_file_search_no_store() -> dict:
    """vector_store_ids 없이 보낸다 — 도구 자체가 받아들여지는지 + 어떻게 거절되는지."""
    p = _base()
    p["instructions"] = "Use the file_search tool to find the document about 'hello world'."
    p["input"] = _USER("Search the file store for 'hello world'.")
    p["tools"] = [{"type": "file_search"}]
    p["tool_choice"] = "auto"
    return p


PROBES: list[Probe] = [
    Probe("text_baseline", "tools 없이 텍스트 완성", build_text_baseline,
          expected_call_types=()),
    Probe("image_generation", "control — 알려진 작동 도구", build_image_generation,
          expected_call_types=("image_generation_call",), timeout=180.0),
    Probe("web_search", "server-side web 검색 (Platform 표기)", build_web_search,
          expected_call_types=("web_search_call",), timeout=120.0),
    Probe("web_search_preview", "server-side web 검색 (preview 별칭)",
          build_web_search_preview,
          expected_call_types=("web_search_call", "web_search_preview_call"),
          timeout=120.0),
    Probe("code_interpreter", "server-side Python 샌드박스", build_code_interpreter,
          expected_call_types=("code_interpreter_call",), timeout=120.0),
    Probe("file_search", "vector_store_ids 없이 보내기", build_file_search_no_store,
          expected_call_types=("file_search_call",), timeout=60.0),
]


# --- classification --------------------------------------------------------


def classify(events: list[dict], probe: Probe) -> ProbeResult:
    """소비된 events 리스트를 분류해 ProbeResult 로 만든다."""
    output_item_types: list[str] = []
    error_messages: list[str] = []
    saw_tool_call_match = False
    saw_other_tool_call = False

    for ev in events:
        t = ev.get("type") or ""
        # error event 형태: type 에 'error' 포함, 혹은 'response.failed'
        if "error" in t or t == "response.failed":
            err = ev.get("error") or ev.get("message") or ev
            error_messages.append(json.dumps(err)[:500])
        if t == "response.output_item.done":
            item = ev.get("item") or {}
            itype = item.get("type") or ""
            if itype:
                output_item_types.append(itype)
            if itype.endswith("_call"):
                if probe.expected_call_types and itype in probe.expected_call_types:
                    saw_tool_call_match = True
                else:
                    saw_other_tool_call = True

    # error 가 있으면 다른 모든 신호보다 우선한다.
    # tool_call 받고 나서 response.failed 가 와도 ACCEPTED 로 잘못 분류하지 않도록.
    if error_messages:
        status = REJECTED_EVENT
        detail = error_messages[0][:300]
    elif saw_tool_call_match:
        status = ACCEPTED
        detail = f"observed expected tool call: {','.join(probe.expected_call_types)}"
    elif probe.expected_call_types and saw_other_tool_call:
        status = TOOL_CALL_OTHER
        detail = f"got other tool calls: {output_item_types}"
    elif probe.expected_call_types:
        # 도구가 invalid 면 보통 HTTP 400 으로 떨어진다 (REJECTED_HTTP).
        # 여기까지 왔는데 expected call 도 없고 에러도 없다 = 모델이 도구를 안 썼거나
        # 서버가 silently 무시했음.
        status = NO_TOOL_CALL
        detail = f"stream finished without expected call. items={output_item_types}"
    else:
        # text baseline — 최소 message item 1개는 있어야 정상 완료로 본다.
        if "message" in output_item_types:
            status = ACCEPTED
            detail = f"text completion ok. items={output_item_types}"
        else:
            status = NO_TOOL_CALL
            detail = f"text baseline produced no message item. items={output_item_types}"

    return ProbeResult(
        name=probe.name,
        status=status,
        detail=detail,
        output_item_types=output_item_types,
        error_messages=error_messages,
        raw_event_count=len(events),
    )


# --- runner ----------------------------------------------------------------


def run_probe(probe: Probe, access: str, account: str, out_dir: pathlib.Path) -> ProbeResult:
    sse_path = out_dir / f"probe_{probe.name}.sse"
    payload = probe.build_payload()
    started = time.monotonic()
    try:
        events = list(stream_responses(
            payload, access, account,
            timeout=probe.timeout,
            events_path=sse_path,
        ))
    except CodexAuthError as e:
        # mid-stream 401 (토큰이 호출 중 만료) 도 여기로 옴.
        result = ProbeResult(
            name=probe.name, status=AUTH_FAILED, detail=str(e)[:500],
        )
    except CodexHTTPError as e:
        result = ProbeResult(
            name=probe.name, status=REJECTED_HTTP, detail=str(e)[:500],
        )
    except CodexNetworkError as e:
        result = ProbeResult(
            name=probe.name, status=NETWORK, detail=str(e)[:500],
        )
    except Exception as e:  # codex_client 에서 못 잡은 케이스
        result = ProbeResult(
            name=probe.name, status=UNKNOWN, detail=f"{type(e).__name__}: {e}"[:500],
        )
    else:
        result = classify(events, probe)

    result.duration_s = time.monotonic() - started
    return result


def format_table(results: list[ProbeResult]) -> str:
    lines = []
    name_w = max(len(r.name) for r in results)
    status_w = max(len(r.status) for r in results)
    header = f"{'PROBE'.ljust(name_w)}  {'STATUS'.ljust(status_w)}  TIME   DETAIL"
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        time_s = f"{r.duration_s:5.1f}s"
        detail = r.detail.replace("\n", " ")
        if len(detail) > 80:
            detail = detail[:77] + "..."
        lines.append(f"{r.name.ljust(name_w)}  {r.status.ljust(status_w)}  {time_s}  {detail}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--out", default="/tmp/codex-probe",
        help="결과 저장 디렉토리 (기본 /tmp/codex-probe)",
    )
    ap.add_argument(
        "--only", help="콤마 구분 probe 이름만 실행 (예: web_search,code_interpreter)",
    )
    ap.add_argument(
        "--skip", help="콤마 구분 probe 이름 제외 (예: image_generation)",
    )
    ap.add_argument(
        "--list", action="store_true", help="probe 목록만 출력하고 종료",
    )
    args = ap.parse_args()

    if args.list:
        for p in PROBES:
            print(f"{p.name:24s} {p.description}")
        return 0

    selected = PROBES
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        selected = [p for p in selected if p.name in wanted]
        missing = wanted - {p.name for p in selected}
        if missing:
            sys.stderr.write(f"unknown probes: {sorted(missing)}\n")
            return 2
    if args.skip:
        skipped = {s.strip() for s in args.skip.split(",") if s.strip()}
        selected = [p for p in selected if p.name not in skipped]

    if not selected:
        sys.stderr.write("no probes selected.\n")
        return 2

    out_dir = pathlib.Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        access, account = load_codex_auth()
    except CodexAuthError as e:
        sys.stderr.write(f"{e}\n")
        return EXIT_AUTH

    results: list[ProbeResult] = []
    for probe in selected:
        sys.stdout.write(f"[run] {probe.name} ... ")
        sys.stdout.flush()
        r = run_probe(probe, access, account, out_dir)
        results.append(r)
        sys.stdout.write(f"{r.status} ({r.duration_s:.1f}s)\n")

    print()
    print(format_table(results))
    print()
    print(f"raw SSE per probe : {out_dir}/probe_<name>.sse")
    results_path = out_dir / "probe_results.json"
    results_path.write_text(json.dumps(
        [r.__dict__ for r in results], indent=2, ensure_ascii=False,
    ))
    print(f"json summary     : {results_path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
