"""codex_client — shared helpers for codex-util plugin skills.

모든 codex-util 스킬은 이 모듈을 통해 codex `responses` 엔드포인트와 통신한다.
auth.json 로드, HTTP/SSE 처리, 예외 계층, exit code 가 한 곳에 모여 있어
신규 스킬은 도구별 payload 빌드와 결과 추출만 작성하면 된다.

Loads the OAuth access token from ~/.codex/auth.json (auth_mode=chatgpt) and
streams Server-Sent Events from `chatgpt.com/backend-api/codex/responses` as
parsed event dicts.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import urllib.error
import urllib.request
from typing import Iterator, Optional


# 잘못된 확장자 / 확장자 없는 파일에 대비해 매직 바이트로 image mimetype 을 판정한다.
# extension 만 믿으면 HEIC 가 png 로 라벨링되어 backend 가 거절하는 식의 함정 발생.
def detect_image_mime(data: bytes) -> Optional[str]:
    """첫 16바이트로 image mimetype 을 판정. 모르면 None."""
    if len(data) < 12:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    # HEIC/HEIF: ftyp box at offset 4
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
        return "image/heic"
    return None


def guess_image_mime(path: pathlib.Path) -> str:
    """파일 → image mimetype. 매직 바이트 only.

    extension 기반 추측은 의도적으로 안 함 — `.png` 라고 적힌 텍스트 파일이 silently
    image/png 로 라벨링되어 backend 에 보내지는 함정 차단. 매직 바이트가 없으면
    ValueError 로 caller 가 명확히 거절하도록."""
    try:
        head = path.open("rb").read(16)
    except OSError as e:
        raise ValueError(f"cannot read image header: {path}: {e}")
    mime = detect_image_mime(head)
    if mime:
        return mime
    raise ValueError(
        f"not a recognized image: {path} (first bytes={head[:8]!r}). "
        f"Supported magic bytes: png/jpeg/gif/webp/bmp/heic."
    )


def _resolve_auth_path() -> pathlib.Path:
    """CODEX_AUTH_PATH 를 호출 시점에 resolve 한다 (import 시점이 아니라).

    long-running 프로세스에서 환경변수를 나중에 바꿔도 반영된다.
    """
    return pathlib.Path(
        os.environ.get("CODEX_AUTH_PATH") or "~/.codex/auth.json"
    ).expanduser()

# Bearer 토큰을 임의 호스트로 유출시키지 않도록 엔드포인트는 상수로 고정한다.
# env override 를 허용하면 .env / CI 한 줄로 토큰이 새 나갈 수 있다.
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


# Exit codes — 모든 codex-util 스킬이 공유한다.
EXIT_OK = 0
EXIT_TOOL = 1       # 도구 호출 결과 누락 (e.g. image_generation_call result 없음)
EXIT_AUTH = 2       # auth.json 문제 또는 401
EXIT_HTTP = 3       # non-401 HTTP error
EXIT_NETWORK = 4    # connect 실패, 타임아웃, 스트림 중단


class CodexError(Exception):
    """예외에 종료 코드를 묶어 main 에서 일괄 매핑한다."""

    exit_code = EXIT_TOOL


class CodexAuthError(CodexError):
    exit_code = EXIT_AUTH


class CodexHTTPError(CodexError):
    exit_code = EXIT_HTTP


class CodexNetworkError(CodexError):
    exit_code = EXIT_NETWORK


class CodexToolError(CodexError):
    """도구 결과를 추출하지 못한 경우."""

    exit_code = EXIT_TOOL


def load_codex_auth(path: Optional[pathlib.Path] = None) -> tuple[str, str]:
    """~/.codex/auth.json 에서 (access_token, account_id) 를 읽는다.

    path 가 None 이면 호출 시점의 CODEX_AUTH_PATH 환경변수(또는 ~/.codex/auth.json)
    를 사용한다.
    """
    if path is None:
        path = _resolve_auth_path()
    if not path.exists():
        raise CodexAuthError(
            f"codex auth file not found: {path}\nRun `codex login` first."
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise CodexAuthError(f"codex auth file is not valid JSON: {path} ({e})")
    if data.get("auth_mode") != "chatgpt":
        raise CodexAuthError(
            f"codex auth_mode='{data.get('auth_mode')}', not 'chatgpt'. "
            "This plugin supports ChatGPT OAuth only. Run `codex login`."
        )
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    account = tokens.get("account_id")
    if not access or not account:
        raise CodexAuthError(
            f"access_token/account_id missing in {path}. Re-run `codex login`."
        )
    return access, account


def _parse_sse_line(raw: bytes) -> Optional[dict]:
    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    # SSE("data: {...}") 와 과거 codex CLI 가 내던 raw JSONL 둘 다 수용
    if line.startswith("data:"):
        data = line[5:].strip()
    elif line.startswith("{"):
        data = line
    else:
        return None
    if not data or data == "[DONE]":
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def stream_responses(
    payload: dict,
    access: str,
    account: str,
    *,
    timeout: float = 120.0,
    events_path: Optional[pathlib.Path] = None,
) -> Iterator[dict]:
    """codex responses 엔드포인트를 호출하고 파싱된 SSE 이벤트를 yield 한다.

    호출자는 필요한 event type 만 골라잡고, 끝나면 iteration 을 중단해
    latency 를 절약할 수 있다 (호출자가 break 하면 with-resp 가 닫히고
    finally 에서 events 파일 핸들도 닫힌다).

    events_path 가 주어지면 raw 라인을 그 파일에 함께 저장한다(디버깅용).
    """
    headers = {
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "chatgpt-account-id": account,
    }
    req = urllib.request.Request(
        CODEX_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 401:
            # 401 은 사실상 auth 문제이므로 EXIT_AUTH 로 분류한다
            raise CodexAuthError(
                "codex responses 401 — access_token expired or invalid.\n"
                "Run `codex` once to refresh the token, or `codex login`.\n"
                f"body: {body[:300]}"
            )
        raise CodexHTTPError(f"codex responses {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise CodexNetworkError(f"codex responses connect failed: {e.reason}")
    except (TimeoutError, socket.timeout) as e:
        raise CodexNetworkError(f"codex responses timeout: {e}")

    events_fp = None
    try:
        with resp:
            if events_path is not None:
                events_fp = events_path.open("w", encoding="utf-8")
            for raw in resp:
                if events_fp is not None:
                    events_fp.write(raw.decode("utf-8", errors="replace"))
                event = _parse_sse_line(raw)
                if event is None:
                    continue
                yield event
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        raise CodexNetworkError(f"codex responses stream interrupted: {e}")
    except OSError as e:
        raise CodexToolError(f"events file io error ({events_path}): {e}")
    finally:
        if events_fp is not None:
            try:
                events_fp.close()
            except OSError:
                pass
