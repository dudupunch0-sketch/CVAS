# CVAS MVP - C-model Block Diagram Parser

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

ISP 알고리즘 C-model에서 `CVAS_START` / `CVAS_END` 구간만 분석하여 블록 다이어그램 입력 데이터를 생성하는 MVP 파서입니다.

## ✨ 주요 특징

- **🎯 파싱 범위 제한**: `CVAS_START` ~ `CVAS_END` 구간만 처리 (그 외는 무시)
- **📦 Block 중심 모델링**: 함수 → Block, 함수 인자/반환값 → Signal
- **🔢 정확한 표현식 파싱**: Shunting-yard 알고리즘으로 연산자 우선순위 처리
- **🔗 완전한 Data Flow 추적**: 중간 변수를 포함한 모든 데이터 흐름 기록
- **⚙️ 연산 카운트 및 Cycle 추정**: add/compare/multiply 연산 노드 기준
- **🛠️ 유연한 설정**: JSON 파일 또는 CLI 인자로 Cycle rule 변경 가능

## 📋 요구사항

- Python 3.10 이상
- 표준 라이브러리만 사용 (외부 의존성 없음)

## 🚀 빠른 시작

### 기본 사용법

```bash
python cvas_mvp.py model.c -o output.json
```

### Cycle Rule 변경

**CLI 인자 사용:**
```bash
python cvas_mvp.py model.c \
  --add-per-cycle 4 \
  --compare-per-cycle 4 \
  --mul-per-cycle 1 \
  -o output.json
```

**JSON 설정 파일 사용:**

`cycle.json`:
```json
{
  "add_per_cycle": 4,
  "compare_per_cycle": 4,
  "mul_per_cycle": 1
}
```

```bash
python cvas_mvp.py model.c --cycle-config cycle.json -o output.json
```

## 📝 입력 파일 형식

C 소스 파일에 `CVAS_START`와 `CVAS_END` 마커를 포함하세요:

```c
// 이 부분은 무시됩니다
#include <stdio.h>

void preprocessing() {
    // ...
}

CVAS_START

// 간단한 필터 함수
int low_pass_filter(int pixel, int neighbor) {
    int sum = pixel + neighbor;
    int avg = sum / 2;
    return avg;
}

// 엣지 검출 함수
int edge_detect(int center, int left, int right) {
    int diff_left = center - left;
    int diff_right = center - right;
    int edge = diff_left + diff_right;

    if (edge > 50) {
        edge = 50;
    }

    return edge;
}

CVAS_END

// 이 부분도 무시됩니다
int main() {
    return 0;
}
```

## 📊 출력 JSON 구조

### 전체 구조

```json
{
  "blocks": [...],           // 함수 정의 → Block
  "operations": [...],       // 내부 연산 노드
  "signals": [...],          // Block 간 / Operation 간 연결
  "flow": {...},            // 실행 흐름 메타데이터
  "diagram_hint": {...},    // 시각화 힌트
  "note": "..."             // 추가 정보
}
```

### Blocks

각 함수는 하나의 Block으로 변환됩니다:

```json
{
  "block_id": "B1",
  "block_name": "edge_detect",
  "inputs": ["center", "left", "right"],
  "outputs": ["return"],
  "internal_ops_summary": {
    "add": 3,
    "compare": 1,
    "multiply": 0
  },
  "estimated_cycles": 2,
  "note": "contains conditional; internal op nodes emitted",
  "position": {
    "x": "TBD by drawing tool",
    "y": "TBD by drawing tool"
  }
}
```

**필드 설명:**
- `block_id`: 고유 식별자 (B1, B2, ...)
- `block_name`: 함수명
- `inputs`: 파라미터 이름 목록
- `outputs`: return 타입이 void가 아니면 `['return']`
- `internal_ops_summary`: 내부 연산 타입별 개수
- `estimated_cycles`: Cycle rule 기반 추정치
- `note`: 제어 흐름 정보 + 추가 메모
- `position`: 시각화 도구에서 사용할 좌표 (기본값: TBD)

### Operations

각 연산은 개별 노드로 표현됩니다:

```json
{
  "op_id": "B1_op_1",
  "op_type": "add",
  "inputs": ["center", "left"],
  "outputs": ["tmp_2"],
  "parent_block_id": "B1"
}
```

**연산 타입:**
- `add`: `+`, `-` 연산
- `compare`: `<`, `>`, `<=`, `>=`, `==`, `!=` 연산
- `multiply`: `*`, `/` 연산

**표현식 파싱 예시:**

```c
int result = a + b * c;
```

생성되는 Operations:
```json
[
  {
    "op_id": "B1_op_1",
    "op_type": "multiply",
    "inputs": ["b", "c"],
    "outputs": ["tmp_2"]
  },
  {
    "op_id": "B1_op_2",
    "op_type": "add",
    "inputs": ["a", "tmp_2"],
    "outputs": ["result"]
  }
]
```

### Signals

Block 간 또는 Operation 간 연결을 표현합니다:

```json
{
  "source_id": "B1_op_1",
  "source_type": "operation",
  "destination_id": "B1_op_2",
  "destination_type": "operation",
  "signal_name": "tmp_2",
  "direction": "internal",
  "comment": "operand flow"
}
```

**Signal 타입:**
- **Block → Block**: 함수 호출 (인자 전달, 반환값)
- **Block → Operation**: Block 입력 파라미터 사용
- **Operation → Operation**: 중간 변수 흐름
- **Operation → Block**: 함수 반환

**Direction 값:**
- `in`: Block으로 들어오는 신호
- `out`: Block에서 나가는 신호
- `internal`: Block 내부 연결

### 완전한 예제

**입력:**
```c
CVAS_START

int multiply_add(int x, int y, int z) {
    int product = x * y;
    int result = product + z;
    return result;
}

CVAS_END
```

**출력 (요약):**
```json
{
  "blocks": [
    {
      "block_id": "B1",
      "block_name": "multiply_add",
      "inputs": ["x", "y", "z"],
      "outputs": ["return"],
      "internal_ops_summary": {
        "add": 1,
        "compare": 0,
        "multiply": 1
      },
      "estimated_cycles": 2
    }
  ],
  "operations": [
    {
      "op_id": "B1_op_1",
      "op_type": "multiply",
      "inputs": ["x", "y"],
      "outputs": ["product"]
    },
    {
      "op_id": "B1_op_2",
      "op_type": "add",
      "inputs": ["product", "z"],
      "outputs": ["result"]
    },
    {
      "op_id": "B1_op_3",
      "op_type": "...",
      "inputs": ["result"],
      "outputs": ["return"]
    }
  ],
  "signals": [
    {
      "source_id": "B1",
      "source_type": "block",
      "destination_id": "B1_op_1",
      "destination_type": "operation",
      "signal_name": "x",
      "direction": "internal"
    },
    {
      "source_id": "B1_op_1",
      "source_type": "operation",
      "destination_id": "B1_op_2",
      "destination_type": "operation",
      "signal_name": "product",
      "direction": "internal"
    }
  ]
}
```

## ⚙️ Cycle 추정 규칙

### 기본 규칙

```python
add_per_cycle = 4      # 1 cycle에 4개의 add/sub 연산
compare_per_cycle = 4  # 1 cycle에 4개의 비교 연산
mul_per_cycle = 1      # 1 cycle에 1개의 곱셈/나눗셈 연산
```

### 계산 방법

```
총 Cycles = ceil(add_count / add_per_cycle)
          + ceil(compare_count / compare_per_cycle)
          + ceil(multiply_count / mul_per_cycle)
```

**예시:**
```c
// 3 add, 1 compare, 0 multiply
int result = a + b + c;
if (result > threshold) {
    result = threshold;
}
```

기본 규칙 (add=4, compare=4):
- Add cycles: ceil(3/4) = 1
- Compare cycles: ceil(1/4) = 1
- **Total: 2 cycles**

### 하드웨어 특성에 맞게 조정

**고성능 HW (병렬 처리 많음):**
```bash
python cvas_mvp.py model.c --add-per-cycle 8 --mul-per-cycle 2
```

**저성능 HW (직렬 처리):**
```bash
python cvas_mvp.py model.c --add-per-cycle 2 --mul-per-cycle 1
```

## 🔍 주요 알고리즘

### 1. Shunting-yard 알고리즘

복잡한 표현식을 정확히 파싱하기 위해 Shunting-yard 알고리즘을 사용합니다:

```
Infix:   a + b * c
         ↓ (Shunting-yard)
Postfix: a b c * +
         ↓ (Evaluate)
Operations:
  1. multiply(b, c) → tmp_1
  2. add(a, tmp_1) → result
```

**장점:**
- ✅ 연산자 우선순위 정확히 처리
- ✅ 괄호 처리
- ✅ 복잡한 중첩 표현식 지원

### 2. Data Flow 추적

각 변수의 생산자(producer)를 추적하여 완전한 데이터 흐름 그래프를 생성합니다:

```python
var_producers = {
    "x": ("block", "B1"),      # 입력 파라미터
    "tmp_1": ("operation", "B1_op_1"),  # 연산 결과
    "result": ("operation", "B1_op_2")  # 최종 결과
}
```

이를 통해 모든 Operation 간 의존성을 정확히 파악합니다.

## ⚠️ 해석 제한사항

이 파서는 MVP로서 다음과 같은 제한사항이 있습니다:

### 지원하지 않는 기능

- ❌ **전처리 매크로**: `#define`, `#ifdef` 등은 해석하지 않음
- ❌ **복잡한 타입**: 함수 포인터, 구조체 멤버 접근 제한적
- ❌ **외부 라이브러리**: 알려진 함수만 추적 (선언되지 않은 함수 무시)
- ❌ **고급 제어 흐름**: `goto`, `switch-case`는 부분적 지원

### 단순화된 처리

- 📌 문자열/주석은 연산 카운트에서 제외
- 📌 함수 호출 흐름은 로컬 함수만 추적
- 📌 배열 인덱싱은 단순 변수로 처리
- 📌 포인터 역참조는 식별자로만 처리

### Unknown 처리

다음 항목은 `unknown` 또는 누락 처리:
- 복잡한 포인터 연산
- 비트 연산자 (`&`, `|`, `^`, `<<`, `>>`)
- 삼항 연산자 (`? :`)
- 증감 연산자 (`++`, `--`)

## 💡 사용 팁

### 1. 코드 작성 가이드

파서가 잘 해석할 수 있도록 코드를 작성하는 팁:

```c
// ✅ GOOD - 간단하고 명확
int result = a + b;
if (result > threshold) {
    result = threshold;
}

// ❌ AVOID - 복잡한 표현식
int result = (a > b) ? (a * 2 + c) : (b - d);
```

### 2. 함수 분리

복잡한 로직은 여러 함수로 분리하면 Block Diagram이 더 명확해집니다:

```c
CVAS_START

// 각 기능을 별도 함수로
int clamp(int value, int max) {
    if (value > max) {
        return max;
    }
    return value;
}

int calculate_edge(int center, int neighbor) {
    int diff = center - neighbor;
    return clamp(diff, 50);
}

CVAS_END
```

### 3. 디버깅

생성된 JSON을 확인하여 파싱이 올바른지 검증하세요:

```bash
# Pretty print JSON
python cvas_mvp.py model.c | jq .

# 특정 블록만 확인
python cvas_mvp.py model.c | jq '.blocks[]'

# 연산 개수 확인
python cvas_mvp.py model.c | jq '.blocks[].internal_ops_summary'
```

## 🔧 문제 해결

### "CVAS region not found" 경고

**원인**: `CVAS_START`와 `CVAS_END` 마커가 없거나 순서가 잘못됨

**해결**:
```c
// ✅ 올바른 순서
CVAS_START
// ... code ...
CVAS_END

// ❌ 잘못된 순서
CVAS_END
// ... code ...
CVAS_START
```

### "No functions found" 경고

**원인**: CVAS 구간 내에 함수 정의가 없음

**해결**:
```c
CVAS_START

// ✅ 함수 정의 필요
int process(int x) {
    return x * 2;
}

CVAS_END
```

### 연산이 누락됨

**원인**: 지원하지 않는 연산자 사용

**해결**: 지원 연산자 사용
- 지원: `+`, `-`, `*`, `/`, `<`, `>`, `<=`, `>=`, `==`, `!=`
- 미지원: `++`, `--`, `&`, `|`, `^`, `<<`, `>>`

## 📚 추가 자료

### JSON Schema (참고용)

생성된 JSON의 스키마:

```typescript
interface Model {
  blocks: Block[];
  operations: Operation[];
  signals: Signal[];
  flow: Flow;
  diagram_hint: DiagramHint;
  note: string;
}

interface Block {
  block_id: string;        // "B1", "B2", ...
  block_name: string;      // Function name
  inputs: string[];        // Parameter names
  outputs: string[];       // ["return"] or []
  internal_ops_summary: {
    add: number;
    compare: number;
    multiply: number;
  };
  estimated_cycles: number;
  note: string;
  position: {
    x: string;
    y: string;
  };
}

interface Operation {
  op_id: string;          // "B1_op_1", "B1_op_2", ...
  op_type: "add" | "compare" | "multiply";
  inputs: string[];       // Operand names
  outputs: string[];      // Result variable name
  parent_block_id: string;
}

interface Signal {
  source_id: string;
  source_type: "block" | "operation";
  destination_id: string;
  destination_type: "block" | "operation";
  signal_name: string;
  direction: "in" | "out" | "internal";
  comment?: string;
}
```

## 📄 라이선스

MIT License - 자유롭게 사용, 수정, 배포 가능합니다.

## 🤝 기여

버그 리포트나 기능 제안은 이슈로 등록해주세요.

---

**Made with ❤️ for ISP algorithm visualization**
