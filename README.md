# CVAS

ISP 알고리즘 C-model에서 `CVAS_START` / `CVAS_END` 구간만 분석하여 블록 다이어그램 입력 데이터를 생성하는 MVP 파서입니다.

## 주요 특징
- **파싱 범위 제한**: `CVAS_START` ~ `CVAS_END` 구간만 처리 (그 외는 무시).
- **Block 중심 모델링**: 함수 = Block, 함수 인자/반환값 = Signal.
- **연산 카운트 및 cycle 추정**: 연산 노드(add/sub, compare, multiply) 기준.
- **Cycle rule은 설정 가능**: JSON 설정 또는 CLI 인자.

## 사용 방법

```bash
python src/cvas_mvp.py path/to/model.c -o output.json
```

### Cycle rule 변경

```bash
python src/cvas_mvp.py path/to/model.c \
  --add-per-cycle 4 \
  --compare-per-cycle 4 \
  --mul-per-cycle 1
```

또는 JSON 설정 파일 사용:

```json
{
  "add_per_cycle": 4,
  "compare_per_cycle": 4,
  "mul_per_cycle": 1
}
```

```bash
python src/cvas_mvp.py path/to/model.c --cycle-config cycle.json
```

## 출력 JSON 구조 (요약)

```json
{
  "blocks": [
    {
      "block_id": "B1",
      "block_name": "demosaic",
      "inputs": ["raw_pixel"],
      "outputs": ["return"],
      "internal_ops_summary": {
        "add": 12,
        "compare": 4,
        "multiply": 2
      },
      "estimated_cycles": 6,
      "note": "contains loop; internal op nodes emitted",
      "position": {
        "x": "TBD by drawing tool",
        "y": "TBD by drawing tool"
      }
    }
  ],
  "operations": [
    {
      "op_id": "B1_op_1",
      "op_type": "add",
      "inputs": ["raw_pixel", "gain"],
      "outputs": ["tmp_2"],
      "parent_block_id": "B1"
    },
    {
      "op_id": "B1_op_2",
      "op_type": "compare",
      "inputs": ["tmp_2", "threshold"],
      "outputs": ["return"],
      "parent_block_id": "B1"
    }
  ],
  "signals": [
    {
      "source_id": "B1",
      "source_type": "block",
      "destination_id": "B2",
      "destination_type": "block",
      "signal_name": "rgb_pixel",
      "direction": "out",
      "comment": "return flow"
    },
    {
      "source_id": "B1_op_1",
      "source_type": "operation",
      "destination_id": "B1_op_2",
      "destination_type": "operation",
      "signal_name": "tmp_2",
      "direction": "internal",
      "comment": "operand flow"
    },
    {
      "source_id": "B1_op_2",
      "source_type": "operation",
      "destination_id": "B1",
      "destination_type": "block",
      "signal_name": "return",
      "direction": "out",
      "comment": "return flow"
    }
  ],
  "flow": {
    "execution_order": ["B1", "B2"],
    "parallelism": "unknown"
  },
  "diagram_hint": {
    "layout": "TBD by drawing tool"
  },
  "note": "internal op nodes emitted"
}
```

## 해석 제한
- 전처리 매크로, 복잡한 템플릿, 외부 구현은 해석하지 않습니다.
- 문자열/주석은 연산 카운트에서 제외합니다.
- 함수 호출 흐름은 **알 수 있는 함수 간 인자/반환 흐름**만 기록합니다.
- 해석 불가 항목은 `unknown` 또는 누락 처리합니다.
