# CVAS JSON Schema v3

Schema v3 is an additive contract for CVAS analysis JSON. It keeps the v2 diagram fields (`blocks`, `operations`, `signals`, `flow.execution_order`, `flow.call_sequence`) and adds explicit execution-timeline facts for the HTML Sequence tab.

## Version fields

- `schema_version: "3.0"` identifies the JSON contract.
- `schema.name: "cvas-analysis"` and `schema.version: "3.0"` describe the contract family.
- `analysis_version` remains the analyzer implementation generation and is not used for viewer schema branching.
- v2 JSON has no `schema_version`; the viewer treats it as `2.x` and uses fallback inference.

## `flow.execution_order`

`flow.execution_order` remains a static block/function order used for visualization. It is not a dynamic runtime trace. v3 adds:

```json
"execution_order_meta": {
  "kind": "static_block_order",
  "source": "analysis_queue",
  "description": "Static function/block order used for visualization; not a dynamic runtime trace."
}
```

## `flow.call_instances[]`

Each direct call occurrence is represented by a stable call instance. Repeated calls to the same callee receive different IDs, e.g. `C_B2_0001` and `C_B2_0002`.

Core fields:

- `call_id`: deterministic call occurrence ID.
- `caller_block_id`, `caller_function`.
- `callee_block_id`, `callee_function`.
- `ordinal_in_caller`: 1-based call occurrence order inside the caller.
- `args[]`: argument expressions with `arg_index`, `param`, `expr`, and `signal_id`.
- `assigned`: optional return assignment target and return `signal_id`.
- `source`: file/line/column/text. v3.0 allows `line` and `column` to be `null`.
- `provenance`: static analysis source, parser, and confidence.

## Enriched `signals[]`

Legacy endpoint fields remain required:

- `source_id`, `source_type`, `destination_id`, `destination_type`.
- `signal_name`, `direction`, `comment`.

v3 adds optional semantic fields:

- `signal_id`: deterministic signal identity.
- `kind`: e.g. `internal_operand`, `internal_copy`, `internal_store`, `function_return`, `call_argument`, `call_return`, `unknown`.
- `role`: `read`, `write`, `read_write`, `control`, or `unknown`.
- `call_id`, `arg_index`, `param`, `expr`, `target` for call-related signals.
- `source_function`, `destination_function`.
- `provenance`.

Call argument signals use IDs like `S_C_B2_0001_ARG_0`; call return signals use IDs like `S_C_B2_0001_RET`.

## `flow.sequence_timeline[]`

The Sequence tab should prefer this viewer-ready model. Each step corresponds to one static block-order entry:

- `step_id`, `order_index`, `block_id`, `function`.
- `call_ids_as_caller`, `call_ids_as_callee`.
- `incoming_signal_ids`, `outgoing_signal_ids`.
- `read_write_summary` with `reads_from_other`, `read_by_other`, `writes_to_other`, and `written_by_other` buckets.

Signal and call IDs must reference objects present in the same JSON document.

## Embedded function IO

`flow.function_io` embeds normalized function read/write metadata when available. The viewer uses it to add function-level reads/writes to v3 timeline cards and to guide legacy Sequence layout. The lookup priority is:

1. embedded `flow.function_io`,
2. build-time embedded `function_io.json` or a runtime-loaded/auto-loaded sidecar when `flow.function_io` is absent,
3. fallback inference.

## Files

- Formal schema: `docs/schema/cvas.schema.v3.json`.
- Minimal example fixture: `tests/fixtures/schema/sequence_timeline_v3.expected.json`.
