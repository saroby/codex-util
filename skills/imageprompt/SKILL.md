---
name: imageprompt
description: Run before `imagegen` when an image request has control risk — visible text, edits, multi-image inputs, IP/brand cleanup, ambiguous ontology ("fox mage", "knight in armor"), exact counts, or tag-soup / fragmented aesthetic input that needs normalization. Triggers on "이미지 편집해줘", "글자가 들어간", "사인", "로고", "캐릭터 일관성", "여러 이미지로", "edit this image", "image with text", "character consistency", "이미지 프롬프트 다듬어줘", "improve the image prompt". For coherent NL requests with no control risk (e.g., "에스프레소 내리는 바리스타 사진", "노인 인물 사진. 따뜻한 골든아워 분위기"), call `imagegen` directly. Three named output modes — every output starts with the literal mode label: `PASS_0_MINIMAL:` (passthrough + ≤ 1 scene-conditional exclusion), `PASS_1_LOCK:` (medium / EXACT TEXT / inline constraints / edit verbs / multi-image indexing / IP cleanup), `PASS_1_LOCK + PASS_2_COMPOSE:` (lock + slot-granular composition; only for ambiguous-ontology lock-insufficient cases or tag-soup normalization). No second LLM rewrite call.
---

# Image Prompt Crafting (for `gpt-image-2`)

**Long ≠ better.** `gpt-image-2` can follow detailed prompts but
gives better results from short coherent NL in strong-prior domains
(café, portrait, landscape, food, product). This skill operates by
**control risk** — the presence of a known failure mode the model
would commit if left to its defaults. With no control risk, the
skill does *less*, not more, and emits `PASS_0_MINIMAL`.

The routing maps to documented GPT Image limitations: visible text,
recurring identity/brand elements, and precise spatial composition
need explicit locks and visual verification. Do not add detail just
because the model is powerful or because the skill was loaded.

No second LLM rewrite layer — would risk corrupting precise
directives and routinely auto-apply composition invention.

## Output format

Every output begins with a literal mode label:

- `PASS_0_MINIMAL: <prompt>`
- `PASS_1_LOCK: <prompt>`
- `PASS_1_LOCK + PASS_2_COMPOSE: <prompt>`

Strip the prefix before passing to `imagegen`. The label is for the
caller to track the routing decision.

## Decision flow (control risk)

| Risk | Mode |
|---|---|
| None — coherent NL, no failure mode below | `PASS_0_MINIMAL` |
| Failure mode + lock suffices | `PASS_1_LOCK` |
| Lock alone leaves wrong-cluster default OR tag-soup needs normalization | `PASS_1_LOCK + PASS_2_COMPOSE` |

Top-down — first match wins:

1. **Visible text** in image (signs, logos, UI, infographics)? →
   `PASS_1` + `EXACT TEXT` (L2).
2. **Edit request** (input image + change)? → `PASS_1` + edit verbs +
   preserve list (L4).
3. **Multi-image input** (style transfer / character / object)? →
   `PASS_1` + image indexing (L5).
4. **IP / brand / celebrity-likeness with real legal risk** (Marvel,
   Nike, living artist, celebrity)? → `PASS_1` + L6 cleanup. *Platform
   / format anchors* (`Instagram`, `TikTok`, `polaroid`, `Vogue
   editorial`, `lookbook`, `street photography`, `LinkedIn headshot`)
   are aesthetic clusters, not IP risks — fall through to step 8 unless
   `PASS_1` fires for other reasons.
5. **Exact counts / spatial layout** ("three people holding one
   umbrella")? → `PASS_1` with explicit count/placement.
6. **Ambiguous ontology** ("fox mage", "knight in armor")? → `PASS_1`
   medium lock; **add `PASS_2`** only if lock alone leaves the wrong
   default cluster active.
7. **Tag-soup or fragmented aesthetic** (`masterpiece, 8k, neon`)? →
   `PASS_1` normalize + `PASS_2` only on slots the user actually named.
8. **None of above** + coherent NL? → **`PASS_0_MINIMAL`**.

User-supplied aesthetic in coherent NL is **not** a failure mode — the
model handles it natively. That's `PASS_0`.

## Workflow

1. User asks for image (generate / edit) **or** refined prompt only
   ("이미지 프롬프트 다듬어줘", "improve this prompt")
2. Run decision flow → pick mode
3. Construct prompt per mode rules
4. Hand off:
   - **Generate image**: strip mode label, call `imagegen`
   - **Refined prompt only**: return full output (with mode label) to
     the user — do not call `imagegen`

## `PASS_0_MINIMAL` — Passthrough

```
PASS_0_MINIMAL: <user prompt verbatim>[. <one scene-conditional exclusion>]
```

Optional appended exclusion (≤ 1 line):
- Default candidate: `No on-screen text, no logos, no watermark.` —
  add only when scene wouldn't naturally have these.
- **Skip** for scenes that *should* have them (signs, packaging,
  storefronts, branded products).
- **Never auto-append `no other people`** — too prone to scene
  conflicts (cafés, weddings, sports naturally include people). Only
  add if user signaled isolation ("solo", "alone", "empty room").
- If unsure, append nothing.

Do **not** expand: style, camera, demographics, setting, ethnicity,
clothing, lens, mood, composition. Even one extra sentence is too
much. `PASS_0` exists to actively *not* invent.

## `PASS_1_LOCK` rules

Output: `PASS_1_LOCK: <constructed prompt>`

### L1 — Single medium

Pick exactly one: `photograph`, `oil painting`, `watercolor`, `3D
render`, `vector illustration`, `pixel art`. Declare in the first
sentence; stay consistent throughout.

### L2 — `EXACT TEXT` directive

`EXACT TEXT: "<verbatim>" in <weight> <family>, <placement>, <color on color>.`
Add `<alignment>`, `<line breaks>`, or `only text in the image` when
layout matters. Do not paraphrase / omit punctuation / translate.

Edge cases (verified weak):
- Hard spelling → letter-by-letter: `Render exactly "QIXR". Letters: Q I X R.`
- Long strings / dense copy / multi-line → still weak; keep short.
- CJK / Arabic / Hebrew → known-weak; verify visually; expect reruns.
- Don't ask for "exact kerning" — use `clean even spacing`.

Best pattern: one short string, quoted, large, high contrast, simple
sans-serif.

### L3 — Inline constraints (scene-conditional)

No negative-prompt field. Encode inline as NL. Prefer positive
substitutes (`solid black background`) over negation (`no background`).

**Conditional, not global:**
- `no on-screen text` — only when scene shouldn't have text
- `no logos` — skip for product / storefront / packaging
- `no other people` — only if user signaled isolation; skip for
  crowd / event / urban scenes
- `no watermark` — generally safe

Suppress what the user does NOT want, do not globally sterilize every
output.

### L4 — Edit-mode bifurcation

**Surgical** (small, conservative):
```
Replace only X with Y. Do not improve, restyle, recompose, upscale,
relight, recolor anything else, crop, or add new elements. Keep
everything else unchanged: <expanded preserve list>.
```

**Transformative** (input as loose reference):
```
Use the input only as loose reference. Redesign the scene to <new>,
while preserving <invariant list>.
```

Edit verbs: `replace only`, `remove only`, `change only`, `add only`,
`keep unchanged`, `do not alter`.

Expanded preserve list — include non-obvious image properties:
subject pose, camera angle, crop, framing, lens feel, lighting
direction + intensity + color temperature, shadows, saturation /
contrast, background objects, all visible text, logo placement,
source's medium and rendering style.

Pair with `imagegen --input-image <path>` (auto-sets `--action edit`).

### L5 — Multi-image input

Index references explicitly:
`Image 1 = base photo. Image 2 = style reference. Image 3 = object.`

- **Identity-critical / base image first**.
- **Style transfer**: name *transferable attributes* — `palette,
  line weight, brush texture, lighting, material finish, paper grain`.
  "Use same style" alone is weak.
- **Character consistency**: anchor + invariant list (face shape,
  hair, signature outfit). Drift across many gens is real.
- **Object insertion**: source / target location / lighting adaptation.

`gpt-image-2` always processes input images at high fidelity per
docs — there is no `--input-fidelity` to set.

### L6 — IP cleanup (real legal risk only)

- "in the style of <living artist>" → descriptive traits (`soft
  watercolor edges, muted pastel palette`)
- "<Brand>-style ad" / "Marvel-style" → descriptive genre
- "photo of <celebrity>" → `fictional adult subject` (safety
  substitution; *not* aesthetic enrichment)
- Trademark-heavy ads → strip + describe product class generically

**Platform / format anchors are NOT IP risks.** When `PASS_1` fires
for *other* reasons but a platform anchor is in the prompt, preserve
the cluster's *visual fingerprint* in NL — do not collapse to a
generic "social media aesthetic":

- `Instagram` → lifestyle scene (café / urban / beach), 3/4 or
  full-body framing, natural daylight, candid-but-styled pose, warm
  filter, vertical composition
- `polaroid` → square format, warm color shift, soft blacks,
  instant-camera grain, white border, subject-centered casual pose
- `Vogue editorial` → high-fashion stylized portrait, dramatic
  lighting, conceptual styling, magazine retouching
- Other anchors (`LinkedIn headshot`, `street photography`,
  `lookbook`) → match cluster fingerprint, not generic commercial
  portrait

This is C3's stylization-signal preservation applied at the
platform-anchor level.

### L7 — CLI hygiene

- `--size` over prose. `--size auto` if no orientation specified;
  `1024x1024` / `1024x1536` / `1536x1024` safest fixed.
- Constraints: max edge ≤ 3840, multiples of 16, ratio ≤ 3:1, total
  pixels 655,360 — 8,294,400. Above 2560×1440 = experimental.
- `--action auto` unless workflow forces (`edit` for in-place,
  `generate` when input images are references).
- Use direct visual verbs (`Generate`, `Draw`, `Edit`, `Replace only`)
  in constructed prompts.

## `PASS_2_COMPOSE` (conditional)

Output: `PASS_1_LOCK + PASS_2_COMPOSE: <prompt with composition>`

Fires only on:
- Decision step 6 — ambiguous ontology where lock alone leaves wrong
  cluster (fill slots needed to anchor)
- Decision step 7 — tag-soup needs normalization (fill *only* slots
  the user actually named)

Does NOT fire on:
- Coherent NL (always `PASS_0`)
- Strong-prior + neutral request (always `PASS_0`)
- Visible-text request (`EXACT TEXT` does the work)

### Why slot granularity matters

Filling slots the user did not imply moves the sample away from the
prior's high-quality mean and amplifies bias. Empty slots stay empty.

### C1 — Detailed NL prose

2–4 sentences for the slots you are filling. Sensory and concrete.

### C2 — Order

`scene → subject → details → composition → lighting → style → text → constraints`

### C3 — Photographic vocabulary, not hype words

Use lens (`35 mm`, `85 mm`), aperture (`shallow depth of field`),
time (`golden hour`), film (`Kodak Portra 400`), grain.

Avoid `masterpiece`, `8k`, `ultra-detailed`, `trending on artstation`
— convert intent to NL (`crisp focus, fine surface detail, rich
tonal range`).

**Preserve hype-token stylization signal.** Hype tokens often carry
an *aesthetic preference* (typically stylized concept art / digital
painting, not documentary photo). Drop the literal tokens but
preserve the signal — it can influence both medium choice (L1) and
style traits:

- `masterpiece, 8k, dramatic` → `stylized cinematic rendering,
  high-contrast color grading, rich tonal range`
- `cyberpunk, neon, masterpiece` → consider `stylized digital
  illustration` medium, not photo
- `golden hour, dreamy, masterpiece` → `cinematic photograph` +
  `stylized color grading`

Do not silently default to documentary photograph when hype tokens
implied stylization. This is the one place hype-token *signal* (not
literal text) is allowed to influence slot choice.

## Field rules (across all modes)

- **Do not invent demographics.** Ethnicity, gender, age, body type —
  only when user specified. *Exception*: L6 safety rewrites may
  introduce `fictional adult subject` for IP/likeness.
- **Entropy preservation.** Slots the user did not fill stay empty so
  the model samples from its learned distribution.
- **One-axis enhancement.** User specified one axis (e.g., golden
  hour) → don't auto-add others.
- **No meta-instructions.** No "think step by step" / "be detailed"
  — wastes budget, leaks into `revised_prompt`.

## Moderation — refuse-or-rewrite patterns

Likely refused or rewrite-required:
- Sexualized minors / explicit sex / graphic gore / self-harm
- Extremism, hate symbols, slurs as endorsement
- Targeted political persuasion / illegal-activity instructions
- Realistic public-figure likeness misuse
- "in the style of <living artist>" / trademark-heavy ads
- Deception / fraud assets

Safer rewrite (apply L6): fictional adult subjects, non-graphic,
original style, no real likeness, no trademarks.

## Anti-patterns (do NOT use)

- Attention weighting: `(red:1.3)`, `[blue:0.5]`
- `BREAK` keyword (CLIP hack)
- Tag soup: `masterpiece, 8k, ultra-detailed, trending on artstation`
- Booru tags: `1girl, solo, long_hair, ...`
- LoRA / embedding invocations (`<lora:foo:0.7>`)
- "negative prompt" parameter — encode inline NL instead
- Aspect ratio in prose as the only control — pass `--size`
- Living-studio / artist names — see L6
- **Inventing aesthetic specificity the user did not imply** — most
  common LLM-agent failure (demographics, lens, mood, composition,
  time of day)
- **Auto-appending global exclusions** that conflict with the scene
  (`no other people` on a busy café, `no logos` on a storefront)
- Meta-instructions to the model
- Upgrading `PASS_0` to `PASS_1`/`PASS_2` just because the skill loaded

## Examples

### `PASS_0_MINIMAL` — coherent NL, no control risk

User: `에스프레소 내리는 바리스타 사진`

```
PASS_0_MINIMAL: 에스프레소 내리는 바리스타 사진. No on-screen text, no logos, no watermark.
```

`no other people` omitted (cafés naturally have people; user didn't
signal isolation). Don't invent tattoos / lighting / lens / mood.

### `PASS_1_LOCK` — visible text

User: `"OPEN" 사인 이미지`

```
PASS_1_LOCK: A photograph of a small enamel storefront sign. EXACT TEXT: "OPEN" in heavy condensed sans, centered, warm white on deep navy enamel, only text in the image. No additional text, no logos.
```

### `PASS_1_LOCK + PASS_2_COMPOSE` — ambiguous ontology

User: `붉은 여우 마법사 이미지 만들어줘`

(`PASS_2` because medium lock alone might still drift to
anthropomorphic-photo territory.)

```
PASS_1_LOCK + PASS_2_COMPOSE: A 35 mm cinematic photograph of a red fox in mid-stride through a moonlit pine forest. Low-angle composition, fox offset to the right third, fog at ankle height. Cool moonlight rim-lights the back, warm bioluminescent fungi along the floor. Shallow DoF on a 50 mm equivalent, slight motion blur on the tail tip, light film grain. No watermark, no human figures, no on-screen text.
```

## Hand-off to imagegen

Strip the `<mode>:` prefix, then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/imagegen/scripts/gen_image.py" \
  "<prompt>" --size 1024x1024 --quality high --output /path/out.png
```

For edits: add `--input-image <path>`. For multi-image: repeat
`--input-image`. See `skills/imagegen/SKILL.md` for full params,
exit codes, and troubleshooting.

**Debug**: `--events sse.log`, then `grep revised_prompt sse.log`
shows the model's internal prompt interpretation — best artifact for
understanding why output drifted.

## Why no LLM rewriter (for AI agents)

A second LLM rewrite would: add latency / token cost, risk silent
corruption of precise directives (`EXACT TEXT`, edit preserve, style
locks), need its own guard list, and routinely auto-apply `PASS_2`
invention without the control-risk gate (the failure mode this skill
explicitly avoids).

Future non-LLM callers (headless pipelines, batch generators) can
opt into a `--enhance` flag on `gen_image.py` — not this skill.
