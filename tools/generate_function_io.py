#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from glob import glob
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from cvas_mvp import (  # type: ignore
    extract_cvas_region,
    find_function_definitions,
    split_top_level_commas,
)


def parse_param_specs(params: str) -> List[str]:
    params = params.strip()
    if not params or params == "void":
        return []
    return [p.strip() for p in split_top_level_commas(params) if p.strip()]


def rule_based_io(param_specs: List[str]) -> Tuple[List[str], List[str]]:
    reads = []
    writes = []
    for spec in param_specs:
        if not spec:
            continue
        spec_compact = " ".join(spec.split())
        name_match = re.search(r"([A-Za-z_]\w*)\s*(\[.*\])?$", spec_compact)
        if not name_match:
            continue
        name = name_match.group(1)
        is_pointer = ("*" in spec_compact) or ("[" in spec_compact)
        is_const = "const" in spec_compact.split()
        if is_pointer:
            reads.append(name)
            if not is_const:
                writes.append(name)
        else:
            reads.append(name)
    return sorted(set(reads)), sorted(set(writes))


def build_rule_map(source: str) -> Dict[str, Dict[str, List[str]]]:
    region, found = extract_cvas_region(source)
    if not found:
        region = source
    functions = find_function_definitions(region)
    output: Dict[str, Dict[str, List[str]]] = {}
    for _, name, params, _ in functions:
        specs = parse_param_specs(params)
        reads, writes = rule_based_io(specs)
        output[name] = {"reads": reads, "writes": writes}
    return output


def extract_json_from_text(text: str) -> Dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in LLM output")
    payload = text[start : end + 1]
    return json.loads(payload)


def build_codex_env() -> Dict[str, str]:
    env = os.environ.copy()
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []

    candidates = [
        str(Path.home() / ".npm-global" / "bin"),
    ]
    candidates.extend(sorted(glob("/usr/local/nvm/versions/node/*/bin")))

    for entry in reversed(candidates):
        if entry and entry not in path_entries and Path(entry).exists():
            path_entries.insert(0, entry)

    env["PATH"] = os.pathsep.join(path_entries)
    return env


def call_codex_cli(prompt: str) -> str:
    env = build_codex_env()
    codex_bin = shutil.which("codex", path=env.get("PATH")) or str(
        Path.home() / ".npm-global" / "bin" / "codex"
    )
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="codex_last_", suffix=".txt", delete=False) as tmp:
            tmp_path = tmp.name
        cmd = [codex_bin, "exec", "-", "--output-last-message", tmp_path]
        result = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "'codex' command not found. Run this in a Codex CLI environment or use --llm-provider openai-compat."
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "codex CLI failed")
    try:
        if tmp_path and Path(tmp_path).exists():
            content = Path(tmp_path).read_text(encoding="utf-8", errors="replace")
            if content.strip():
                return content
        return result.stdout
    finally:
        _cleanup_temp(tmp_path)


def _cleanup_temp(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def call_openai_compat(prompt: str, model: str, base_url: str, api_key: str, api_mode: str) -> str:
    import json as _json
    import urllib.request

    if api_mode == "chat":
        url = base_url.rstrip("/") + "/v1/chat/completions"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a precise static analysis assistant."},
                {"role": "user", "content": prompt},
            ],
        }
    else:
        url = base_url.rstrip("/") + "/v1/responses"
        body = {
            "model": model,
            "input": [
                {"role": "system", "content": "You are a precise static analysis assistant."},
                {"role": "user", "content": prompt},
            ],
        }

    data = _json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")

    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
    payload = _json.loads(raw)

    if "output_text" in payload and isinstance(payload["output_text"], str):
        return payload["output_text"]
    if "output" in payload:
        for item in payload["output"]:
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content") or []
                for part in content:
                    if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                        return part.get("text", "")
    if "choices" in payload:
        return payload["choices"][0]["message"]["content"]

    raise RuntimeError("Unexpected OpenAI-compatible response format")


def run_llm(provider: str, prompt: str, model: str, base_url: str | None, api_key: str | None, api_mode: str) -> Dict:
    if provider == "codex-cli":
        output = call_codex_cli(prompt)
    elif provider == "openai-compat":
        if not api_key:
            raise ValueError("API key is required for openai-compat provider")
        output = call_openai_compat(prompt, model, base_url or "https://api.openai.com", api_key, api_mode)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    return extract_json_from_text(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate function_io.json with hybrid rule+LLM pipeline")
    parser.add_argument("input", type=Path, help="C source file")
    parser.add_argument("--out", type=Path, default=Path("function_io.json"), help="Final output JSON path")
    parser.add_argument("--out-rule", type=Path, default=Path("function_io.rule.json"), help="Rule-based output")
    parser.add_argument("--out-v1", type=Path, default=Path("function_io.v1.json"), help="LLM-refined output")
    parser.add_argument("--out-v2", type=Path, default=Path("function_io.v2.json"), help="LLM-verified output")
    parser.add_argument("--llm-provider", choices=["none", "codex-cli", "openai-compat"], default="none")
    parser.add_argument("--model", help="Model name for LLM providers")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL")
    parser.add_argument("--api-key", help="API key for OpenAI-compatible providers")
    parser.add_argument("--api-mode", choices=["responses", "chat"], default="responses")

    args = parser.parse_args()

    source = args.input.read_text(encoding="utf-8")
    rule_map = build_rule_map(source)
    args.out_rule.write_text(json.dumps(rule_map, indent=2), encoding="utf-8")

    if args.llm_provider == "none":
        args.out.write_text(json.dumps(rule_map, indent=2), encoding="utf-8")
        print(f"Wrote {args.out}")
        return

    if args.llm_provider == "openai-compat" and not args.model:
        raise SystemExit("--model is required when using --llm-provider openai-compat")
    if args.llm_provider == "codex-cli" and not args.model:
        # Codex CLI may use the locally configured default model. Keep the interface permissive.
        args.model = "codex-default"

    api_key = args.api_key or os.getenv("OPENAI_API_KEY")

    prompt_refine = (
        "You are given C source code and a draft function IO map. "
        "Improve the map so that reads/writes are correct. "
        "Output ONLY valid JSON.\n\n"
        "C code:\n" + source + "\n\n"
        "Draft IO JSON:\n" + json.dumps(rule_map, indent=2) + "\n"
    )

    v1_map = run_llm(args.llm_provider, prompt_refine, args.model, args.base_url, api_key, args.api_mode)
    args.out_v1.write_text(json.dumps(v1_map, indent=2), encoding="utf-8")

    prompt_verify = (
        "Given C code and an IO map, verify and correct any mistakes. "
        "Consider read-after-write, write-after-read, write-after-write dependencies. "
        "Output ONLY valid JSON.\n\n"
        "C code:\n" + source + "\n\n"
        "IO JSON:\n" + json.dumps(v1_map, indent=2) + "\n"
    )

    v2_map = run_llm(args.llm_provider, prompt_verify, args.model, args.base_url, api_key, args.api_mode)
    args.out_v2.write_text(json.dumps(v2_map, indent=2), encoding="utf-8")

    args.out.write_text(json.dumps(v2_map, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
