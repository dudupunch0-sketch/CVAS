#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

REQUIRED_FIELDS = ["blocks", "operations", "signals", "flow"]
SEQUENCE_RENDERER_V3_TIMELINE = "v3_timeline"
SEQUENCE_RENDERER_LEGACY = "legacy"


def detect_schema_version(data: dict) -> str:
    if isinstance(data, dict):
        schema_version = data.get("schema_version")
        if isinstance(schema_version, str):
            return schema_version
        schema = data.get("schema")
        if isinstance(schema, dict) and isinstance(schema.get("version"), str):
            return schema["version"]
    return "2.x"


def normalize_function_io_map(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    functions = value.get("functions")
    if isinstance(functions, dict):
        return functions
    return value


def get_viewer_function_io_map(data: dict, state_function_io: object | None = None) -> dict:
    flow = data.get("flow") if isinstance(data, dict) else {}
    flow_io = flow.get("function_io") if isinstance(flow, dict) else None
    flow_map = normalize_function_io_map(flow_io)
    if flow_map:
        return flow_map
    return normalize_function_io_map(state_function_io)


def select_sequence_renderer(data: dict) -> str:
    flow = data.get("flow") if isinstance(data, dict) else {}
    timeline = flow.get("sequence_timeline") if isinstance(flow, dict) else None
    if isinstance(timeline, list) and timeline:
        return SEQUENCE_RENDERER_V3_TIMELINE
    return SEQUENCE_RENDERER_LEGACY


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _call_arg_expr_by_param(call: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    args = call.get("args") if isinstance(call, dict) else None
    if not isinstance(args, list):
        return mapping
    for index, arg in enumerate(args):
        if isinstance(arg, dict):
            param = arg.get("param")
            expr = arg.get("expr")
            if param is not None and expr is not None:
                mapping[str(param)] = str(expr)
        elif arg is not None:
            callee_params = call.get("callee_params")
            if isinstance(callee_params, list) and index < len(callee_params):
                mapping[str(callee_params[index])] = str(arg)
    return mapping


def _map_call_io_names(names: list[str], call: dict | None) -> list[str]:
    if not call:
        return names
    expr_by_param = _call_arg_expr_by_param(call)
    return [expr_by_param.get(name, name) for name in names]


def _timeline_io_item(
    function_io_map: dict,
    function_name: object,
    role: str,
    call: dict | None = None,
) -> dict | None:
    if not isinstance(function_name, str) or not function_name:
        return None
    io = function_io_map.get(function_name)
    if not isinstance(io, dict):
        return None
    contract_reads = _as_string_list(io.get("reads"))
    contract_writes = _as_string_list(io.get("writes"))
    item = {
        "role": role,
        "function": function_name,
        "reads": _map_call_io_names(contract_reads, call),
        "writes": _map_call_io_names(contract_writes, call),
        "contract_reads": contract_reads,
        "contract_writes": contract_writes,
    }
    if call:
        item["call_id"] = call.get("call_id")
        item["caller_function"] = call.get("caller_function")
        item["callee_function"] = call.get("callee_function")
        assigned = call.get("assigned")
        if isinstance(assigned, dict) and assigned.get("target") is not None:
            item["assigned"] = str(assigned["target"])
    provenance = io.get("provenance")
    if isinstance(provenance, dict):
        item["provenance"] = provenance
    return item


def summarize_timeline_function_io(
    data: dict,
    step: dict,
    state_function_io: object | None = None,
) -> list[dict]:
    """Return the v3 viewer's function_io summary for a timeline card."""
    function_io_map = get_viewer_function_io_map(data, state_function_io)
    if not function_io_map or not isinstance(step, dict):
        return []

    flow = data.get("flow") if isinstance(data, dict) else {}
    call_instances = flow.get("call_instances") if isinstance(flow, dict) else []
    call_by_id = {
        call.get("call_id"): call
        for call in call_instances
        if isinstance(call, dict) and call.get("call_id") is not None
    }

    summary: list[dict] = []
    step_item = _timeline_io_item(function_io_map, step.get("function"), "step_function")
    if step_item:
        summary.append(step_item)

    for call_id in step.get("call_ids_as_caller", []) or []:
        call = call_by_id.get(call_id)
        if not isinstance(call, dict):
            continue
        item = _timeline_io_item(
            function_io_map,
            call.get("callee_function"),
            "called_function",
            call,
        )
        if item:
            summary.append(item)

    for call_id in step.get("call_ids_as_callee", []) or []:
        call = call_by_id.get(call_id)
        if not isinstance(call, dict):
            continue
        item = _timeline_io_item(
            function_io_map,
            call.get("caller_function"),
            "caller_function",
            call,
        )
        if item:
            summary.append(item)

    return summary


def load_json(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[json_to_html] Failed to read input JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[json_to_html] Failed to parse JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if not isinstance(data, dict):
        print("[json_to_html] JSON root must be an object.", file=sys.stderr)
        raise SystemExit(1)

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        print(f"[json_to_html] Missing required fields: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(1)

    return data


def build_html(data: dict) -> str:
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    default_function_io = {}
    function_io_path = Path(__file__).with_name("function_io.json")
    if function_io_path.exists():
        try:
            default_function_io = json.loads(function_io_path.read_text(encoding="utf-8"))
            if not isinstance(default_function_io, dict):
                default_function_io = {}
        except (OSError, json.JSONDecodeError):
            default_function_io = {}
    function_io_json = json.dumps(default_function_io, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>CVAS Diagram Viewer</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f6f8;
      --panel-bg: #ffffff;
      --border: #2f343b;
      --muted: #6b7280;
      --accent: #2563eb;
      --edge: #4b5563;
      --edge-secondary: #9ca3af;
      --edge-call: #0ea5e9;
      --highlight: #f59e0b;
    }}
    body {{
      margin: 0;
      font-family: "Inter", "Noto Sans", sans-serif;
      background: var(--bg);
      color: #111827;
      height: 100vh;
      display: flex;
      flex-direction: column;
    }}
    header {{
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 12px 16px;
      background: var(--panel-bg);
      border-bottom: 1px solid #e5e7eb;
      flex-wrap: wrap;
    }}
    header input[type=\"text\"] {{
      padding: 6px 10px;
      min-width: 220px;
      border: 1px solid #d1d5db;
      border-radius: 6px;
    }}
    header label {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 14px;
    }}
    header button {{
      padding: 6px 12px;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      background: #fff;
      cursor: pointer;
    }}
    header .tab {{
      border: 1px solid #d1d5db;
      border-radius: 999px;
      padding: 6px 14px;
      background: #fff;
      font-size: 13px;
    }}
    header .tab.active {{
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }}
    #ioStatus {{
      font-size: 12px;
      color: #374151;
      background: #eef2ff;
      border: 1px solid #c7d2fe;
      border-radius: 999px;
      padding: 4px 10px;
      white-space: nowrap;
    }}
    main {{
      flex: 1;
      display: grid;
      grid-template-columns: 2fr 1fr;
      min-height: 0;
    }}
    #diagram-panel {{
      position: relative;
      background: #fff;
      border-right: 1px solid #e5e7eb;
      overflow: hidden;
    }}
    #sequence-panel {{
      position: relative;
      background: #fff;
      border-right: 1px solid #e5e7eb;
      overflow: auto;
      padding: 16px 20px;
    }}
    #sequence-panel[hidden] {{
      display: none;
    }}
    .seq-group {{
      flex: 0 0 auto;
      margin-bottom: 10px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      padding: 8px;
      background: #f9fafb;
    }}
    .seq-title {{
      font-size: 12px;
      font-weight: 600;
      margin: 0 0 6px 0;
      color: #111827;
    }}
    .seq-board {{
      position: relative;
      display: flex;
      flex-wrap: nowrap;
      gap: 10px;
      align-items: flex-start;
      min-height: 120px;
      width: max-content;
      padding: 8px 8px 16px 8px;
    }}
    .seq-board-overlay {{
      position: absolute;
      inset: 0;
      pointer-events: none;
      overflow: visible;
      z-index: 0;
    }}
    .seq-group {{
      position: relative;
      z-index: 1;
    }}
    .seq-group-edge {{
      stroke: #94a3b8;
      stroke-width: 1.4;
      fill: none;
      marker-end: url(#group-arrowhead);
      opacity: 0.9;
    }}
    .seq-canvas {{
      position: relative;
      min-height: 64px;
      border-radius: 6px;
      background: #ffffff;
      border: 1px dashed #e5e7eb;
    }}
    .seq-node {{
      position: absolute;
      padding: 3px 7px;
      background: #fff;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      font-size: 11px;
      display: inline-flex;
      gap: 4px;
      align-items: center;
      cursor: grab;
      user-select: none;
      line-height: 1.15;
    }}
    .timeline-shell {{
      display: grid;
      gap: 12px;
      max-width: 1120px;
      padding-bottom: 24px;
    }}
    .timeline-banner {{
      border: 1px solid #bfdbfe;
      background: #eff6ff;
      color: #1e3a8a;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 13px;
      line-height: 1.45;
    }}
    .timeline-card {{
      border: 1px solid #d1d5db;
      background: #ffffff;
      border-radius: 12px;
      padding: 12px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
    }}
    .timeline-card-header {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 8px;
    }}
    .timeline-title {{
      font-weight: 700;
      color: #111827;
      font-size: 14px;
    }}
    .timeline-subtitle {{
      color: #6b7280;
      font-size: 12px;
      margin-top: 2px;
    }}
    .timeline-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      justify-content: flex-end;
    }}
    .timeline-pill {{
      border-radius: 999px;
      background: #f3f4f6;
      color: #374151;
      padding: 3px 8px;
      font-size: 11px;
      border: 1px solid #e5e7eb;
    }}
    .timeline-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
    }}
    .timeline-section {{
      border: 1px dashed #e5e7eb;
      border-radius: 8px;
      padding: 8px;
      background: #f9fafb;
      min-height: 54px;
    }}
    .timeline-section-title {{
      font-weight: 600;
      color: #374151;
      margin-bottom: 6px;
      font-size: 12px;
    }}
    .timeline-list {{
      display: grid;
      gap: 5px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .timeline-item {{
      border-radius: 6px;
      border: 1px solid #e5e7eb;
      background: #fff;
      padding: 5px 6px;
      font-size: 12px;
      line-height: 1.35;
    }}
    .timeline-empty {{
      color: #9ca3af;
      font-size: 12px;
    }}
    .timeline-mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      color: #334155;
    }}
    .seq-count {{
      background: #111827;
      color: #fff;
      border-radius: 999px;
      padding: 2px 6px;
      font-size: 11px;
    }}
    .seq-svg {{
      position: absolute;
      inset: 0;
      pointer-events: none;
    }}
    .seq-edge {{
      stroke: #9ca3af;
      stroke-width: 1.2;
      fill: none;
      marker-end: url(#arrowhead);
    }}
    #sequence-content {{
      transform-origin: top left;
    }}
    #detail-panel {{
      background: var(--panel-bg);
      padding: 12px 16px;
      overflow: auto;
    }}
    #detail-panel h2 {{
      margin-top: 0;
      font-size: 16px;
    }}
    #detail-json {{
      background: #0f172a;
      color: #e2e8f0;
      padding: 12px;
      border-radius: 8px;
      font-size: 12px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    #anomaly-list {{
      margin-top: 16px;
      padding: 10px;
      border: 1px dashed #f59e0b;
      border-radius: 8px;
      background: #fffbeb;
    }}
    #anomaly-list ul {{
      margin: 8px 0 0 16px;
      padding: 0;
      font-size: 13px;
      color: #92400e;
    }}
    svg {{
      width: 100%;
      height: 100%;
    }}
    .node-box {{
      fill: #ffffff;
      stroke: var(--border);
      stroke-width: 2;
      rx: 10;
      ry: 10;
    }}
    .node-box.loop {{
      stroke-dasharray: 6 4;
    }}
    .node-title {{
      font-weight: 600;
      font-size: 13px;
      fill: #111827;
    }}
    .node-line {{
      font-size: 12px;
      fill: var(--muted);
    }}
    .edge-line {{
      fill: none;
      stroke: var(--edge);
      stroke-width: 1.6;
    }}
    .edge-line.exec {{
      stroke: var(--edge-secondary);
      stroke-dasharray: 4 4;
    }}
    .edge-line.call {{
      stroke: var(--edge-call);
      stroke-dasharray: 6 3;
    }}
    .edge-label {{
      font-size: 11px;
      fill: #374151;
      pointer-events: none;
    }}
    .highlight rect {{
      stroke: var(--highlight);
      stroke-width: 3;
    }}
  </style>
</head>
<body>
  <header>
    <strong>CVAS Diagram Viewer</strong>
    <input id=\"searchInput\" type=\"text\" placeholder=\"Search block_id or block_name\" />
    <label><input id=\"toggleData\" type=\"checkbox\" checked /> Show data-flow edges</label>
    <label><input id=\"toggleExec\" type=\"checkbox\" /> Show execution-order edges</label>
    <label><input id=\"toggleCall\" type=\"checkbox\" /> Show call-graph edges</label>
    <button id=\"resetBtn\">Reset View</button>
    <button id=\"tabDiagram\" class=\"tab active\">Diagram</button>
    <button id=\"tabSequence\" class=\"tab\">Sequence</button>
    <button id=\"seqZoomOut\" class=\"tab\">Seq -</button>
    <button id=\"seqZoomReset\" class=\"tab\">Seq 100%</button>
    <button id=\"seqZoomIn\" class=\"tab\">Seq +</button>
    <button id=\"seqLoad\" class=\"tab\">Load Map</button>
    <button id=\"seqExport\" class=\"tab\">Export Map</button>
    <button id=\"ioLoad\" class=\"tab\">Load IO</button>
    <span id=\"ioStatus\">IO: none</span>
    <input id=\"seqFile\" type=\"file\" accept=\"application/json\" hidden />
    <input id=\"ioFile\" type=\"file\" accept=\"application/json\" hidden />
  </header>
  <main>
    <section id=\"diagram-panel\">
      <svg id=\"diagramSvg\" xmlns=\"http://www.w3.org/2000/svg\"></svg>
    </section>
    <section id=\"sequence-panel\" hidden>
      <div id=\"sequence-content\"></div>
    </section>
    <aside id=\"detail-panel\">
      <h2>Details</h2>
      <div id=\"detail-json\">Click a node or edge to inspect data.</div>
      <div id=\"anomaly-list\" hidden>
        <strong>Anomaly Report</strong>
        <ul id=\"anomaly-items\"></ul>
      </div>
    </aside>
  </main>

  <!-- Ensure ./assets/elk.bundled.js is available next to this HTML file for offline use. -->
  <script src=\"./assets/elk.bundled.js\"></script>
  <script>
  const DATA = {data_json};
  const DEFAULT_FUNCTION_IO = {function_io_json};

  const state = {{
    toggles: {{ showDataFlow: true, showExecution: false, showCallGraph: false }},
    selectedNodeId: null,
    selectedEdgeKey: null,
    viewTransform: {{ x: 0, y: 0, k: 1 }},
    initialTransform: {{ x: 0, y: 0, k: 1 }},
    anomalies: [],
    activeTab: "diagram",
    sequenceZoom: 1,
    sequenceMap: {{}},
    sequenceGroupMap: {{}},
    functionIO: {{}},
    functionIOSource: "none"
  }};

  function addAnomaly(kind, message, details) {{
    state.anomalies.push({{ kind, message, details }});
  }}

  function updateIOSourceStatus(state) {{
    const el = document.getElementById("ioStatus");
    if (!el) return;
    el.textContent = `IO: ${{state.functionIOSource || "none"}}`;
  }}

  function applySequenceZoom(state) {{
    const content = document.getElementById("sequence-content");
    if (!content) return;
    const z = Math.max(0.5, Math.min(2.5, state.sequenceZoom || 1));
    state.sequenceZoom = z;
    content.style.transform = `scale(${{z}})`;
  }}

  function parseData(raw) {{
    const data = Object.assign({{}}, raw);
    data.blocks = Array.isArray(data.blocks) ? data.blocks : [];
    data.signals = Array.isArray(data.signals) ? data.signals : [];
    data.flow = data.flow || {{}};
    data.flow.execution_order = Array.isArray(data.flow.execution_order) ? data.flow.execution_order : [];
    data.flow.call_graph = data.flow.call_graph || {{}};
    return data;
  }}

  function computeNodeSize(node) {{
    const baseWidth = 180;
    const charWidth = 7;
    const lineHeight = 16;
    const lines = [node.title].concat(node.bodyLines);
    const maxLen = Math.max.apply(null, lines.map(line => line.length));
    const width = Math.max(baseWidth, Math.min(360, maxLen * charWidth + 40));
    const height = 20 + lines.length * lineHeight + 12;
    return {{ width, height }};
  }}

  function buildNodes(data) {{
    return data.blocks.map(block => {{
      const inputs = Array.isArray(block.inputs) ? block.inputs.join(", ") : "";
      const outputs = Array.isArray(block.outputs) ? block.outputs.join(", ") : "";
      const cycles = block.estimated_cycles != null ? String(block.estimated_cycles) : "n/a";
      const badges = [];
      if (block.cfg && block.cfg.has_branches) badges.push("⚡ branch");
      if (block.cfg && Array.isArray(block.cfg.loops) && block.cfg.loops.length) badges.push("🔁 loop");
      const blockId = block.block_id || "unknown";
      const blockName = block.block_name || "unnamed";
      const node = {{
        id: blockId,
        title: `${{blockName}} (${{blockId}})`,
        bodyLines: [
          `inputs: ${{inputs || "-"}}`,
          `outputs: ${{outputs || "-"}}`,
          `cycles: ${{cycles}}`
        ].concat(badges.length ? [`badges: ${{badges.join(", ")}}`] : []),
        data: block
      }};
      const size = computeNodeSize(node);
      return {{
        id: node.id,
        width: size.width,
        height: size.height,
        data: node
      }};
    }});
  }}

  function mergeParallelEdges(edges) {{
    const map = new Map();
    edges.forEach(edge => {{
      const key = `${{edge.source_id}}::${{edge.destination_id}}`;
      if (!map.has(key)) {{
        map.set(key, {{
          source_id: edge.source_id,
          destination_id: edge.destination_id,
          labels_merged: [],
          original_signals: []
        }});
      }}
      const item = map.get(key);
      item.labels_merged.push(edge.label);
      item.original_signals.push(edge.original);
    }});
    return Array.from(map.values()).map(item => {{
      const labelCount = item.labels_merged.length;
      const label_display = labelCount > 4 ? `${{labelCount}} labels` : item.labels_merged.join(", ");
      return Object.assign(item, {{ label_display }});
    }});
  }}

  function buildEdges(data, toggles) {{
    const blockIds = new Set(data.blocks.map(block => block.block_id));
    const dataEdgesRaw = [];

    if (toggles.showDataFlow) {{
      data.signals.forEach(signal => {{
        if (signal.source_type !== "block" || signal.destination_type !== "block") return;
        if (!blockIds.has(signal.source_id) || !blockIds.has(signal.destination_id)) {{
          addAnomaly("warn", "Signal references missing block", signal);
          return;
        }}
        dataEdgesRaw.push({{
          source_id: signal.source_id,
          destination_id: signal.destination_id,
          label: signal.signal_name || "signal",
          original: signal
        }});
      }});
    }}

    const mergedDataEdges = mergeParallelEdges(dataEdgesRaw).map((edge, index) => ({{
      id: `data_${{index}}_${{edge.source_id}}_${{edge.destination_id}}`,
      sources: [edge.source_id],
      targets: [edge.destination_id],
      labels: [{{ text: edge.label_display }}],
      data: Object.assign({{ type: "data" }}, edge)
    }}));

    const execEdges = [];
    if (toggles.showExecution && Array.isArray(data.flow.execution_order)) {{
      const order = data.flow.execution_order;
      for (let i = 0; i < order.length - 1; i += 1) {{
        const from = order[i];
        const to = order[i + 1];
        if (!blockIds.has(from) || !blockIds.has(to)) {{
          addAnomaly("warn", "execution_order references missing block", {{ from, to }});
          continue;
        }}
        execEdges.push({{
          id: `exec_${{i}}_${{from}}_${{to}}`,
          sources: [from],
          targets: [to],
          labels: [{{ text: "exec" }}],
          data: {{ type: "exec", from, to }}
        }});
      }}
    }}

    const callEdges = [];
    if (toggles.showCallGraph && data.flow.call_graph && data.flow.call_graph.nodes) {{
      const nodes = data.flow.call_graph.nodes;
      const functionToBlock = {{}};
      Object.keys(nodes).forEach(name => {{
        if (nodes[name] && nodes[name].block_id) functionToBlock[name] = nodes[name].block_id;
      }});

      Object.keys(nodes).forEach(name => {{
        const callerBlock = functionToBlock[name];
        if (!callerBlock) return;
        const callees = Array.isArray(nodes[name].callees) ? nodes[name].callees : [];
        callees.forEach(calleeName => {{
          const calleeBlock = functionToBlock[calleeName];
          if (!calleeBlock) {{
            addAnomaly("warn", "call_graph mapping failed", {{ caller: name, callee: calleeName }});
            return;
          }}
          if (!blockIds.has(callerBlock) || !blockIds.has(calleeBlock)) {{
            addAnomaly("warn", "call_graph references missing block", {{ callerBlock, calleeBlock }});
            return;
          }}
          callEdges.push({{
            id: `call_${{name}}_${{calleeName}}`,
            sources: [callerBlock],
            targets: [calleeBlock],
            labels: [{{ text: "call" }}],
            data: {{ type: "call", caller: name, callee: calleeName }}
          }});
        }});
      }});
    }}

    return mergedDataEdges.concat(execEdges, callEdges);
  }}

  function layoutWithELK(nodes, edges, elkOptions) {{
    const elk = new ELK();
    const graph = {{
      id: "root",
      layoutOptions: elkOptions,
      children: nodes,
      edges: edges
    }};
    return elk.layout(graph);
  }}

  function renderSVG(layout, svgEl) {{
    const nodes = layout.children || [];
    const edges = layout.edges || [];

    let minX = 0;
    let minY = 0;
    let maxX = 0;
    let maxY = 0;

    function trackPoint(x, y) {{
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }}

    nodes.forEach(node => {{
      trackPoint(node.x, node.y);
      trackPoint(node.x + node.width, node.y + node.height);
    }});

    edges.forEach(edge => {{
      (edge.sections || []).forEach(section => {{
        trackPoint(section.startPoint.x, section.startPoint.y);
        (section.bendPoints || []).forEach(bp => trackPoint(bp.x, bp.y));
        trackPoint(section.endPoint.x, section.endPoint.y);
      }});
    }});

    const padding = 40;
    const viewBox = [
      minX - padding,
      minY - padding,
      (maxX - minX) + padding * 2,
      (maxY - minY) + padding * 2
    ].join(" ");

    const edgeParts = edges.map(edge => {{
      const sections = edge.sections || [];
      if (!sections.length) return "";
      const section = sections[0];
      const points = [section.startPoint]
        .concat(section.bendPoints || [])
        .concat([section.endPoint]);
      const polyline = points.map(p => `${{p.x}},${{p.y}}`).join(" ");
      const edgeType = edge.data && edge.data.type ? edge.data.type : "data";
      const className = edgeType === "exec" ? "edge-line exec" : edgeType === "call" ? "edge-line call" : "edge-line";
      const label = edge.labels && edge.labels[0] ? edge.labels[0].text : "";
      const midPoint = points[Math.floor(points.length / 2)];
      return `
        <g class=\"edge-group\" data-edge-id=\"${{edge.id}}\">
          <polyline class=\"${{className}}\" points=\"${{polyline}}\" />
          <text class=\"edge-label\" x=\"${{midPoint.x + 6}}\" y=\"${{midPoint.y - 6}}\">${{label}}</text>
        </g>`;
      // TODO: add edge label toggle if needed.
    }}).join("");

    const nodeParts = nodes.map(node => {{
      const data = node.data || {{}};
      const lines = [data.title].concat(data.bodyLines || []);
      const textLines = lines.map((line, idx) => {{
        const y = node.y + 24 + idx * 16;
        const className = idx === 0 ? "node-title" : "node-line";
        return `<text class=\"${{className}}\" x=\"${{node.x + 12}}\" y=\"${{y}}\">${{line}}</text>`;
      }}).join("");
      const loopClass = data.data && data.data.cfg && Array.isArray(data.data.cfg.loops) && data.data.cfg.loops.length ? "loop" : "";
      return `
        <g class=\"node-group\" data-node-id=\"${{node.id}}\">
          <rect class=\"node-box ${{loopClass}}\" x=\"${{node.x}}\" y=\"${{node.y}}\" width=\"${{node.width}}\" height=\"${{node.height}}\"></rect>
          ${{textLines}}
        </g>`;
    }}).join("");

    svgEl.setAttribute("viewBox", viewBox);
    svgEl.innerHTML = `
      <g id=\"viewport\" transform=\"translate(${{state.viewTransform.x}}, ${{state.viewTransform.y}}) scale(${{state.viewTransform.k}})\">
        ${{edgeParts}}
        ${{nodeParts}}
      </g>`;
  }}

  function applySearchHighlight(state, query) {{
    const svg = document.getElementById("diagramSvg");
    const nodeGroups = svg.querySelectorAll(".node-group");
    const q = query.trim().toLowerCase();
    nodeGroups.forEach(group => {{
      const id = group.getAttribute("data-node-id");
      const block = state.blockMap[id];
      const name = block && block.block_name ? block.block_name.toLowerCase() : "";
      if (!q) {{
        group.classList.remove("highlight");
      }} else if ((id && id.toLowerCase().includes(q)) || name.includes(q)) {{
        group.classList.add("highlight");
      }} else {{
        group.classList.remove("highlight");
      }}
    }});
  }}

  function bindUI(state) {{
    const svg = document.getElementById("diagramSvg");
    const detailPanel = document.getElementById("detail-json");
    const searchInput = document.getElementById("searchInput");
    const toggleData = document.getElementById("toggleData");
    const toggleExec = document.getElementById("toggleExec");
    const toggleCall = document.getElementById("toggleCall");
    const resetBtn = document.getElementById("resetBtn");
    const tabDiagram = document.getElementById("tabDiagram");
    const tabSequence = document.getElementById("tabSequence");
    const seqZoomOut = document.getElementById("seqZoomOut");
    const seqZoomReset = document.getElementById("seqZoomReset");
    const seqZoomIn = document.getElementById("seqZoomIn");
    const diagramPanel = document.getElementById("diagram-panel");
    const sequencePanel = document.getElementById("sequence-panel");
    const seqLoad = document.getElementById("seqLoad");
    const seqExport = document.getElementById("seqExport");
    const seqFile = document.getElementById("seqFile");
    const ioLoad = document.getElementById("ioLoad");
    const ioFile = document.getElementById("ioFile");

    searchInput.addEventListener("input", event => {{
      applySearchHighlight(state, event.target.value);
    }});

    function rerender() {{
      state.anomalies = [];
      const edges = buildEdges(state.data, state.toggles);
      layoutWithELK(state.nodes, edges, state.elkOptions).then(layout => {{
        state.layout = layout;
        renderSVG(layout, svg);
        applySearchHighlight(state, searchInput.value);
        updateAnomalies(state);
      }});
    }}

    toggleData.addEventListener("change", () => {{
      state.toggles.showDataFlow = toggleData.checked;
      rerender();
    }});
    toggleExec.addEventListener("change", () => {{
      state.toggles.showExecution = toggleExec.checked;
      rerender();
    }});
    toggleCall.addEventListener("change", () => {{
      state.toggles.showCallGraph = toggleCall.checked;
      rerender();
    }});

    resetBtn.addEventListener("click", () => {{
      state.viewTransform = Object.assign({{}}, state.initialTransform);
      if (state.layout) renderSVG(state.layout, svg);
    }});

    function setTab(next) {{
      state.activeTab = next;
      if (next === "diagram") {{
        diagramPanel.hidden = false;
        sequencePanel.hidden = true;
        tabDiagram.classList.add("active");
        tabSequence.classList.remove("active");
      }} else {{
        diagramPanel.hidden = true;
        sequencePanel.hidden = false;
        tabSequence.classList.add("active");
        tabDiagram.classList.remove("active");
        requestAnimationFrame(() => {{
          const board = document.querySelector("#sequence-content .seq-board");
          if (board) drawSequenceGroupEdges(board, state.data);
        }});
      }}
    }}

    tabDiagram.addEventListener("click", () => setTab("diagram"));
    tabSequence.addEventListener("click", () => setTab("sequence"));

    seqZoomOut.addEventListener("click", () => {{
      state.sequenceZoom = (state.sequenceZoom || 1) / 1.15;
      applySequenceZoom(state);
      const board = document.querySelector("#sequence-content .seq-board");
      if (board) requestAnimationFrame(() => drawSequenceGroupEdges(board, state.data));
    }});
    seqZoomReset.addEventListener("click", () => {{
      state.sequenceZoom = 1;
      applySequenceZoom(state);
      const board = document.querySelector("#sequence-content .seq-board");
      if (board) requestAnimationFrame(() => drawSequenceGroupEdges(board, state.data));
    }});
    seqZoomIn.addEventListener("click", () => {{
      state.sequenceZoom = (state.sequenceZoom || 1) * 1.15;
      applySequenceZoom(state);
      const board = document.querySelector("#sequence-content .seq-board");
      if (board) requestAnimationFrame(() => drawSequenceGroupEdges(board, state.data));
    }});

    sequencePanel.addEventListener("wheel", event => {{
      if (!event.ctrlKey) return;
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.1 : 0.9;
      state.sequenceZoom = (state.sequenceZoom || 1) * factor;
      applySequenceZoom(state);
      const board = document.querySelector("#sequence-content .seq-board");
      if (board) requestAnimationFrame(() => drawSequenceGroupEdges(board, state.data));
    }}, {{ passive: false }});

    window.addEventListener("resize", () => {{
      const board = document.querySelector("#sequence-content .seq-board");
      if (board) requestAnimationFrame(() => drawSequenceGroupEdges(board, state.data));
    }});

    seqLoad.addEventListener("click", () => seqFile.click());
    seqFile.addEventListener("change", event => {{
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {{
        try {{
          const data = JSON.parse(reader.result);
          state.sequenceMap = (data && data.nodes) ? data.nodes : {{}};
          state.sequenceGroupMap = (data && data.groups) ? data.groups : {{}};
          renderSequence(state);
        }} catch (err) {{
          alert("Failed to load map: " + err);
        }}
      }};
      reader.readAsText(file);
      seqFile.value = "";
    }});

    seqExport.addEventListener("click", () => {{
      const payload = JSON.stringify({{
        version: 2,
        nodes: state.sequenceMap,
        groups: state.sequenceGroupMap
      }}, null, 2);
      const blob = new Blob([payload], {{ type: "application/json" }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "custom_map.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }});

    ioLoad.addEventListener("click", () => ioFile.click());
    ioFile.addEventListener("change", event => {{
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {{
        try {{
          const data = JSON.parse(reader.result);
          state.functionIO = data || {{}};
          state.functionIOSource = "loaded from file";
          updateIOSourceStatus(state);
          renderSequence(state);
        }} catch (err) {{
          alert("Failed to load IO map: " + err);
        }}
      }};
      reader.readAsText(file);
      ioFile.value = "";
    }});

    let isPanning = false;
    let start = {{ x: 0, y: 0 }};

    svg.addEventListener("mousedown", event => {{
      if (event.button !== 0) return;
      isPanning = true;
      start = {{ x: event.clientX - state.viewTransform.x, y: event.clientY - state.viewTransform.y }};
    }});
    window.addEventListener("mousemove", event => {{
      if (!isPanning) return;
      state.viewTransform.x = event.clientX - start.x;
      state.viewTransform.y = event.clientY - start.y;
      if (state.layout) renderSVG(state.layout, svg);
    }});
    window.addEventListener("mouseup", () => {{
      isPanning = false;
    }});

    svg.addEventListener("wheel", event => {{
      event.preventDefault();
      const delta = -event.deltaY;
      const zoomFactor = delta > 0 ? 1.1 : 0.9;
      const next = Math.min(3.5, Math.max(0.25, state.viewTransform.k * zoomFactor));
      state.viewTransform.k = next;
      if (state.layout) renderSVG(state.layout, svg);
    }}, {{ passive: false }});

    svg.addEventListener("click", event => {{
      const nodeGroup = event.target.closest(".node-group");
      const edgeGroup = event.target.closest(".edge-group");
      if (nodeGroup) {{
        const nodeId = nodeGroup.getAttribute("data-node-id");
        state.selectedNodeId = nodeId;
        state.selectedEdgeKey = null;
        const block = state.blockMap[nodeId];
        detailPanel.textContent = JSON.stringify(block || {{ error: "Node not found" }}, null, 2);
        return;
      }}
      if (edgeGroup) {{
        const edgeId = edgeGroup.getAttribute("data-edge-id");
        state.selectedEdgeKey = edgeId;
        state.selectedNodeId = null;
        const edge = (state.layout.edges || []).find(item => item.id === edgeId);
        detailPanel.textContent = JSON.stringify(edge && edge.data ? edge.data : {{ error: "Edge not found" }}, null, 2);
      }}
    }});

    rerender();
  }}

  function updateAnomalies(state) {{
    const panel = document.getElementById("anomaly-list");
    const list = document.getElementById("anomaly-items");
    list.innerHTML = "";
    if (!state.anomalies.length) {{
      panel.hidden = true;
      return;
    }}
    state.anomalies.forEach(anomaly => {{
      const li = document.createElement("li");
      li.textContent = `${{anomaly.kind.toUpperCase()}}: ${{anomaly.message}}`;
      list.appendChild(li);
    }});
    panel.hidden = false;
  }}

  function init() {{
    const data = parseData(DATA);
    const nodes = buildNodes(data);
    const blockMap = {{}};
    data.blocks.forEach(block => {{
      blockMap[block.block_id] = block;
    }});

    const elkOptions = {{
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "50",
      "elk.layered.spacing.nodeNodeBetweenLayers": "80",
      "elk.layered.spacing.edgeNodeBetweenLayers": "40"
    }};

    state.data = data;
    state.nodes = nodes;
    state.blockMap = blockMap;
    state.elkOptions = elkOptions;
    state.functionIO = (DEFAULT_FUNCTION_IO && typeof DEFAULT_FUNCTION_IO === "object") ? DEFAULT_FUNCTION_IO : {{}};
    state.functionIOSource = Object.keys(state.functionIO).length ? "embedded" : "none";
    updateIOSourceStatus(state);

    renderSequence(state);
    bindUI(state);
    // Optional fetch-based override for workflows that keep function_io.json outside the embedded build.
    autoLoadFunctionIO(state);
  }}

  async function autoLoadFunctionIO(state) {{
    const candidates = ["./function_io.json", "../function_io.json"];
    for (const path of candidates) {{
      try {{
        const resp = await fetch(path, {{ cache: "no-store" }});
        if (!resp.ok) continue;
        const data = await resp.json();
        if (data && typeof data === "object") {{
          state.functionIO = data;
          state.functionIOSource = `auto-loaded (${{path}})`;
          updateIOSourceStatus(state);
          renderSequence(state);
          return;
        }}
      }} catch (err) {{
        // Ignore and try next candidate.
      }}
    }}
  }}

  function detectSchemaVersion(data) {{
    if (data && typeof data.schema_version === "string") return data.schema_version;
    if (data && data.schema && typeof data.schema.version === "string") return data.schema.version;
    return "2.x";
  }}

  function normalizeFunctionIOMap(value) {{
    if (!value || typeof value !== "object") return {{}};
    if (value.functions && typeof value.functions === "object") return value.functions;
    return value;
  }}

  function getFunctionIOMap(state) {{
    const flowIO = state.data && state.data.flow ? state.data.flow.function_io : null;
    const flowMap = normalizeFunctionIOMap(flowIO);
    if (Object.keys(flowMap).length) return flowMap;
    return normalizeFunctionIOMap(state.functionIO);
  }}

  function renderSequence(state) {{
    const flow = state.data && state.data.flow ? state.data.flow : {{}};
    const timeline = Array.isArray(flow.sequence_timeline) ? flow.sequence_timeline : [];
    const schemaVersion = detectSchemaVersion(state.data);
    if (timeline.length) {{
      renderSequenceTimelineV3(state, timeline, schemaVersion);
      return;
    }}
    renderLegacySequence(state, schemaVersion);
  }}

  function appendTimelineText(parent, className, text) {{
    const el = document.createElement("div");
    el.className = className;
    el.textContent = text;
    parent.appendChild(el);
    return el;
  }}

  function appendTimelinePill(parent, text) {{
    const pill = document.createElement("span");
    pill.className = "timeline-pill";
    pill.textContent = text;
    parent.appendChild(pill);
    return pill;
  }}

  function makeIdMap(items, key) {{
    const map = new Map();
    if (!Array.isArray(items)) return map;
    items.forEach(item => {{
      if (item && item[key] != null) map.set(item[key], item);
    }});
    return map;
  }}

  function renderTimelineList(parent, title, items, renderItem) {{
    const section = document.createElement("div");
    section.className = "timeline-section";
    appendTimelineText(section, "timeline-section-title", title);
    if (!items.length) {{
      appendTimelineText(section, "timeline-empty", "none");
    }} else {{
      const list = document.createElement("ul");
      list.className = "timeline-list";
      items.forEach(item => {{
        const li = document.createElement("li");
        li.className = "timeline-item";
        renderItem(li, item);
        list.appendChild(li);
      }});
      section.appendChild(list);
    }}
    parent.appendChild(section);
  }}

  function describeCall(call) {{
    if (!call) return "missing call";
    const callId = call.call_id || "unknown";
    const caller = call.caller_function || call.caller_block_id || "?";
    const callee = call.callee_function || call.callee_block_id || "?";
    const args = Array.isArray(call.args) ? call.args.map(arg => arg.expr || "?").join(", ") : "";
    const assigned = call.assigned && call.assigned.target ? ` -> ${{call.assigned.target}}` : "";
    return `${{callId}}: ${{caller}} -> ${{callee}}(${{args}})${{assigned}}`;
  }}

  function describeSignal(signal) {{
    if (!signal) return "missing signal";
    const signalId = signal.signal_id || "unknown";
    const callId = signal.call_id ? ` call_id=${{signal.call_id}}` : "";
    const kind = signal.kind || signal.comment || "signal";
    const expr = signal.expr || signal.signal_name || "";
    const param = signal.param ? ` param=${{signal.param}}` : "";
    return `${{signalId}}: ${{kind}} ${{expr}}${{param}}${{callId}}`;
  }}

  function asStringList(value) {{
    if (!Array.isArray(value)) return [];
    return value.filter(item => item != null).map(item => String(item));
  }}

  function callArgExprByParam(call) {{
    const map = new Map();
    const args = call && Array.isArray(call.args) ? call.args : [];
    args.forEach((arg, index) => {{
      if (arg && typeof arg === "object") {{
        if (arg.param != null && arg.expr != null) map.set(String(arg.param), String(arg.expr));
      }} else if (arg != null && Array.isArray(call.callee_params) && index < call.callee_params.length) {{
        map.set(String(call.callee_params[index]), String(arg));
      }}
    }});
    return map;
  }}

  function mapCallIONames(names, call) {{
    if (!call) return names;
    const exprByParam = callArgExprByParam(call);
    return names.map(name => exprByParam.has(name) ? exprByParam.get(name) : name);
  }}

  function timelineIOItem(functionIOMap, functionName, role, call) {{
    if (!functionName || !functionIOMap || !functionIOMap[functionName]) return null;
    const io = functionIOMap[functionName];
    if (!io || typeof io !== "object") return null;
    const contractReads = asStringList(io.reads);
    const contractWrites = asStringList(io.writes);
    const item = {{
      role,
      function: functionName,
      reads: mapCallIONames(contractReads, call),
      writes: mapCallIONames(contractWrites, call),
      contract_reads: contractReads,
      contract_writes: contractWrites
    }};
    if (call) {{
      item.call_id = call.call_id || "";
      item.caller_function = call.caller_function || "";
      item.callee_function = call.callee_function || "";
      if (call.assigned && call.assigned.target != null) item.assigned = String(call.assigned.target);
    }}
    if (io.provenance && typeof io.provenance === "object") item.provenance = io.provenance;
    return item;
  }}

  function summarizeTimelineFunctionIO(state, step, functionIOMap, callById) {{
    const summary = [];
    if (!step || !functionIOMap || !Object.keys(functionIOMap).length) return summary;
    const stepItem = timelineIOItem(functionIOMap, step.function, "step_function", null);
    if (stepItem) summary.push(stepItem);
    (step.call_ids_as_caller || []).forEach(callId => {{
      const call = callById.get(callId);
      if (!call) return;
      const item = timelineIOItem(functionIOMap, call.callee_function, "called_function", call);
      if (item) summary.push(item);
    }});
    (step.call_ids_as_callee || []).forEach(callId => {{
      const call = callById.get(callId);
      if (!call) return;
      const item = timelineIOItem(functionIOMap, call.caller_function, "caller_function", call);
      if (item) summary.push(item);
    }});
    return summary;
  }}

  function describeTimelineIO(item) {{
    const callId = item.call_id ? ` ${{item.call_id}}` : "";
    const reads = item.reads && item.reads.length ? item.reads.join(", ") : "-";
    const writes = item.writes && item.writes.length ? item.writes.join(", ") : "-";
    const assigned = item.assigned ? ` return->${{item.assigned}}` : "";
    const provenance = item.provenance && item.provenance.source ? ` source=${{item.provenance.source}}` : "";
    return `${{item.role}}${{callId}} ${{item.function}} reads: ${{reads}}; writes: ${{writes}}${{assigned}}${{provenance}}`;
  }}

  function renderSequenceTimelineV3(state, timeline, schemaVersion) {{
    const container = document.getElementById("sequence-content");
    const flow = state.data.flow || {{}};
    const callById = makeIdMap(flow.call_instances || [], "call_id");
    const signalById = makeIdMap(state.data.signals || [], "signal_id");
    const functionIOMap = getFunctionIOMap(state);

    container.innerHTML = "";
    const shell = document.createElement("div");
    shell.className = "timeline-shell";
    shell.dataset.schemaVersion = schemaVersion;

    const banner = document.createElement("div");
    banner.className = "timeline-banner";
    const metaKind = flow.execution_order_meta && flow.execution_order_meta.kind ? flow.execution_order_meta.kind : "static_block_order";
    banner.textContent = `Schema ${{schemaVersion}} sequence_timeline: one card per execution_order block. Order kind: ${{metaKind}}. v2 call_sequence remains available as a fallback.`;
    shell.appendChild(banner);

    timeline.forEach(step => {{
      const card = document.createElement("article");
      card.className = "timeline-card";
      card.dataset.stepId = step.step_id || "";
      card.dataset.blockId = step.block_id || "";
      card.dataset.function = step.function || "";

      const header = document.createElement("div");
      header.className = "timeline-card-header";
      const titleWrap = document.createElement("div");
      appendTimelineText(titleWrap, "timeline-title", `${{step.order_index}}. ${{step.function || "unknown"}}`);
      appendTimelineText(titleWrap, "timeline-subtitle", `${{step.block_id || "unknown block"}} · ${{step.step_id || "no step_id"}}`);
      header.appendChild(titleWrap);

      const badges = document.createElement("div");
      badges.className = "timeline-badges";
      appendTimelinePill(badges, `caller calls: ${{(step.call_ids_as_caller || []).length}}`);
      appendTimelinePill(badges, `callee calls: ${{(step.call_ids_as_callee || []).length}}`);
      appendTimelinePill(badges, `incoming signal_id: ${{(step.incoming_signal_ids || []).length}}`);
      appendTimelinePill(badges, `outgoing signal_id: ${{(step.outgoing_signal_ids || []).length}}`);
      header.appendChild(badges);
      card.appendChild(header);

      const grid = document.createElement("div");
      grid.className = "timeline-grid";

      renderTimelineList(grid, "Call IDs as caller", step.call_ids_as_caller || [], (li, callId) => {{
        li.classList.add("timeline-mono");
        li.textContent = describeCall(callById.get(callId));
      }});
      renderTimelineList(grid, "Call IDs as callee", step.call_ids_as_callee || [], (li, callId) => {{
        li.classList.add("timeline-mono");
        li.textContent = describeCall(callById.get(callId));
      }});
      renderTimelineList(grid, "Incoming signal_id", step.incoming_signal_ids || [], (li, signalId) => {{
        li.classList.add("timeline-mono");
        li.textContent = describeSignal(signalById.get(signalId));
      }});
      renderTimelineList(grid, "Outgoing signal_id", step.outgoing_signal_ids || [], (li, signalId) => {{
        li.classList.add("timeline-mono");
        li.textContent = describeSignal(signalById.get(signalId));
      }});
      renderTimelineList(
        grid,
        "Function IO reads/writes",
        summarizeTimelineFunctionIO(state, step, functionIOMap, callById),
        (li, item) => {{
          li.classList.add("timeline-mono");
          li.textContent = describeTimelineIO(item);
        }}
      );

      card.appendChild(grid);
      shell.appendChild(card);
    }});

    container.appendChild(shell);
    applySequenceZoom(state);
  }}

  function renderLegacySequence(state, schemaVersion) {{
    const container = document.getElementById("sequence-content");
    const seq = (state.data.flow && state.data.flow.call_sequence) ? state.data.flow.call_sequence : [];
    if (!seq.length) {{
      container.textContent = "No call sequence data available.";
      return;
    }}

    container.innerHTML = "";
    const board = document.createElement("div");
    board.className = "seq-board";

    const groupByName = new Map();
    seq.forEach(group => groupByName.set(group.function, group));

    const order = buildFunctionOrder(state.data, seq);
    order.forEach(name => {{
      const group = groupByName.get(name);
      if (!group) return;
      const calls = group.calls || [];
      const groupEl = document.createElement("div");
      groupEl.className = "seq-group";
      groupEl.dataset.function = group.function;
      applyGroupTransform(groupEl, state, group.function);
      const title = document.createElement("div");
      title.className = "seq-title";
      title.textContent = group.function;
      groupEl.appendChild(title);

      const canvas = document.createElement("div");
      canvas.className = "seq-canvas";
      canvas.dataset.function = group.function;

      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "seq-svg");
      svg.innerHTML = `
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 Z" fill="#9ca3af"></path>
          </marker>
        </defs>
      `;
      canvas.appendChild(svg);

      const layout = buildSequenceLayout(group.function, calls, getFunctionIOMap(state));
      const nodes = layout.nodes;
      const edges = layout.edges;
      canvas._seqEdges = edges;

      // Compute canvas size
      const width = Math.max(220, (layout.maxLayer + 1) * 150 + 40);
      const height = Math.max(64, (layout.maxRow + 1) * 58 + 28);
      canvas.style.width = width + "px";
      canvas.style.height = height + "px";
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(height));

      nodes.forEach(node => {{
        const el = document.createElement("div");
        el.className = "seq-node";
        el.dataset.nodeId = node.id;
        el.dataset.function = group.function;
        el.textContent = node.label;
        el.style.left = node.x + "px";
        el.style.top = node.y + "px";
        attachDrag(el, svg, edges, state);
        canvas.appendChild(el);
      }});

      drawSequenceEdges(svg, edges);
      groupEl.appendChild(canvas);
      board.appendChild(groupEl);
      attachGroupDrag(groupEl, board, state);
    }});

    // Append any remaining functions not covered by the call graph order
    seq.forEach(group => {{
      if (order.indexOf(group.function) !== -1) return;
      const calls = group.calls || [];
      const groupEl = document.createElement("div");
      groupEl.className = "seq-group";
      groupEl.dataset.function = group.function;
      applyGroupTransform(groupEl, state, group.function);
      const title = document.createElement("div");
      title.className = "seq-title";
      title.textContent = group.function;
      groupEl.appendChild(title);

      const canvas = document.createElement("div");
      canvas.className = "seq-canvas";
      canvas.dataset.function = group.function;

      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "seq-svg");
      svg.innerHTML = `
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto">
            <path d="M0,0 L8,3 L0,6 Z" fill="#9ca3af"></path>
          </marker>
        </defs>
      `;
      canvas.appendChild(svg);

      const layout = buildSequenceLayout(group.function, calls, getFunctionIOMap(state));
      const nodes = layout.nodes;
      const edges = layout.edges;
      canvas._seqEdges = edges;

      const width = Math.max(220, (layout.maxLayer + 1) * 150 + 40);
      const height = Math.max(64, (layout.maxRow + 1) * 58 + 28);
      canvas.style.width = width + "px";
      canvas.style.height = height + "px";
      svg.setAttribute("width", String(width));
      svg.setAttribute("height", String(height));

      nodes.forEach(node => {{
        const el = document.createElement("div");
        el.className = "seq-node";
        el.dataset.nodeId = node.id;
        el.dataset.function = group.function;
        el.textContent = node.label;
        el.style.left = node.x + "px";
        el.style.top = node.y + "px";
        attachDrag(el, svg, edges, state);
        canvas.appendChild(el);
      }});

      drawSequenceEdges(svg, edges);
      groupEl.appendChild(canvas);
      board.appendChild(groupEl);
      attachGroupDrag(groupEl, board, state);
    }});

    container.appendChild(board);
    applySequenceZoom(state);
    requestAnimationFrame(() => {{
      board.querySelectorAll(".seq-svg").forEach(svg => {{
        const canvas = svg.parentElement;
        const edges = canvas && canvas._seqEdges ? canvas._seqEdges : [];
        drawSequenceEdges(svg, edges);
      }});
      drawSequenceGroupEdges(board, state.data);
    }});
  }}

  function buildFunctionOrder(data, seq) {{
    const order = [];
    const visited = new Set();
    const callGraph = data.flow && data.flow.call_graph ? data.flow.call_graph : null;
    const entry = callGraph && callGraph.entry_functions ? callGraph.entry_functions : [];
    const seqMap = new Map();
    seq.forEach(group => seqMap.set(group.function, group));

    function dfs(funcName) {{
      if (visited.has(funcName)) return;
      visited.add(funcName);
      order.push(funcName);
      const group = seqMap.get(funcName);
      if (!group || !group.calls) return;
      group.calls.forEach(call => dfs(call.callee));
    }}

    entry.forEach(name => dfs(name));
    seq.forEach(group => {{
      if (!visited.has(group.function)) {{
        order.push(group.function);
        visited.add(group.function);
      }}
    }});
    return order;
  }}

  function drawSequenceGroupEdges(board, data) {{
    const old = board.querySelector(".seq-board-overlay");
    if (old) old.remove();

    const groupEls = Array.from(board.querySelectorAll(".seq-group"));
    if (!groupEls.length) return;

    const overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    overlay.setAttribute("class", "seq-board-overlay");
    overlay.setAttribute("width", String(Math.ceil(board.scrollWidth || board.clientWidth || 0)));
    overlay.setAttribute("height", String(Math.ceil(board.scrollHeight || board.clientHeight || 0)));
    overlay.innerHTML = `
      <defs>
        <marker id="group-arrowhead" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto">
          <path d="M0,0 L8,3 L0,6 Z" fill="#94a3b8"></path>
        </marker>
      </defs>
    `;
    board.insertBefore(overlay, board.firstChild);

    const boardRect = board.getBoundingClientRect();
    const groupMap = new Map();
    groupEls.forEach(el => {{
      const fn = el.dataset.function;
      if (!fn) return;
      groupMap.set(fn, el);
    }});

    const callGraph = data && data.flow ? data.flow.call_graph : null;
    const drawn = new Set();
    if (callGraph && callGraph.nodes) {{
      Object.keys(callGraph.nodes).forEach(caller => {{
        const node = callGraph.nodes[caller];
        const callerEl = groupMap.get(caller);
        if (!callerEl || !node || !Array.isArray(node.callees)) return;
        node.callees.forEach(callee => {{
          const calleeEl = groupMap.get(callee);
          if (!calleeEl) return;
          const key = `${{caller}}->${{callee}}`;
          if (drawn.has(key)) return;
          drawn.add(key);
          appendGroupEdgePath(overlay, boardRect, callerEl, calleeEl);
        }});
      }});
    }}

    // Fallback: connect neighbors if call graph data is absent.
    if (!drawn.size && groupEls.length > 1) {{
      for (let i = 0; i < groupEls.length - 1; i += 1) {{
        appendGroupEdgePath(overlay, boardRect, groupEls[i], groupEls[i + 1]);
      }}
    }}
  }}

  function appendGroupEdgePath(svg, boardRect, fromEl, toEl) {{
    const zoom = state.sequenceZoom || 1;
    const fromRect = fromEl.getBoundingClientRect();
    const toRect = toEl.getBoundingClientRect();
    const x1 = ((fromRect.right - boardRect.left) / zoom);
    const y1 = (((fromRect.top + fromRect.height / 2) - boardRect.top) / zoom);
    const x2 = ((toRect.left - boardRect.left) / zoom);
    const y2 = (((toRect.top + toRect.height / 2) - boardRect.top) / zoom);
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", "seq-group-edge");
    path.setAttribute("d", `M${{x1}},${{y1}} C${{x1 + dx}},${{y1}} ${{x2 - dx}},${{y2}} ${{x2}},${{y2}}`);
    svg.appendChild(path);
  }}

  function applyGroupTransform(groupEl, state, functionName) {{
    const saved = state.sequenceGroupMap && state.sequenceGroupMap[functionName];
    const tx = saved && typeof saved.x === "number" ? saved.x : 0;
    const ty = saved && typeof saved.y === "number" ? saved.y : 0;
    groupEl.dataset.tx = String(tx);
    groupEl.dataset.ty = String(ty);
    groupEl.style.transform = `translate(${{tx}}px, ${{ty}}px)`;
    groupEl.style.cursor = "grab";
  }}

  function buildSequenceLayout(functionName, calls, functionIO) {{
    const nodes = [];
    const edges = [];

    const assignInfo = calls.map((call, index) => {{
      return {{
        index,
        callee: call.callee,
        args: call.args || [],
        assigned: call.assigned || null,
        calleeParams: call.callee_params || []
      }};
    }});

    const deps = new Map();
    assignInfo.forEach(call => deps.set(call.index, []));

    const keywords = new Set(["if", "for", "while", "return", "sizeof", "int", "float", "double", "char", "void"]);
    const extractIdentifiers = (text) => {{
      if (!text) return [];
      const matches = text.match(/[A-Za-z_]\\w*/g) || [];
      return matches.filter(token => !keywords.has(token));
    }};
    const toSet = (items) => new Set(items);
    const mergeSets = (a, b) => new Set([...a, ...b]);

    const ioFor = (name) => (functionIO && functionIO[name]) ? functionIO[name] : null;
    const readWriteForCall = (call) => {{
      const io = ioFor(call.callee);
      let reads = [];
      let writes = [];
      if (io && Array.isArray(io.reads) && Array.isArray(io.writes)) {{
        io.reads.forEach(param => {{
          const idx = call.calleeParams.indexOf(param);
          if (idx >= 0 && call.args[idx] != null) {{
            reads = reads.concat(extractIdentifiers(call.args[idx]));
          }}
        }});
        io.writes.forEach(param => {{
          const idx = call.calleeParams.indexOf(param);
          if (idx >= 0 && call.args[idx] != null) {{
            writes = writes.concat(extractIdentifiers(call.args[idx]));
          }}
        }});
      }} else {{
        call.args.forEach(arg => {{
          reads = reads.concat(extractIdentifiers(arg));
        }});
      }}
      if (call.assigned) {{
        writes = writes.concat(extractIdentifiers(call.assigned));
      }}
      return {{ readSet: toSet(reads), writeSet: toSet(writes) }};
    }};

    for (let i = 0; i < assignInfo.length; i += 1) {{
      const src = assignInfo[i];
      for (let j = i + 1; j < assignInfo.length; j += 1) {{
        const dst = assignInfo[j];
        const srcRW = readWriteForCall(src);
        const dstRW = readWriteForCall(dst);
        const raw = intersects(srcRW.writeSet, mergeSets(dstRW.readSet, dstRW.writeSet));
        const war = intersects(srcRW.readSet, dstRW.writeSet);
        if (raw || war) {{
          deps.get(dst.index).push(src.index);
        }}
      }}
    }}

    const layerByIndex = new Map();
    let maxLayer = 0;
    assignInfo.forEach(call => {{
      const incoming = deps.get(call.index) || [];
      let layer = 0;
      if (incoming.length) {{
        layer = Math.max(...incoming.map(idx => layerByIndex.get(idx) || 0)) + 1;
      }}
      layerByIndex.set(call.index, layer);
      maxLayer = Math.max(maxLayer, layer);
    }});

    const rowByLayer = new Map();
    let maxRow = 0;
    assignInfo.forEach(call => {{
      const layer = layerByIndex.get(call.index) || 0;
      const row = rowByLayer.get(layer) || 0;
      rowByLayer.set(layer, row + 1);
      maxRow = Math.max(maxRow, row);

      const id = `${{functionName}}::${{call.index}}::${{call.callee}}`;
      const mapKey = `${{functionName}}::${{id}}`;
      const saved = state.sequenceMap[mapKey];
      const x = saved && typeof saved.x === "number" ? saved.x : (layer * 150 + 26);
      const y = saved && typeof saved.y === "number" ? saved.y : (row * 58 + 18);

      nodes.push({{
        id,
        label: call.callee,
        index: call.index,
        layer,
        row,
        x,
        y
      }});
    }});

    assignInfo.forEach(call => {{
      const incoming = deps.get(call.index) || [];
      incoming.forEach(srcIdx => {{
        edges.push({{
          from: `${{functionName}}::${{srcIdx}}::${{assignInfo[srcIdx].callee}}`,
          to: `${{functionName}}::${{call.index}}::${{call.callee}}`
        }});
      }});
    }});

    return {{
      nodes,
      edges,
      maxLayer,
      maxRow
    }};
  }}

  function intersects(a, b) {{
    for (const item of a) {{
      if (b.has(item)) return true;
    }}
    return false;
  }}

  function drawSequenceEdges(svg, edges) {{
    const canvas = svg.parentElement;
    if (!canvas) return;
    const zoom = state.sequenceZoom || 1;
    const svgRect = svg.getBoundingClientRect();
    const nodeEls = Array.from(canvas.querySelectorAll(".seq-node"));
    const nodeMap = new Map();
    nodeEls.forEach(el => {{
      const r = el.getBoundingClientRect();
      nodeMap.set(el.dataset.nodeId, {{
        x: (r.left - svgRect.left) / zoom,
        y: (r.top - svgRect.top) / zoom,
        w: r.width / zoom,
        h: r.height / zoom
      }});
    }});
    const existing = svg.querySelectorAll("path.seq-edge");
    existing.forEach(el => el.remove());

    edges.forEach(edge => {{
      const from = nodeMap.get(edge.from);
      const to = nodeMap.get(edge.to);
      if (!from || !to) return;
      const x1 = from.x + from.w;
      const y1 = from.y + from.h / 2;
      const x2 = to.x;
      const y2 = to.y + to.h / 2;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const midX = (x1 + x2) / 2;
      path.setAttribute("d", `M${{x1}},${{y1}} C${{midX}},${{y1}} ${{midX}},${{y2}} ${{x2}},${{y2}}`);
      path.setAttribute("class", "seq-edge");
      svg.appendChild(path);
    }});
  }}

  function attachDrag(nodeEl, svg, edges, state) {{
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;

    nodeEl.addEventListener("pointerdown", event => {{
      dragging = true;
      nodeEl.setPointerCapture(event.pointerId);
      startX = event.clientX;
      startY = event.clientY;
      originX = parseFloat(nodeEl.style.left || "0");
      originY = parseFloat(nodeEl.style.top || "0");
      nodeEl.style.cursor = "grabbing";
    }});

    nodeEl.addEventListener("pointermove", event => {{
      if (!dragging) return;
      const zoom = state.sequenceZoom || 1;
      const dx = (event.clientX - startX) / zoom;
      const dy = (event.clientY - startY) / zoom;
      const nextX = originX + dx;
      const nextY = originY + dy;
      nodeEl.style.left = `${{nextX}}px`;
      nodeEl.style.top = `${{nextY}}px`;

      const key = `${{nodeEl.dataset.function}}::${{nodeEl.dataset.nodeId}}`;
      state.sequenceMap[key] = {{ x: nextX, y: nextY }};

      drawSequenceEdges(svg, edges);
    }});

    nodeEl.addEventListener("pointerup", event => {{
      dragging = false;
      nodeEl.releasePointerCapture(event.pointerId);
      nodeEl.style.cursor = "grab";
    }});
  }}

  function attachGroupDrag(groupEl, board, state) {{
    let dragging = false;
    let pointerId = null;
    let startX = 0;
    let startY = 0;
    let originX = 0;
    let originY = 0;

    groupEl.addEventListener("pointerdown", event => {{
      if (event.button !== 0) return;
      if (event.target.closest(".seq-node")) return;
      dragging = true;
      pointerId = event.pointerId;
      groupEl.setPointerCapture(pointerId);
      startX = event.clientX;
      startY = event.clientY;
      originX = parseFloat(groupEl.dataset.tx || "0");
      originY = parseFloat(groupEl.dataset.ty || "0");
      groupEl.style.cursor = "grabbing";
    }});

    groupEl.addEventListener("pointermove", event => {{
      if (!dragging) return;
      const zoom = state.sequenceZoom || 1;
      const tx = originX + (event.clientX - startX) / zoom;
      const ty = originY + (event.clientY - startY) / zoom;
      groupEl.dataset.tx = String(tx);
      groupEl.dataset.ty = String(ty);
      groupEl.style.transform = `translate(${{tx}}px, ${{ty}}px)`;
      const fn = groupEl.dataset.function;
      if (fn) state.sequenceGroupMap[fn] = {{ x: tx, y: ty }};
      drawSequenceGroupEdges(board, state.data);
    }});

    groupEl.addEventListener("pointerup", event => {{
      if (!dragging) return;
      dragging = false;
      if (pointerId != null) groupEl.releasePointerCapture(pointerId);
      pointerId = null;
      groupEl.style.cursor = "grab";
    }});
  }}

  if (typeof ELK === "undefined") {{
    document.getElementById("detail-json").textContent = "ELK.js bundle not loaded. Place ./assets/elk.bundled.js next to this HTML file.";
  }} else {{
    init();
  }}
  </script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert CVAS JSON to a standalone HTML viewer.")
    parser.add_argument("input_json", help="Path to input JSON file")
    parser.add_argument("output_html", help="Path to output HTML file")
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output_html)

    data = load_json(input_path)
    html = build_html(data)

    try:
        output_path.write_text(html, encoding="utf-8")
    except OSError as exc:
        print(f"[json_to_html] Failed to write output HTML: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
