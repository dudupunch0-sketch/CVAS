from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping

FunctionIOMap = Dict[str, Dict[str, List[str]]]


@dataclass
class ValidationIssue:
    level: str
    code: str
    message: str
    function: str | None = None
    field: str | None = None

    def to_json(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }
        if self.function is not None:
            payload["function"] = self.function
        if self.field is not None:
            payload["field"] = self.field
        return payload


@dataclass
class ValidationResult:
    normalized: FunctionIOMap
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.level == "error" for issue in self.issues)


def function_io_agent_output_schema() -> Dict[str, Any]:
    """Return the JSON schema expected from an external CLI agent."""
    string_array = {
        "type": "array",
        "items": {"type": "string"},
        "uniqueItems": True,
    }
    function_entry = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "reads": string_array,
            "writes": string_array,
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "evidence": {"type": "string"},
            "rationale": {"type": "string"},
            "provenance": {"type": "string"},
        },
        "required": ["reads", "writes"],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://cvas.local/schema/function-io-agent-output-v2.json",
        "title": "CVAS function IO agent output",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "const": "function-io-agent-output/v2"},
            "functions": {
                "type": "object",
                "additionalProperties": function_entry,
            },
            "coverage_gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "kind": {"type": "string"},
                        "subject": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["kind", "subject", "evidence"],
                },
            },
            "provenance": {
                "type": "object",
                "additionalProperties": True,
            },
        },
        "required": ["schema_version", "functions"],
    }


def _coerce_string_list(value: Any, *, function: str, field_name: str) -> List[str]:
    if not isinstance(value, list):
        raise ValueError(f"{function}.{field_name} must be a list of strings")
    output: List[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{function}.{field_name} must be a list of strings")
        output.append(item)
    return sorted(set(output))


def normalize_function_io_payload(payload: Mapping[str, Any]) -> FunctionIOMap:
    """Accept legacy v1 maps or expanded v2 payloads and return a legacy map."""
    if not isinstance(payload, Mapping):
        raise ValueError("function IO payload must be a JSON object")

    if "functions" in payload:
        functions_payload = payload["functions"]
    elif "function_io" in payload:
        functions_payload = payload["function_io"]
    else:
        functions_payload = payload

    if not isinstance(functions_payload, Mapping):
        raise ValueError("function IO functions payload must be a JSON object")

    normalized: FunctionIOMap = {}
    for function, entry in functions_payload.items():
        if not isinstance(function, str):
            raise ValueError("function names must be strings")
        if not isinstance(entry, Mapping):
            raise ValueError(f"{function} entry must be a JSON object")
        for field_name in ("reads", "writes"):
            if field_name not in entry:
                raise ValueError(f"{function}.{field_name} is required")
        normalized[function] = {
            "reads": _coerce_string_list(entry["reads"], function=function, field_name="reads"),
            "writes": _coerce_string_list(entry["writes"], function=function, field_name="writes"),
        }
    return normalized


def validate_function_io_map(
    io_map: FunctionIOMap,
    *,
    function_params: Mapping[str, List[str]],
    validation_mode: str = "warn",
) -> ValidationResult:
    """Validate a normalized map against the recorded static snapshot."""
    if validation_mode not in {"warn", "strict"}:
        raise ValueError("validation_mode must be 'warn' or 'strict'")

    issues: List[ValidationIssue] = []
    for function in sorted(function_params):
        if function not in io_map:
            issues.append(
                ValidationIssue(
                    level="error" if validation_mode == "strict" else "warning",
                    code="missing_function",
                    message=(
                        f"Agent output omitted function '{function}' from the recorded static snapshot; "
                        "use --merge-missing-from-rule or add an explicit entry."
                    ),
                    function=function,
                )
            )

    for function, entry in io_map.items():
        known_params = set(function_params.get(function, []))
        if function not in function_params:
            issues.append(
                ValidationIssue(
                    level="error" if validation_mode == "strict" else "warning",
                    code="unknown_function",
                    message=(
                        f"Agent output references function '{function}' that is absent from the "
                        "recorded static snapshot; preserve as an agent-only finding if evidence exists."
                    ),
                    function=function,
                )
            )
            continue
        for field_name in ("reads", "writes"):
            values = entry.get(field_name, [])
            if not isinstance(values, list):
                issues.append(
                    ValidationIssue(
                        level="error",
                        code="invalid_field_type",
                        message=f"{function}.{field_name} must be a list",
                        function=function,
                        field=field_name,
                    )
                )
                continue
            for param in values:
                if param not in known_params:
                    issues.append(
                        ValidationIssue(
                            level="error" if validation_mode == "strict" else "warning",
                            code="unknown_parameter",
                            message=(
                                f"{function}.{field_name} references '{param}', which is absent from "
                                "the recorded static parameter snapshot."
                            ),
                            function=function,
                            field=field_name,
                        )
                    )
    return ValidationResult(normalized=io_map, issues=issues)


def validation_report_to_json(
    result: ValidationResult,
    *,
    coverage_gaps: List[Any] | None = None,
) -> Dict[str, Any]:
    errors = sum(1 for issue in result.issues if issue.level == "error")
    warnings = sum(1 for issue in result.issues if issue.level == "warning")
    return {
        "schema_version": "function-io-validation-report/v1",
        "status": "failed" if result.has_errors else "ok",
        "summary": {
            "functions": len(result.normalized),
            "errors": errors,
            "warnings": warnings,
        },
        "issues": [issue.to_json() for issue in result.issues],
        "coverage_gaps": coverage_gaps or [],
    }
