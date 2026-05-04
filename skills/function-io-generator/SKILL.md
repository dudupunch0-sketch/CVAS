---
name: function-io-generator
description: Generate and refine a function-level reads/writes IO map (`function_io.json`) for C code using the CVAS project script (`tools/generate_function_io.py`). Use when the user wants Sequence-tab dependency lane quality improved, wants a rule-only baseline, or wants the preferred CLI-agent file handoff workflow with optional legacy Codex CLI/OpenAI-compatible automation.
---

# Function IO Generator

Use this skill to build or refresh `function_io.json` for CVAS Sequence-tab dependency modeling.

## When to use

- User asks to generate/update `function_io.json`.
- Sequence-tab lane/edge behavior looks wrong or too conservative.
- User wants a portable workflow where CVAS writes task files and the active CLI agent performs final semantic synthesis.
- User wants legacy automation through `codex-cli` or an OpenAI-compatible API (`responses` or `chat`).

## Prerequisites

- Run inside a CVAS repo that contains `tools/generate_function_io.py`.
- Input C file exists (commonly `test_examples.c`).
- Preferred `agent-file` mode does not require API keys, network access, Codex, Claude Code, or OpenCode on the CVAS host.
- Legacy `codex-cli` mode requires `codex` and `node` in PATH.
- Legacy `openai-compat` mode requires network access, API key, model, and base URL.

## Rule-only baseline

```bash
python tools/generate_function_io.py <input.c> --llm-provider none
```

This writes:
- `function_io.rule.json`
- `function_io.json` (same as rule output in `none` mode)

## Preferred CLI-agent handoff workflow

Step 1: Generate deterministic draft IO plus an agent-readable task package. This does not call any LLM provider or subprocess agent.

```bash
python tools/generate_function_io.py <input.c> \
  --llm-provider agent-file \
  --agent-task-dir .cvas/agent_tasks/function_io \
  --agent-output-dir .cvas/agent_outputs/function_io
```

Expected task files:
- `README.md`
- `function_io_refine.prompt.md`
- `function_io_verify.prompt.md`
- `function_io_refine.input.json`
- `function_io.schema.json`
- `static_summary.json`
- `source_excerpt.c`

Step 2: Ask the active CLI agent to read the generated `README.md` and prompts, then write:
- `.cvas/agent_outputs/function_io/function_io.v1.json`
- `.cvas/agent_outputs/function_io/function_io.v2.json`

Step 3: Import and validate the agent output.

```bash
python tools/generate_function_io.py <input.c> \
  --import-agent-output .cvas/agent_outputs/function_io/function_io.v2.json \
  --out function_io.json \
  --validation-report .cvas/agent_outputs/function_io/validation_report.json \
  --validation-mode warn \
  --merge-missing-from-rule
```

Import mode validates JSON shape, missing static-snapshot functions, and static-snapshot references, writes a legacy-compatible `function_io.json`, and preserves `coverage_gaps` in the validation report. This is contract validation/reconciliation, not a second semantic static-analysis pass. Use `--merge-missing-from-rule` when omitted functions should be explicitly filled from the deterministic baseline.

## Legacy automation mode: Codex CLI

```bash
python tools/generate_function_io.py <input.c> --llm-provider codex-cli
```

Notes:
- Script uses `codex exec` non-interactive mode.
- Script handles non-interactive PATH issues for common NVM / npm-global setups.
- Script writes intermediate files:
  - `function_io.rule.json`
  - `function_io.v1.json`
  - `function_io.v2.json`
  - `function_io.json` (final)

## Legacy automation mode: OpenAI-compatible API

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

## Viewer workflow

After `function_io.json` is generated or imported, regenerate the HTML viewer if needed:

```bash
python cvas_wrapper.py <input.c> viewer/output.html --output-json viewer/output.json
```

`json_to_html.py` embeds `function_io.json` at build time and Sequence-tab can also auto-load runtime files.

## Validation checklist

- `function_io.json` keys match actual function names in the CVAS region or are intentionally preserved agent-only findings with evidence in `validation_report.json`.
- `reads`/`writes` lists use parameter names, not arbitrary local aliases.
- Output buffers (e.g. `out`) are in `writes` and often `reads` if read-modify-write semantics matter.
- Pure helper functions (`abs`, `clamp`, `median`) usually have `writes: []`.
- `coverage_gaps` are preserved for source-backed static-analysis omissions instead of being silently dropped.

## Troubleshooting

- `agent-file` import exits non-zero in strict mode
  - Open the validation report and fix schema/reference issues in the agent JSON.
  - If the report contains `missing_function`, either add that function to the agent output or rerun import with `--merge-missing-from-rule`.
  - Do not delete evidence-backed `coverage_gaps` just because they are absent from static facts.
- `codex command not found`
  - Ensure `codex` is installed and in PATH.
  - Ensure `node` is in PATH for the `codex` wrapper.
- OpenAI-compatible call fails
  - Check `--base-url`, `--model`, `--api-key`, and whether endpoint supports `responses` vs `chat`.
- Sequence tab does not visibly change after updating IO map
  - The new IO map may be equivalent to fallback dependency logic for that example.
  - Confirm the viewer is using the expected source (`IO: embedded`, `auto-loaded`, or `loaded from file`).

## Commit hygiene

Intermediate files (`function_io.rule.json`, `function_io.v1.json`, `function_io.v2.json`, validation reports, and `.cvas/` task packages) are often generated for inspection only. Decide explicitly whether to commit them.
