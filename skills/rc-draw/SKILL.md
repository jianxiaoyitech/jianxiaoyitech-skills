---
name: rc-draw
description: Use when the user wants to generate or edit images through Right
  Code rc-draw. This skill is for prompt-to-image or reference-image editing
  tasks that should run through scripts, including model selection, resolution
  validation, harness-specific API key lookup, async task polling, and local
  image download.
compatibility: Requires Python 3 and network access to right.codes. Intended
  for Codex, Claude Code, Gemini CLI, OpenCode, and Paseo environments.
metadata:
  author: geek-env
  version: "1.0"
---

# RC Draw

Use this skill when the user wants image generation through Right Code
`rc_draw`.

The runtime control flow must stay in the bundled scripts.
Do not hand-write `curl` requests in the agent response.
Do not poll tasks manually in prose.

## Entry Phase

Normalize the request into:

- `prompt`
- `model`
- `image_size`
- `aspect_ratio`
- `reference_image`
- `output_dir`

Defaults:

- `model`: `gpt-image-2`
- `image_size`: `1K`
- `aspect_ratio`: `1:1`
- `reference_image`: none
- `output_dir`: current working directory

Ask only for missing fields that materially change the result.
If the user already provided enough information, do not ask follow-up
questions.

## Model Phase

Pick the API branch from the model:

- `gpt-image-2`: OpenAI Images compatible branch
- `nano-banana`: Gemini branch, `1K` only
- `nano-banana-2`: Gemini branch, `1K`, `2K`, or `4K`
- `nano-banana-pro`: Gemini branch, `1K`, `2K`, or `4K`
- `nano-banana-2-lite`: Gemini branch, `1K` only

If the user asks for an unsupported size, stop and explain the allowed sizes
for that model.

## Harness Phase

The scripts auto-detect the active harness by default.
Use explicit `--harness` only when detection fails.

Resolution order:

1. Harness-specific environment variables
2. Harness-specific config files
3. Fallback override variables such as `RIGHT_CODES_API_KEY`

Harness expectations:

- `codex`: use Codex-style `OPENAI_API_KEY`, then read
  `~/.codex/auth.json`
- `claude-code`: use Claude Code-style `ANTHROPIC_AUTH_TOKEN`, then read
  `~/.claude/settings.json`
- `gemini-cli`: use Gemini-style `GEMINI_API_KEY` or `GOOGLE_API_KEY`, then
  read `~/.gemini/settings.json` if present
- `opencode`: prefer OpenCode environment variables or read
  `~/.config/opencode/opencode.json`
- `paseo`: detect the underlying harness from the same workspace or home config

If auto-detect fails, rerun with explicit `--harness`.

## Submission Phase

Run the submit script from the user's project directory.
Do not change the process working directory to the skill directory.
Invoke the script from the current harness-installed skill path.
Do not hardcode a Codex-specific installation path in the workflow.

Submit with:

```bash
python3 <installed-skill-path>/scripts/rc_draw_submit.py \
  --prompt "..." \
  --model "..." \
  --image-size "..." \
  --aspect-ratio "..." \
  --reference-image path/to/ref.png \
  --harness codex
```

Submission rules:

- The submit script resolves the API key itself.
- The submit script builds the request body itself.
- The submit script submits the task itself.
- The submit script prints the `task_id` immediately after submit succeeds.
- The main thread stops after getting the `task_id`.

## Monitoring Phase

After submission:

1. Tell the user that background monitoring is starting.
2. Start a monitor agent or subagent.
3. Let that monitor agent run the query script from the project directory.

Query with:

```bash
python3 <installed-skill-path>/scripts/rc_draw_query.py \
  --task-id task_xxx \
  --harness codex \
  --timeout 1200
```

Monitoring rules:

- The monitor agent runs the query script.
- The main thread does not do manual polling while the monitor agent is active.
- The query script polls `GET /v1/tasks/{task_id}` itself.
- The query script prints polling `status` and `progress` during execution.
- The query script keeps waiting and polling until the task status becomes
  `completed` or `failed`.
- Once polling starts, keep reporting progress continuously until the task
  status becomes `completed` or `failed`.
- The query script sleeps `1` second between polls by default.
- The query script downloads finished images into the current project
  directory itself.
- If the script is started from the skill directory instead of the project
  directory, it should fail fast instead of writing output there.

## Result Phase

When the query script exits:

- Report visible progress updates while the task is running.
- Never stop reporting progress while the task is still running.
- Do not stop waiting just because progress is unchanged for a while.
- Report the selected model.
- Report the resolved output directory.
- Report the saved local file paths.
- Do not expose the remote download URL after download succeeds.
- Mention the `task_id`.

If the client renders local images inline, it may show them.
Do not rely on inline rendering.
Always provide local file paths only.

## Failure Phase

If the task reaches `failed`:

- Surface `error.message`.
- Keep the original prompt and params in the response summary.
- Suggest one concrete next adjustment only when the failure implies one.

If API key resolution fails:

- Tell the user which harness lookup failed.
- Tell the user to set the harness-native variable such as `OPENAI_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `GEMINI_API_KEY`, or pass `--api-key`.

If the query script times out:

- Report that the task never reached `completed` or `failed` within the
  monitor window.
- Keep the `task_id`.
- Offer to start another monitor run on the same `task_id`.

## Notes

Right Code `rc_draw` uses async tasks.

- Images branch: `POST /draw/v1/images/generations`
- Gemini branch: `POST /draw/v1beta/models/{model}:generateContent`
- Polling: `GET /v1/tasks/{task_id}`

Use the scripts as the source of truth for request shape and polling behavior.
