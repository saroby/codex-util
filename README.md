# codex-util

Use Codex AI features inside Claude Code by calling the ChatGPT subscription path
(`chatgpt.com/backend-api/codex/responses`) directly. Reuses the existing
`codex login` OAuth session — no Platform API key, no Node CLI fork per call.

## Skills

- `imagegen` — image generation via the `image_generation` tool
- `imageprompt` — prompt-routing and prompt-crafting rules for `imagegen`
  when a request has control risk such as visible text, edits, multiple
  input images, IP cleanup, exact layout, or tag-soup input
- `ask` — pure-text consultation / second-opinion / web research via GPT-5.
  `--web` flips on the server-side `web_search` tool (verified working on this
  endpoint), `--json` requests JSON-only output (instruction-based, best-effort
  — `response_format` support on this endpoint is unverified),
  `--show-citations` prints a citations footer (routed to stderr when `--json`
  is on so stdout stays parseable).

More skills may follow as thin wrappers over `ask` (e.g. `review` for diff
review) using the shared `scripts/codex_client.py` helper.

## Verified server-side tool whitelist

The `chatgpt.com/backend-api/codex/responses` endpoint with ChatGPT OAuth
accepts a **narrow** tool surface — not Platform Responses parity. Verified
by `scripts/probe_capabilities.py`:

| Tool                | Status | Used by |
|---------------------|--------|---------|
| (no tools, text)    | OK     | `ask` (default) |
| `image_generation`  | OK     | `imagegen` |
| `web_search`        | OK     | `ask --web` |
| `web_search_preview`| 400    | (Platform alias, rejected) |
| `code_interpreter`  | 400    | (rejected — `Unsupported tool type`) |
| `file_search`       | 400    | (rejected — `Unsupported tool type`) |

Re-run the probe at any time:

```bash
python3 scripts/probe_capabilities.py --out /tmp/codex-probe
```

Treat this as a regression check: if ChatGPT silently changes the schema,
existing skills will break, and this script is the fastest way to find out.

## Prerequisites

- Python 3.10+ (uses `str | None` and PEP 585 generics)
- `codex login` completed → `~/.codex/auth.json` has `auth_mode = "chatgpt"` and a valid `tokens.access_token`
- The Codex CLI is needed only for login / refresh (not at runtime)

## Installation

1. Add `codex-util` as an entry in a local Claude Code marketplace JSON,
   pointing `source` at `<path-to-this-repo>` (the directory containing
   `.claude-plugin/plugin.json`).
2. Enable the plugin in Claude Code settings.
3. Verify with `/help` and by triggering the skill (e.g. "codex로 이미지 만들어줘").

## License

MIT
