# codex-util

Use Codex AI features inside Claude Code by calling the ChatGPT subscription path
(`chatgpt.com/backend-api/codex/responses`) directly. Reuses the existing
`codex login` OAuth session — no Platform API key, no Node CLI fork per call.

## Skills

- `imagegen` — image generation via the `image_generation` tool

More skills will be added (chat, research, code, vision …) using the shared
`scripts/codex_client.py` helper.

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
