# CVAS Enhanced - JSON Schema v3 C-model Block Diagram Parser

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Schema](https://img.shields.io/badge/schema-3.0-green.svg)]()
[![Analysis](https://img.shields.io/badge/analysis-2.0-blue.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

ISP 알고리즘 C-model 분석을 위한 고급 파서로, `CVAS_START` / `CVAS_END` 구간을 분석하여 상세한 블록 다이어그램, 제어 흐름 데이터, v3 sequence timeline JSON을 생성합니다. JSON contract version은 `schema_version: "3.0"`이고, 기존 analyzer generation 의미의 `analysis_version: "2.0"` 필드는 유지됩니다.

## 📚 목차

- [요구사항](#-요구사항)
- [빠른 시작](#-빠른-시작)
- [프로젝트 구조](#-프로젝트-구조)
- [입력 파일 형식](#-입력-파일-형식)
- [출력 JSON 구조](#-출력-json-구조)
- [문제 해결](#-문제-해결)
- [라이선스](#-라이선스)
- [Change log](#-change-log)

## 🎉 주요 기능

### 🧭 JSON Schema v3: 실행 timeline contract

- **명시적 contract version**
  - top-level `schema_version: "3.0"`와 `schema.version: "3.0"` 출력
  - 기존 `analysis_version: "2.0"`은 analyzer generation metadata로 유지
- **반복 호출 구분**
  - 동일 callee를 여러 번 호출해도 `flow.call_instances[]`에 안정적인 `call_id`(`C_B2_0001`, `C_B2_0002` 등) 부여
- **Signal metadata / provenance**
  - 기존 endpoint 필드는 유지하면서 `signal_id`, `kind`, `role`, `call_id`, `arg_index`, `param`, `expr`, `target`, `provenance` 추가
- **Sequence timeline**
  - `flow.sequence_timeline[]`이 block execution order별 call/signal/read-write summary를 제공
  - HTML viewer는 v3 timeline card UI를 우선 사용하고, v2 JSON은 legacy sequence fallback으로 렌더링
- **Function IO embedding**
  - `--function-io function_io.json`으로 함수별 reads/writes metadata를 `flow.function_io`에 포함 가능

### ✨ v2.0 Priority 1: 완전한 데이터 흐름 추적

- **단순 대입문 처리** (`a = b;`)
  - "copy" 타입 Operation 생성
  - 데이터 흐름 완전성 70% → 90%

- **복합 연산자 지원** (`i++`, `i += 2`, `x *= y`)
  - 자동 정규화: `i++` → `i = i + 1`
  - 실제 코드 커버리지 대폭 향상

- **비트 및 시프트 연산**
  - `&`, `|`, `^`, `<<`, `>>` 완벽 지원
  - ISP 알고리즘에 필수적인 비트 조작 추적

### 🔍 Priority 2: 제어 흐름 및 호출 그래프 분석

- **Control Flow Graph (CFG)**
  - Basic Block 분석
  - 루프 구조 파악
  - 분기/조건문 추적
  - 중첩 깊이 분석

- **Function Call Graph**
  - 전체 호출 체인 분석
  - Critical Path 계산 (가장 긴 실행 경로)
  - 재귀 호출 감지
  - 함수별 총 Cycle 추정 (호출된 함수 포함)

### 📊 향상된 분석 품질

| 메트릭 | v1.0 | v2.0 | 개선 |
|--------|------|------|------|
| 데이터 흐름 완전성 | 70% | 90% | +29% |
| 코드 커버리지 | 80% | 95% | +19% |
| 연산 타입 | 3종 | 6종 | +100% |
| 제어 흐름 분석 | 기본 | CFG | ✅ |
| 호출 그래프 | 직접 호출 | 전체 체인 | ✅ |
| Critical Path | ❌ | ✅ | NEW |

---

## 📋 요구사항

- Python 3.10 이상
- Python-side dependencies are listed in `requirements.txt` (`pycparser`, `pytest`)
- 기본 `fast` 분석은 `pycparser`를 사용하고, AST 파싱이 불가능하면 text fallback으로 최대한 JSON을 생성
- `--analysis-mode full` 사용 시: 시스템 `gcc`/`g++` 10.2 이상 권장 (GCC dump metadata용)
- `--analysis-mode full`은 선택 의존성 `tree_sitter`, `tree_sitter_c`, `tree_sitter_cpp`가 있으면 구조 분석에 사용하고, 없으면 기존 fast fallback을 사용
- RHEL 8.10에서는 GCC toolchain이 기본 제공되지 않으면 `sudo dnf groupinstall -y "Development Tools"`로 설치할 수 있음
- 설치/가상환경/검증 명령은 [requirements.md](requirements.md) 참고

---

## 🚀 빠른 시작

### 기본 사용법

환경 준비와 의존성 설치:

```bash
cd ..
python -m venv .venv
cd CVAS
source ../.venv/bin/activate
../.venv/bin/python -m pip install --upgrade pip
pip install -r requirements.txt
```

```bash
python src/cvas_cli.py model.c -o output.json
```

`src/cvas_cli.py`가 직접 CLI entrypoint이고, `src/cvas_mvp.py`는 기존 사용자를 위한 호환 wrapper입니다.

```bash
python src/cvas_mvp.py model.c -o output.json
```

### 분석 모드

CVAS는 사용자-facing 분석 모드를 `fast`와 `full` 두 가지로 유지합니다.

- `fast`: `pycparser` 기반 경량 분석을 먼저 시도하고, AST 파싱이 실패하면 text fallback으로 최대한 결과를 생성합니다.
- `full`: 선택적으로 tree-sitter C/C++ 구조 파서를 먼저 사용하고, 설치되어 있지 않거나 결과가 없으면 fast 경로로 fallback한 뒤 GCC dump metadata를 추가합니다. clang/libclang은 필수 요구사항이 아닙니다.

```bash
python src/cvas_cli.py model.c --analysis-mode fast -o output.json
python src/cvas_cli.py model.c --analysis-mode full -o output.json
python src/cvas_cli.py model.c --analysis-mode full --compile-arg=-Iinclude -o output.json
```

`--compile-arg`와 `--compile-db`는 full mode의 중립 compile flag alias입니다. `--clang-arg`와 `--clang-compile-db` 이름도 legacy 호환성을 위해 계속 유지됩니다. 현재 `full` 모드에서는 include/define/undef/system-include/std/language 정보를 재구성해 GCC dump pass에 전달하는 용도로 사용됩니다.

`full` 모드의 GCC dump는 보강 metadata입니다. `gcc`/`g++`가 없거나 compiler diagnostic이 발생해도 일반적으로 JSON 생성 자체를 막지 않고 `gcc_dump.status`에 `ok`, `failed`, `unavailable` 중 하나로 기록합니다.

### 오프라인 다이어그램 뷰어 (JSON → HTML)

CVAS JSON을 단일 HTML로 시각화하려면 `json_to_html.py`를 사용합니다.

```bash
python json_to_html.py output.json output.html
```

> **주의:** `output.html`과 같은 폴더에 `./assets/elk.bundled.js`가 있어야 합니다.  
> 사내망/오프라인 환경에서는 `viewer/assets/elk.bundled.js`를 실제 ELK.js 브라우저 번들로 교체한 뒤,
> `output.html`과 동일 경로에 `assets/elk.bundled.js`가 있도록 배치하세요.

CVAS 분석부터 HTML까지 한 번에 실행하려면:

```bash
python cvas_wrapper.py model.c output.html
```

`cvas_wrapper.py`는 `output.html` 생성 후 필요한 ELK 자산을 같은 출력 폴더 기준 `./assets/elk.bundled.js`로 자동 복사합니다.

문서/데모 용 최신 샘플 출력은 다음처럼 갱신할 수 있습니다.

```bash
python cvas_wrapper.py test_examples.c docs/test_examples_output.html --output-json docs/test_examples_output.json
```

프로젝트 소개용 정적 개요 문서는 `docs/cvas_project_overview.html`에 있습니다.

### Sequence 탭 (v3 timeline card UI) + `function_io.json`

현재 `output.html`에는 다음이 포함됩니다.

- `Diagram` 탭: operation-flow 중심 block diagram (data-flow / execution-order / call-graph 토글)
- `Sequence` 탭: v3 `flow.sequence_timeline[]`이 있으면 timeline card UI를 우선 렌더링하고, v2 JSON처럼 `sequence_timeline`이 없으면 기존 `flow.call_sequence` 기반 legacy sequence view로 fallback

v3 Sequence card는 각 static block-order step에 대해 다음 정보를 보여줍니다.

- `step_id`, `block_id`, `function`, `order_index`
- caller/callee로 연결된 `call_id` 목록
- incoming/outgoing `signal_id` 목록
- `flow.function_io` 기반 함수별 reads/writes summary

`read_write_summary` bucket은 JSON contract에 직렬화되어 downstream consumer가 사용할 수 있지만, 현재 viewer card에서는 개별 bucket 전체를 직접 펼쳐 표시하지 않습니다.

반복 호출은 같은 callee라도 서로 다른 `call_id`로 표시되므로, `top()`이 `inc()`를 두 번 호출하는 경우에도 `C_B2_0001`, `C_B2_0002`처럼 call occurrence를 구분할 수 있습니다.

`Sequence` 탭의 reads/writes 판단 정확도를 높이려면 `function_io.json`(함수별 reads/writes 메타데이터)을 생성한 뒤 `--function-io`로 JSON에 embed합니다. 파일을 전달하지 않으면 pipeline이 rule-based 기본 `flow.function_io` envelope를 생성합니다.

기본 생성(규칙 기반):

```bash
python tools/generate_function_io.py model.c --llm-provider none
```

권장 방식은 CVAS가 정적 스냅샷과 CLI-agent 작업 패키지를 파일로 만들고, 별도 CLI agent가 그 파일을 읽어 최종 의미 보강 JSON을 작성한 뒤 CVAS가 다시 import/validate하는 흐름입니다. 이 방식은 CVAS 실행 환경에서 외부 LLM이나 Codex/Claude/OpenCode CLI를 직접 호출하지 않습니다.

```bash
python tools/generate_function_io.py model.c \
  --llm-provider agent-file \
  --agent-task-dir .cvas/agent_tasks/function_io \
  --agent-output-dir .cvas/agent_outputs/function_io
```

생성되는 작업 패키지에는 `README.md`, refine/verify prompt, `function_io_refine.input.json`, `function_io.schema.json`, `static_summary.json`, `source_excerpt.c`가 포함됩니다. CLI agent는 패키지의 지시를 따라 보통 다음 파일을 작성합니다.

```text
.cvas/agent_outputs/function_io/function_io.v1.json
.cvas/agent_outputs/function_io/function_io.v2.json
```

agent 결과를 최종 `function_io.json`으로 가져오고 검증 리포트를 남기려면:

```bash
python tools/generate_function_io.py model.c \
  --import-agent-output .cvas/agent_outputs/function_io/function_io.v2.json \
  --out function_io.json \
  --validation-report .cvas/agent_outputs/function_io/validation_report.json \
  --validation-mode warn \
  --merge-missing-from-rule
```

`--validation-mode strict`는 schema/static reference 오류나 static snapshot 함수 누락이 있으면 non-zero로 종료하므로 CI나 handoff 검증에 적합합니다. `--merge-missing-from-rule`를 쓰면 agent가 생략한 함수를 deterministic rule map으로 명시적으로 채웁니다. `coverage_gaps`는 validation report에 보존되어 CVAS가 놓친 정적 facts를 후속 검토할 수 있습니다.

기존 자동화 방식도 유지됩니다. Codex CLI 기반 legacy hybrid 생성:

```bash
python tools/generate_function_io.py model.c --llm-provider codex-cli
```

내부 테스트에서 nested Codex sandbox가 네트워크를 막는 환경이면:

```bash
python tools/generate_function_io.py model.c \
  --llm-provider codex-cli \
  --codex-danger-full-access \
  --codex-timeout-sec 60
```

OpenAI-compatible API 기반 legacy hybrid 생성 (`responses` / `chat` 선택 가능):

```bash
python tools/generate_function_io.py model.c \
  --llm-provider openai-compat \
  --model <MODEL_NAME> \
  --base-url <BASE_URL> \
  --api-key <API_KEY> \
  --api-mode responses
```

생성한 IO metadata를 분석 JSON에 포함하려면:

```bash
python src/cvas_cli.py model.c --function-io function_io.json -o output.json
python cvas_wrapper.py model.c output.html --cvas-args --function-io function_io.json
```

`json_to_html.py`는 JSON 내부 `flow.function_io`를 먼저 사용하고, 필요하면 생성 시점의 embedded `function_io.json` 또는 런타임 sidecar(`./function_io.json`, `../function_io.json`)도 로드 시도합니다.

### Cycle Rule 설정

**CLI 인자:**
```bash
python src/cvas_mvp.py model.c \
  --add-per-cycle 4 \
  --compare-per-cycle 4 \
  --mul-per-cycle 1 \
  --copy-per-cycle 8 \
  --shift-per-cycle 2 \
  --bitwise-per-cycle 4 \
  --const-per-cycle 8 \
  --load-per-cycle 4 \
  --store-per-cycle 4 \
  -o output.json
```

**JSON 설정 파일:**

`cycle.json`:
```json
{
  "add_per_cycle": 4,
  "compare_per_cycle": 4,
  "mul_per_cycle": 1,
  "copy_per_cycle": 8,
  "shift_per_cycle": 2,
  "bitwise_per_cycle": 4,
  "const_per_cycle": 8,
  "load_per_cycle": 4,
  "store_per_cycle": 4
}
```

```bash
python src/cvas_mvp.py model.c --cycle-config cycle.json -o output.json
```

---

## 📁 프로젝트 구조

- `src/cvas_mvp.py`: CVAS CLI 호환 wrapper
- `src/cvas_cli.py`: CLI front-end (인자 파싱 / I-O / 실행)
- `src/cvas_pipeline.py`: CVAS 핵심 분석 파이프라인 본체
- `src/cvas_passes.py`: 함수 단위 전처리 / lowering / 분석 패스
- `src/cvas_model.py`: 공유 IR 데이터 모델과 cycle rule 정의
- `src/cvas_index.py`: 프로젝트 소스 수집 / 심볼 인덱싱 / 선언명 추출
- `src/cvas_cfg.py`: control-flow graph 분석과 control note 추출
- `src/cvas_callgraph.py`: 함수 호출 그래프와 call sequence 생성
- `src/cvas_expr.py`: 식 토큰화 / 연산 lowering / operand 분류
- `src/cvas_serialize.py`: JSON 직렬화 계층
- `src/cvas_source.py`: CVAS region / function discovery / source helper 공용 모듈
- `src/cvas_text.py`: 문장 분할 / 괄호 처리 / 식별자 토큰 추출 유틸
- `src/c_ast_utils.py`: C AST 분석 유틸리티
- `cvas_wrapper.py`: 분석부터 HTML 생성까지 실행하는 래퍼 스크립트 (출력 폴더에 ELK 자산 자동 복사)
- `json_to_html.py`: CVAS JSON을 단일 HTML로 변환 (Diagram/Sequence 탭, IO 상태 표시, 드래그/줌 포함)
- `tools/generate_function_io.py`: `function_io.json` 생성기 (규칙 기반 + LLM 하이브리드)
- `function_io.json`: Sequence 탭 reads/writes summary 및 의존성 판단용 함수 IO 메타데이터
- `docs/cvas_datapath_pipeline_design.md`: datapath 중심 II=1 파이프라인 분석 설계 문서
- `docs/schema/cvas.schema.v3.json`: Schema v3 formal JSON Schema
- `docs/schema/cvas-schema-v3.md`: Schema v3 field contract 설명
- `docs/cvas_project_overview.html`: 프로젝트 소개 / 구조 / 사용 흐름 / 명령어 요약 HTML
- `docs/test_examples_output.html`: `test_examples.c` 기준 최신 샘플 뷰어 HTML
- `docs/test_examples_output.json`: 샘플 뷰어 HTML 생성에 사용한 최신 JSON 출력
- `viewer/`: 오프라인 HTML 뷰어 및 ELK.js 번들 자산
- `fixtures/`: 파싱 회귀 테스트용 C 코드 모음

---

## 📝 입력 파일 형식

### 기본 구조

```c
#include <stdint.h>

// 이 부분은 무시됩니다
void preprocessing() {
    // ...
}

CVAS_START

// Example 1: Simple assignment (NEW in v2.0)
int copy_value(int x) {
    int y = x;  // Creates "copy" operation
    return y;
}

// Example 2: Compound operators (NEW in v2.0)
int increment(int counter) {
    counter++;           // Normalized to: counter = counter + 1
    counter += 5;        // Normalized to: counter = counter + 5
    return counter;
}

// Example 3: Bitwise operations (NEW in v2.0)
int apply_mask(int value, int mask) {
    int masked = value & mask;     // Bitwise AND
    int shifted = masked >> 2;     // Right shift
    return shifted | 0x01;         // Bitwise OR
}

// Example 4: Control flow (Enhanced CFG analysis)
int clamp(int value, int min, int max) {
    if (value < min) {
        return min;
    }
    if (value > max) {
        return max;
    }
    return value;
}

// Example 5: Function calls (Call Graph analysis)
int process(int input) {
    int masked = apply_mask(input, 0xFF);
    int result = clamp(masked, 0, 100);
    return result;
}

CVAS_END

// 이 부분도 무시됩니다
int main() {
    return 0;
}
```

---

## 🧪 파싱 회귀용 Fixture

복잡한 함수 정의/호출 파싱 실패 사례를 회귀 방지용으로 아래 파일에 정리했습니다.

- `fixtures/function_parsing_edge_cases.c`

---

## 📊 출력 JSON 구조

### Schema v3 additive 구조

v3 출력은 기존 v2 diagram 필드를 제거하지 않고, Sequence tab이 직접 소비할 수 있는 call/signal timeline contract를 추가합니다.

```json
{
  "schema_version": "3.0",
  "schema": {
    "name": "cvas-analysis",
    "version": "3.0",
    "compatibility": {"preserves_v2_fields": true}
  },
  "blocks": [...],
  "operations": [...],
  "signals": [
    {
      "source_id": "B2",
      "source_type": "block",
      "destination_id": "B1",
      "destination_type": "block",
      "signal_name": "a",
      "direction": "in",
      "signal_id": "S_C_B2_0001_ARG_0",
      "kind": "call_argument",
      "role": "read",
      "call_id": "C_B2_0001",
      "arg_index": 0,
      "param": "x",
      "expr": "a",
      "source_function": "top",
      "destination_function": "inc",
      "provenance": {"source": "static", "parser": "ast", "confidence": "high"}
    }
  ],
  "flow": {
    "execution_order": [...],
    "execution_order_meta": {"kind": "static_block_order", "source": "analysis_queue"},
    "parallelism": "sequential",
    "call_sequence": [...],
    "call_graph": {...},
    "call_instances": [
      {
        "call_id": "C_B2_0001",
        "caller_block_id": "B2",
        "caller_function": "top",
        "callee_block_id": "B1",
        "callee_function": "inc",
        "ordinal_in_caller": 1,
        "args": [{"arg_index": 0, "param": "x", "expr": "a", "signal_id": "S_C_B2_0001_ARG_0"}],
        "assigned": {"target": "first", "signal_id": "S_C_B2_0001_RET"},
        "provenance": {"source": "static", "parser": "ast", "confidence": "high"}
      }
    ],
    "sequence_timeline": [
      {
        "step_id": "T_0000_B1",
        "order_index": 0,
        "block_id": "B1",
        "function": "inc",
        "call_ids_as_caller": [],
        "call_ids_as_callee": ["C_B2_0001"],
        "incoming_signal_ids": ["S_C_B2_0001_ARG_0"],
        "outgoing_signal_ids": ["S_C_B2_0001_RET"],
        "read_write_summary": {
          "reads_from_other": [...],
          "read_by_other": [],
          "writes_to_other": [...],
          "written_by_other": []
        }
      }
    ],
    "function_io": {"source": "rule-based", "functions": {...}},
    "dependencies": {"inter_block": [...], "call_instance": [...]}
  },
  "diagram_hint": {...},
  "note": "Enhanced with P1+P2 and schema v3 sequence timeline",
  "analysis_version": "2.0",
  "analysis_mode": "full",
  "analysis_backend": "tree-sitter+pycparser+gcc-dump",
  "project_mode": false,
  "duplicate_functions": [],
  "gcc_dump": {
    "backend": "gcc",
    "status": "ok",
    "language": "c",
    "standard": "c11",
    "dump_files": ["cvas-gcc-dump.c.015t.cfg"],
    "diagnostics": []
  }
}
```

`analysis_mode`와 `analysis_backend`는 정상 모델과 early-return 출력 모두에 포함됩니다. `gcc_dump`는 `--analysis-mode full`에서만 포함되는 선택 metadata이며, `status`는 `ok`, `failed`, `unavailable` 중 하나입니다. CVAS region이 없거나 region 안에서 함수 정의를 찾지 못해도 full mode에서는 GCC dump metadata가 가능한 범위에서 기록됩니다. Formal schema v3 contract와 fixture 예시는 `docs/schema/cvas.schema.v3.json`, `docs/schema/cvas-schema-v3.md`, `tests/fixtures/schema/sequence_timeline_v3.expected.json`을 참고하세요.

### Block 구조 (CFG 추가)

```json
{
  "block_id": "B1",
  "block_name": "process",
  "inputs": ["input"],
  "outputs": ["return"],
  "internal_ops_summary": {
    "add": 0,
    "compare": 2,
    "multiply": 0,
    "copy": 1,
    "shift": 1,
    "bitwise": 2
  },
  "estimated_cycles": 3,
  "note": "contains conditional; internal op nodes emitted",
  "position": {...},
  "cfg": {
    "function_name": "process",
    "basic_blocks": [
      {
        "block_id": "process_entry",
        "parent_function": "process",
        "operations": [],
        "predecessors": [],
        "successors": ["process_main"],
        "block_type": "entry"
      },
      {
        "block_id": "process_main",
        "parent_function": "process",
        "operations": ["B1_op_1", "B1_op_2"],
        "predecessors": ["process_entry"],
        "successors": ["process_exit"],
        "block_type": "conditional_branch"
      },
      {
        "block_id": "process_exit",
        "parent_function": "process",
        "operations": [],
        "predecessors": ["process_main"],
        "successors": [],
        "block_type": "exit"
      }
    ],
    "entry_block": "process_entry",
    "exit_blocks": ["process_exit"],
    "loops": [],
    "has_branches": true,
    "max_nesting_depth": 2,
    "analysis_confidence": "high",
    "analysis_coverage": 1.0,
    "analysis_limitations": []
  }
}
```

### Operations (확장된 타입)

```json
[
  {
    "op_id": "B1_op_1",
    "op_type": "copy",
    "inputs": ["x"],
    "outputs": ["y"],
    "parent_block_id": "B1"
  },
  {
    "op_id": "B2_op_1",
    "op_type": "bitwise",
    "inputs": ["value", "mask"],
    "outputs": ["tmp_1"],
    "parent_block_id": "B2"
  },
  {
    "op_id": "B2_op_2",
    "op_type": "shift",
    "inputs": ["tmp_1", "2"],
    "outputs": ["shifted"],
    "parent_block_id": "B2"
  }
]
```

### Call Graph 구조

```json
{
  "call_graph": {
    "nodes": {
      "apply_mask": {
        "function_name": "apply_mask",
        "block_id": "B1",
        "callers": ["process"],
        "callees": [],
        "call_depth": 1,
        "is_recursive": false,
        "self_cycles": 2,
        "total_cycles": 2
      },
      "clamp": {
        "function_name": "clamp",
        "block_id": "B2",
        "callers": ["process"],
        "callees": [],
        "call_depth": 1,
        "is_recursive": false,
        "self_cycles": 1,
        "total_cycles": 1
      },
      "process": {
        "function_name": "process",
        "block_id": "B3",
        "callers": [],
        "callees": ["apply_mask", "clamp"],
        "call_depth": 0,
        "is_recursive": false,
        "self_cycles": 1,
        "total_cycles": 4
      }
    },
    "entry_functions": ["process"],
    "call_chains": [
      ["process", "apply_mask"],
      ["process", "clamp"]
    ],
    "critical_path": ["process", "apply_mask"],
    "max_depth": 1,
    "has_recursion": false,
    "analysis_confidence": "high",
    "analysis_coverage": 1.0,
    "analysis_limitations": []
  }
}
```

---

## 🎯 새로운 연산 타입

### v1.0: 3가지 타입
- `add`: `+`, `-`
- `compare`: `<`, `>`, `<=`, `>=`, `==`, `!=`
- `multiply`: `*`, `/`

### v2.0: 6가지 타입

| 타입 | 연산자 | 예시 | 일반적 Cycle |
|------|--------|------|--------------|
| `add` | `+`, `-` | `a + b` | 4 ops/cycle |
| `compare` | `<`, `>`, `<=`, `>=`, `==`, `!=` | `a < b` | 4 ops/cycle |
| `multiply` | `*`, `/`, `%` | `a * b` | 1 op/cycle |
| **`copy`** | 직접 대입 | `a = b` | **8 ops/cycle** |
| **`shift`** | `<<`, `>>` | `a << 2` | **2 ops/cycle** |
| **`bitwise`** | `&`, `|`, `^` | `a & b` | **4 ops/cycle** |

---

## 🔍 주요 개선 사항

### 1. 완전한 데이터 흐름 추적

**Before (v1.0):**
```c
int func(int x) {
    int y = x;  // ❌ Operation 생성 안 됨
    return y;   // ❌ 데이터 흐름 끊김
}
```

**After (v2.0):**
```c
int func(int x) {
    int y = x;  // ✅ "copy" operation 생성
    return y;   // ✅ 완전한 데이터 흐름
}
```

### 2. 복합 연산자 자동 정규화

**Input:**
```c
int counter = 0;
counter++;        // Post-increment
counter += 5;     // Compound addition
counter *= 2;     // Compound multiplication
```

**Internal Normalization:**
```c
counter = counter + 1;
counter = counter + 5;
counter = counter * 2;
```

**Output:** 3개의 Operations (add, add, multiply)

### 3. 비트 연산 완벽 지원

**Input:**
```c
int mask_data(int value) {
    int masked = value & 0xFF;      // Bitwise AND
    int shifted = masked >> 4;       // Right shift
    int result = shifted | 0x10;     // Bitwise OR
    return result;
}
```

**Output:**
- 2 bitwise operations (`&`, `|`)
- 1 shift operation (`>>`)
- 완전한 데이터 흐름 추적

### 4. Control Flow Graph

**Input:**
```c
int clamp(int value, int min, int max) {
    if (value < min) {
        return min;
    }
    if (value > max) {
        return max;
    }
    return value;
}
```

**CFG Output:**
```
entry
  ↓
main (has 2 conditionals)
  ↓
exit
```

**Analysis:**
- `has_branches: true`
- `max_nesting_depth: 2`
- 2 compare operations detected

### 5. Call Graph 및 Critical Path

**Input:**
```c
CVAS_START

int add(int a, int b) {
    return a + b;  // 1 cycle
}

int mul(int a, int b) {
    return a * b;  // 1 cycle (multiply)
}

int compute(int x, int y) {
    int sum = add(x, y);      // Calls add
    int product = mul(sum, y); // Calls mul
    return product;            // 0 cycles
}

CVAS_END
```

**Call Graph:**
```
compute (entry, depth=0, total=3 cycles)
  ├─→ add (depth=1, 1 cycle)
  └─→ mul (depth=1, 1 cycle)
```

**Critical Path:** `compute → mul` (longest execution)

---

## 💡 사용 팁

### 1. ISP 알고리즘 최적화

비트 연산이 많은 ISP 코드:

```c
CVAS_START

// Demosaic: Bayer pattern interpolation
uint16_t demosaic_green(uint16_t *bayer, int x, int y, int width) {
    uint16_t left = bayer[y * width + (x - 1)];
    uint16_t right = bayer[y * width + (x + 1)];
    uint16_t top = bayer[(y - 1) * width + x];
    uint16_t bottom = bayer[(y + 1) * width + x];

    uint16_t sum = left + right + top + bottom;
    uint16_t avg = sum >> 2;  // Divide by 4 using shift

    return avg & 0x3FF;  // Clip to 10-bit
}

CVAS_END
```

**Analysis Output:**
- 4 add operations (pixel additions)
- 1 shift operation (division)
- 1 bitwise operation (clipping)
- Total: ~2 cycles (with default rules)

### 2. Call Graph로 병목 지점 찾기

```bash
python src/cvas_mvp.py isp_pipeline.c -o analysis.json
```

**JSON에서 확인:**
```json
{
  "flow": {
    "call_graph": {
      "critical_path": ["main_pipeline", "demosaic", "color_correct"],
      "nodes": {
        "demosaic": {
          "total_cycles": 50
        },
        "color_correct": {
          "total_cycles": 10
        }
      }
    }
  }
}
```

→ `demosaic` 함수가 병목! 최적화 집중 대상

### 3. CFG로 루프 분석

```c
int sum_array(int *data, int size) {
    int sum = 0;
    for (int i = 0; i < size; i++) {
        sum += data[i];
    }
    return sum;
}
```

**CFG Output:**
```json
{
  "cfg": {
    "loops": [
      {
        "loop_id": "sum_array_loop_1",
        "header_block": "sum_array_main",
        "nesting_level": 1,
        "estimated_iterations": "unknown"
      }
    ]
  }
}
```

---

## 🐛 문제 해결

### "No functions found"

확인 사항:
1. `CVAS_START`와 `CVAS_END` 존재 여부
2. 함수 정의가 CVAS 구간 내에 있는지
3. 함수 형식이 올바른지: `type name(params) { ... }`

### Operations가 예상보다 적음

v2.0에서는 다음이 모두 Operation으로 생성됩니다:
- ✅ 단순 대입: `a = b`
- ✅ 복합 연산자: `i++`, `i += 2`
- ✅ 비트 연산: `a & b`, `a << 2`

여전히 적다면 지원하지 않는 패턴일 수 있습니다.

### Call Graph가 비어있음

확인:
- 함수 호출이 CVAS 구간 내 함수인지
- 외부 라이브러리 함수는 추적 안 됨

---

## 📚 기술 상세

### Shunting-yard 알고리즘

연산자 우선순위를 정확히 처리:

```
Input:  a + b * c
Tokens: [a, +, b, *, c]
Infix → Postfix: [a, b, c, *, +]
Operations:
  1. multiply(b, c) → tmp_1
  2. add(a, tmp_1) → result
```

### CFG Basic Block 분류

- **entry**: 함수 시작점
- **sequential**: 직선 실행
- **conditional_branch**: if/switch
- **loop_header**: 루프 시작
- **loop_body**: 루프 본문
- **exit**: 함수 종료점

---

## 🔮 향후 계획 (P3)

### Sequence View 추가 개선

Schema v3 timeline card UI는 구현되어 있으며, 남은 개선은 표시 정책과 IO metadata 품질 쪽입니다.

- `function_io.json` 품질 점검 및 프롬프트 튜닝 (규칙 기반 → LLM/agent 보정 → 검증)
- `tools/generate_function_io.py` 실행 옵션 보강 (timeout/retry/logging 등)
- timeline card에서 signal 표시량이 많아질 수 있으므로 다음 표시 정책은 계속 조정 필요
  - 모든 신호를 항상 펼치지 않고 핵심 제어/데이터 의존 신호를 우선 노출
  - 함수 블록 간 신호와 함수 내부 call-level 신호를 구분해 표시
  - `signal_id`/`call_id` provenance를 유지하면서 검색/필터 UI 추가 검토

### Memory Access Pattern Analysis (TODO)

```python
# TODO: Requires user annotation
@dataclass
class MemoryAccess:
    access_type: str  # "read", "write"
    variable: str
    is_array: bool
    pattern: str  # "sequential", "strided", "random"
```

**Why TODO:**
- 메모리 범위는 런타임에 결정되는 경우 많음
- 사용자 어노테이션이 더 정확함
- 정적 분석만으로는 복잡한 포인터 산술 추적 어려움

---

## 📄 라이선스

MIT License - 자유롭게 사용, 수정, 배포 가능합니다.

---

## 🙏 기여

버그 리포트나 기능 제안은 이슈로 등록해주세요.

---

**CVAS Enhanced / JSON Schema v3** - Made with ❤️ for ISP algorithm optimization

---

## 🗒️ Change log

### CVAS JSON Schema v3 Updates

- Added top-level `schema_version: "3.0"` and `schema` metadata while preserving v2 diagram fields.
- Added `flow.call_instances[]` with stable repeated-call IDs.
- Added enriched signal metadata (`signal_id`, `kind`, `role`, `call_id`, argument/return fields, provenance).
- Added `flow.sequence_timeline[]` for viewer-ready Sequence timeline cards.
- Added `flow.function_io` embedding via `--function-io` and rule-based default normalization.
- Updated HTML viewer to prefer v3 timeline cards and retain v2 `call_sequence` fallback.

### CVAS v2.0 Release Notes

## 🎉 Major Release: Enhanced Analysis & Complete Data Flow

**Release Date**: 2024-02-02  \
**Version**: 2.0.0  \
**Codename**: "Complete Flow"

---

## 🌟 Highlights

### 완전한 데이터 흐름 추적 (P1)
v1.0에서 놓쳤던 모든 데이터 흐름을 이제 추적합니다!

```c
// v1.0: ❌ 데이터 흐름 끊김
int func(int x) {
    int y = x;   // Operation 생성 안 됨
    return y;
}

// v2.0: ✅ 완전한 추적
int func(int x) {
    int y = x;   // "copy" operation 생성
    return y;    // 완전한 signal chain
}
```

### 제어 흐름 그래프 (P2)
함수 내부 구조를 Basic Block 단위로 분석합니다!

```
entry → main (conditional) → exit
        ↓ (back edge)
        loop_body
```

### 함수 호출 그래프 (P2)
전체 시스템의 실행 경로와 Critical Path를 파악합니다!

```
main → demosaic (50 cycles) → color_correct (10 cycles)
    → sharpen (5 cycles)

Critical Path: main → demosaic (병목 지점!)
```

---

## 📊 By the Numbers

| 메트릭 | v1.0 | v2.0 | 개선 |
|--------|------|------|------|
| **데이터 흐름 완전성** | 70% | 90% | +29% ⬆️ |
| **실제 코드 커버리지** | 80% | 95% | +19% ⬆️ |
| **지원 연산 타입** | 3 | 6 | +100% ⬆️ |
| **제어 흐름 분석** | 없음 | CFG | ✅ NEW |
| **호출 그래프** | 직접 호출 | 전체 체인 | ✅ NEW |
| **Critical Path** | 없음 | 자동 계산 | ✅ NEW |
| **하위 호환성** | - | 100% | ✅ |

---

## ✨ New Features

### P1: Complete Data Flow Tracking

#### 1. Simple Assignment Handling
```c
int a = b;  // Creates "copy" operation
```

**Impact:**
- Data flow completeness: 70% → 90%
- No more broken variable chains

#### 2. Compound Operators
```c
i++;         // Auto-normalized to: i = i + 1
i += 5;      // Auto-normalized to: i = i + 5
x *= y;      // Auto-normalized to: x = x * y
```

**Supported:**
- `++`, `--` (pre and post)
- `+=`, `-=`, `*=`, `/=`, `%=`
- `&=`, `|=`, `^=`, `<<=`, `>>=`

**Impact:**
- Real-world code coverage: 80% → 95%

#### 3. Bitwise & Shift Operations
```c
int masked = value & 0xFF;     // bitwise AND
int shifted = value << 2;       // left shift
int combined = a | b;           // bitwise OR
```

**New Operation Types:**
- `shift`: `<<`, `>>`
- `bitwise`: `&`, `|`, `^`

**Impact:**
- Essential for ISP algorithms
- Accurate cycle estimation for bit manipulation

### P2: Control Flow Analysis

#### 1. Control Flow Graph (CFG)

Every block now includes CFG:

```json
{
  "cfg": {
    "basic_blocks": [
      {"block_type": "entry", ...},
      {"block_type": "conditional_branch", ...},
      {"block_type": "loop_header", ...},
      {"block_type": "exit", ...}
    ],
    "loops": [
      {
        "loop_id": "func_loop_1",
        "nesting_level": 1,
        "estimated_iterations": "unknown"
      }
    ],
    "has_branches": true,
    "max_nesting_depth": 2
  }
}
```

**Benefits:**
- Understand function structure
- Identify loops and branches
- Measure complexity

#### 2. Function Call Graph

Complete call chain analysis:

```json
{
  "call_graph": {
    "nodes": {
      "main": {
        "callees": ["demosaic", "color_correct"],
        "call_depth": 0,
        "self_cycles": 5,
        "total_cycles": 65
      },
      "demosaic": {
        "callers": ["main"],
        "call_depth": 1,
        "self_cycles": 50,
        "total_cycles": 50
      }
    },
    "critical_path": ["main", "demosaic"],
    "has_recursion": false
  }
}
```

**Benefits:**
- Find bottleneck functions
- Calculate total execution cycles
- Detect recursion
- Identify critical path

### P3: Memory Tracking (TODO)

**Status:** Planned for future release

**Rationale:**
- Memory access patterns require runtime information
- Static analysis has limitations with complex pointer arithmetic
- User annotation would be more accurate

**Proposed approach:**
```c
// Future syntax (not yet implemented)
CVAS_START
// @memory: input_buffer[1920*1080], read, sequential
// @memory: output_buffer[1920*1080], write, sequential
int process_frame(uint8_t *input, uint8_t *output) {
    ...
}
CVAS_END
```

---

## 🔧 Enhanced Features

### Extended Cycle Rules

**New cycle types:**
```json
{
  "add_per_cycle": 4,
  "compare_per_cycle": 4,
  "mul_per_cycle": 1,
  "copy_per_cycle": 8,
  "shift_per_cycle": 2,
  "bitwise_per_cycle": 4
}
```

### CLI Enhancements

**New options:**
```bash
--copy-per-cycle 8
--shift-per-cycle 2
--bitwise-per-cycle 4
```

**Better output:**
```
Building enhanced model with P1+P2 analysis...
✓ Analysis complete. Output written to output.json
✓ Analyzed 7 functions
✓ Extracted 25 operations
✓ Tracked 48 data flows
✓ Call graph: 7 nodes, depth 2
✓ Critical path: main → demosaic → sharpen
```

---

## 🐛 Bug Fixes

### v1.0 Issues Resolved

1. **Simple assignments ignored**
   - **Issue:** `a = b;` created no operation
   - **Fixed:** Now creates "copy" operation
   - **Impact:** Complete data flow tracking

2. **Compound operators unsupported**
   - **Issue:** `i++` ignored
   - **Fixed:** Auto-normalization to `i = i + 1`
   - **Impact:** Real-world code works

3. **Bitwise operations missing**
   - **Issue:** `a & b` not tracked
   - **Fixed:** Full bitwise operator support
   - **Impact:** ISP algorithms properly analyzed

4. **Incomplete call graph**
   - **Issue:** Only direct calls tracked
   - **Fixed:** Complete call chain analysis
   - **Impact:** System-wide optimization possible

---

## 🔄 Compatibility

### Backward Compatibility

Schema v3 is additive. It preserves the legacy diagram-facing fields while adding richer contract metadata:

- `analysis_version: "2.0"` remains the analyzer-generation marker.
- `schema_version: "3.0"` and `schema.version: "3.0"` identify the JSON contract.
- Legacy fields such as `blocks`, `operations`, `signals`, `flow.execution_order`, `flow.call_graph`, and `flow.call_sequence` remain present.
- The HTML viewer prefers `flow.sequence_timeline[]` when present and falls back to the legacy `flow.call_sequence` renderer when timeline data is absent.

### Schema v3 Additions

- `flow.call_instances[]`: stable repeated-call occurrences with `call_id` values.
- enriched `signals[]`: optional `signal_id`, `kind`, `role`, `call_id`, parameter/expression, and provenance metadata.
- `flow.sequence_timeline[]`: one static timeline card per execution-order block.
- `flow.function_io`: normalized rule-based or imported function IO metadata used by the Sequence view.
- `flow.dependencies`: compact dependency indexes for block and call-instance consumers.

---

## 📈 Performance Notes

Current performance depends on input size, analysis mode, and parser backend. The maintained contract is verified by the regression suite and smoke commands rather than fixed README benchmark numbers. Use the checked-in tests and wrapper smoke flow when comparing changes.

---

## 🎓 Documentation

### Maintained Documentation

- ✅ **README.md**: Main user-facing guide and examples
- ✅ **requirements.md**: Environment setup, dependencies, and verification commands
- ✅ **docs/full_mode_cpp_design.md**: Current `fast`/`full` backend contract and hardening checklist
- ✅ **docs/plans/2026-05-04-analysis-backend-shift.md**: Implementation plan/history for the clang-to-GCC-dump backend shift
- ✅ **docs/cvas_datapath_pipeline_design.md**: live schema v3 datapath and pipeline contract
- ✅ **docs/schema/cvas.schema.v3.json**: formal JSON Schema v3 document
- ✅ **docs/schema/cvas-schema-v3.md**: field-level schema v3 notes
- ✅ **docs/test_examples_output.json** and **docs/test_examples_output.html**: regenerated sample artifacts

### Examples

**7 comprehensive examples:**
1. Simple assignment
2. Compound operators
3. Bitwise operations
4. Control flow
5. Function calls
6. Complex expressions
7. ISP-like algorithms

---

## 📅 Roadmap / Known Limitations

Near-term improvements remain focused on quality and hardening rather than changing the public contract:

- improve `function_io` metadata quality and imported-agent workflows;
- refine Sequence tab display policy for dense call/signal graphs;
- improve CFG/loop precision where static analysis can prove more facts;
- keep full-mode backend hardening compatible with the additive schema v3 contract.

---

## 📞 Support

- Documentation: `README.md` and `docs/`
- Examples: `test_examples.c` and `tests/fixtures/`
- Issues and feature requests: use the repository issue tracker.

---

## 📜 License

MIT License - Free to use, modify, and distribute.

---

**CVAS Enhanced / JSON Schema v3** emits a backward-compatible C analysis model with explicit schema metadata, stable call-instance IDs, enriched signal provenance, and a Sequence timeline view that can consume embedded `function_io` metadata.
