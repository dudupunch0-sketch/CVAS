#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

REQUIRED_FIELDS = ["blocks", "operations", "signals", "flow", "cfg", "call_graph"]


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
  </header>
  <main>
    <section id=\"diagram-panel\">
      <svg id=\"diagramSvg\" xmlns=\"http://www.w3.org/2000/svg\"></svg>
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

  const state = {{
    toggles: {{ showDataFlow: true, showExecution: false, showCallGraph: false }},
    selectedNodeId: null,
    selectedEdgeKey: null,
    viewTransform: {{ x: 0, y: 0, k: 1 }},
    initialTransform: {{ x: 0, y: 0, k: 1 }},
    anomalies: []
  }};

  function addAnomaly(kind, message, details) {{
    state.anomalies.push({{ kind, message, details }});
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
      const blockId = block.block_id || \"unknown\";
      const blockName = block.block_name || \"unnamed\";
      const node = {{
        id: blockId,
        title: `${{blockName}} (${{blockId}})`,
        bodyLines: [
          `inputs: ${inputs || "-"}`,
          `outputs: ${outputs || "-"}`,
          `cycles: ${cycles}`
        ].concat(badges.length ? [`badges: ${badges.join(", ")}`] : []),
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

    const mergedDataEdges = mergeParallelEdges(dataEdgesRaw).map((edge, index) => ({
      id: `data_${index}_${edge.source_id}_${edge.destination_id}`,
      sources: [edge.source_id],
      targets: [edge.destination_id],
      labels: [{ text: edge.label_display }],
      data: Object.assign({ type: "data" }, edge)
    }));

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
          id: `exec_${i}_${from}_${to}`,
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
            id: `call_${name}_${calleeName}`,
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
          ${textLines}
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

    bindUI(state);
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
