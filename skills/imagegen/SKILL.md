---
name: imagegen
description: This skill should be used when the user asks to "codex로 이미지 만들어줘", "이미지 생성해줘", "ChatGPT 구독으로 그림 그려줘", "generate image via codex", or any image generation task that should stay on the ChatGPT subscription path (codex responses endpoint) instead of the metered Platform API.
---

# Codex Image Generation

Generate images through the codex `responses` endpoint while reusing the
existing ChatGPT OAuth session. The Node `codex` CLI is not launched per
request, eliminating Node runtime overhead (150~300MB × N parallel) for batch
workloads.

The script reads the OAuth access token from `~/.codex/auth.json` and POSTs a
Responses-style payload to:

```
POST https://chatgpt.com/backend-api/codex/responses
```

It extracts the `image_generation_call` result from the SSE stream and saves
the decoded image. Common HTTP/SSE/auth handling lives in the plugin-shared
`scripts/codex_client.py`, so the same pattern applies to other codex-util
skills.

## How to invoke

Call the script directly from Claude Code. Do not shell out to `codex responses` — this skill replaces that layer.

Requires Python 3.10+ (uses `str | None` and PEP 585 generics).

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/imagegen/scripts/gen_image.py" "<prompt>" -o /path/to/output.png
```

`${CLAUDE_PLUGIN_ROOT}` is exported by Claude Code for plugin runtime. **If the
variable expands to an empty string** in the bash invocation, fall back to the
absolute path of the script — `gen_image.py` lives at `scripts/gen_image.py`
relative to this `SKILL.md`, and the plugin root is the directory that
contains `.claude-plugin/plugin.json`. The script auto-discovers the plugin
root once invoked, so any absolute path to it works.

## When this skill applies

- Image generation must stay on the ChatGPT subscription path (no Platform API metering)
- A pipeline needs many images in parallel and Node CLI fork/exec cost is painful
- A reproducible scriptable path is preferred over an interactive image tool

## Preconditions

1. `codex login` has completed and `~/.codex/auth.json` has `auth_mode = "chatgpt"` with a valid `tokens.access_token`
2. The access token is not expired. Running any `codex` command (or `codex login status`) refreshes it. A 401 from this script means: run `codex` once, or `codex login`.
3. The `codex` CLI is needed only for login / refresh — it is not invoked at image-generation time.

## Parameters

- `prompt` — required text prompt
- `--model` — mainline model used to invoke the tool (default `gpt-5.5`)
- `--size` — for example `1024x1024` (default `1024x1024`)
- `--quality` — `auto|low|medium|high` (default `high`)
- `--background` — `auto|opaque|transparent` (default `auto`)
- `--action` — `auto|generate|edit` (default `generate`)
- `--output` — output path (parent directory is auto-created)
- `--events` — save raw SSE event text for debugging
- `--timeout` — HTTP timeout seconds (default `120`)

## Environment overrides

- `CODEX_AUTH_PATH` — alternative path to `auth.json` (default `~/.codex/auth.json`)

The responses endpoint URL is hardcoded by design to prevent the Bearer token from being redirected to an arbitrary host via environment variables.

## Example

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/imagegen/scripts/gen_image.py" \
  "A cinematic red fox mage in a moonlit forest, detailed fantasy illustration" \
  --model gpt-5.5 \
  --size 1024x1024 \
  --quality high \
  --background opaque \
  --output /tmp/fox_mage.png \
  --events /tmp/fox_mage_events.sse
```

On success the script prints `Saved /path/to/output.png` and the file exists at the requested path.

## Exit codes

- `0` — image saved
- `1` — no `image_generation_call` result found, or base64/IO failure (re-run with `--events sse.log`)
- `2` — auth issue: `auth_mode` not `chatgpt`, missing tokens, or 401 expired
- `3` — non-401 HTTP error from the responses endpoint
- `4` — network error (connection failed, timeout, stream interrupted)

These codes are shared across codex-util skills via `codex_client.py`.

## Troubleshooting

### Exit 2 — auth issue

`~/.codex/auth.json` is not in `chatgpt` mode, or the access token has expired (401). Run `codex` once to refresh, or re-login:

```bash
codex login status   # touches auth.json and refreshes if possible
codex login          # full re-login
```

### Exit 1 — no image result

Re-run with `--events sse.log` and inspect the raw SSE stream. If the response schema changed, `extract_image_b64` (or `_parse_sse_line` in `codex_client.py`) needs an update.

### Transparent background fails

Some model/tool combinations do not support transparent backgrounds. Retry with `--background opaque`.
