---
name: function-io-generator
description: Generate and refine a function-level reads/writes IO map (`function_io.json`) for C code using the CVAS project script (`tools/generate_function_io.py`). Use when the user wants Sequence-tab dependency lane quality improved, wants to create/update function_io metadata, or wants to run the hybrid rule-based + LLM workflow via Codex CLI or an OpenAI-compatible API (responses/chat modes).
---

# Function IO Generator

Use this skill to build or refresh `function_io.json` for CVAS Sequence-tab dependency modeling.

## When to use

- User asks to generate/update `function_io.json`
- Sequence-tab lane/edge behavior looks wrong or too conservative
- User wants to run the hybrid pipeline (rule-based -> LLM refine -> LLM verify)
- User wants to use `codex-cli` or OpenAI-compatible API (`responses` or `chat`)

## Prerequisites

- Run inside a CVAS repo that contains `tools/generate_function_io.py`
- Input C file exists (commonly `test_examples.c`)
- For `codex-cli` mode: `codex` command available in PATH (and `node` available)
- For `openai-compat` mode: network access + API key + model/base URL

## Primary command

Rule-based only (fast baseline):

```bash
python tools/generate_function_io.py <input.c> --llm-provider none
```

This writes:
- `function_io.rule.json`
- `function_io.json` (same as rule output in `none` mode)

## Hybrid mode (Codex CLI)

```bash
python tools/generate_function_io.py <input.c> --llm-provider codex-cli
```

Notes:
- Script uses `codex exec` non-interactive mode
- Script handles non-interactive PATH issues for common NVM / npm-global setups
- Script writes intermediate files:
  - `function_io.rule.json`
  - `function_io.v1.json`
  - `function_io.v2.json`
  - `function_io.json` (final)

## Hybrid mode (OpenAI-compatible API)

Responses API mode (default):

```bash
python tools/generate_function_io.py <input.c> \
  --llm-provider openai-compat \
  --model <MODEL_NAME> \
  --base-url <BASE_URL> \
  --api-key <API_KEY> \
  --api-mode responses
```

Chat Completions mode:

```bash
python tools/generate_function_io.py <input.c> \
  --llm-provider openai-compat \
  --model <MODEL_NAME> \
  --base-url <BASE_URL> \
  --api-key <API_KEY> \
  --api-mode chat
```

You can also use `OPENAI_API_KEY` instead of `--api-key`.

## Workflow (recommended)

1. Run rule-based mode first if you want a quick sanity baseline.
2. Run hybrid mode (`codex-cli` or `openai-compat`).
3. Inspect `function_io.v1.json` and `function_io.v2.json` if the final result looks odd.
4. Copy/keep final `function_io.json` in project root.
5. Regenerate HTML viewer if needed:

```bash
python cvas_wrapper.py <input.c> viewer/output.html --output-json viewer/output.json
```

`json_to_html.py` embeds `function_io.json` at build time and Sequence-tab can also auto-load runtime files.

## Validation checklist

- `function_io.json` keys match actual function names in the CVAS region
- `reads`/`writes` lists use parameter names (not arbitrary aliases)
- Output buffers (e.g. `out`) are in `writes` (and often `reads` if read-modify-write semantics matter)
- Pure helper functions (`abs`, `clamp`, `median`) usually have `writes: []`

## Troubleshooting

- `codex command not found`
  - Ensure `codex` is installed and in PATH
  - Ensure `node` is in PATH for the `codex` wrapper
- OpenAI-compatible call fails
  - Check `--base-url`, `--model`, `--api-key`, and whether endpoint supports `responses` vs `chat`
- Sequence tab does not visibly change after updating IO map
  - The new IO map may be equivalent to fallback dependency logic for that example
  - Confirm the viewer is using the expected source (`IO: embedded`, `auto-loaded`, or `loaded from file`)

## Commit hygiene

Intermediate files (`function_io.rule.json`, `function_io.v1.json`, `function_io.v2.json`) are often generated for inspection only. Decide explicitly whether to commit them.
