"""导出单文件 graph.html：离线可打开、可分享的知识图谱视图。

不依赖前端构建产物与任何 CDN：内联 graph.json 数据 + 一段原生 JS（社区环形布局、
自绘 canvas、搜索、点击看详情）。适合发给同事或在报告里附带。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Settings
from ..graph.store import Store
from .json_export import build_document

#: 内联进 HTML 的节点上限（按度数取 top-N，避免文件过大）
MAX_HTML_NODES = 800
MAX_HTML_EDGES = 3000

_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__ — CogenNav 图谱</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; height: 100vh; display: flex; font: 13px/1.5 ui-sans-serif, system-ui, "PingFang SC", sans-serif;
         background: #09090b; color: #e4e4e7; }
  #side { width: 300px; border-right: 1px solid #27272a; display: flex; flex-direction: column; }
  #side h1 { font-size: 14px; margin: 0; padding: 12px; border-bottom: 1px solid #27272a; }
  #search { margin: 8px; padding: 6px 8px; background: #18181b; border: 1px solid #3f3f46; color: inherit; border-radius: 4px; }
  #list { overflow: auto; flex: 1; padding: 0 8px 8px; }
  .item { padding: 5px 6px; border-radius: 4px; cursor: pointer; font-family: ui-monospace, monospace; font-size: 11px; }
  .item:hover, .item.active { background: #27272a; }
  .kind { color: #a1a1aa; }
  #stage { flex: 1; position: relative; }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  canvas.dragging { cursor: grabbing; }
  #detail { position: absolute; right: 12px; top: 12px; width: 320px; max-height: 80vh; overflow: auto;
            background: #18181bee; border: 1px solid #3f3f46; border-radius: 6px; padding: 12px; display: none; }
  #detail h2 { font-size: 13px; margin: 0 0 6px; }
  #detail pre { white-space: pre-wrap; word-break: break-all; font-size: 11px; color: #a1a1aa; }
  #legend { position: absolute; left: 12px; bottom: 12px; max-width: 55%; font-size: 11px; color: #a1a1aa; }
  .swatch { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; }
  .stats { padding: 8px 12px; border-top: 1px solid #27272a; color: #a1a1aa; font-size: 11px; }
</style>
</head>
<body>
<div id="side">
  <h1>__TITLE__</h1>
  <input id="search" placeholder="搜索符号…" />
  <div id="list"></div>
  <div class="stats" id="stats"></div>
</div>
<div id="stage">
  <canvas id="canvas"></canvas>
  <div id="detail"></div>
  <div id="legend"></div>
</div>
<script id="graph-data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('graph-data').textContent);
const nodes = DATA.nodes, edges = DATA.edges;
const byId = new Map(nodes.map(n => [n.id, n]));
const adjacency = new Map();
for (const e of edges) {
  if (!adjacency.has(e.source)) adjacency.set(e.source, []);
  if (!adjacency.has(e.target)) adjacency.set(e.target, []);
  adjacency.get(e.source).push(e);
  adjacency.get(e.target).push(e);
}
const PALETTE = ['#60a5fa','#f472b6','#34d399','#fbbf24','#a78bfa','#22d3ee','#fb923c','#4ade80',
                 '#f87171','#818cf8','#2dd4bf','#e879f9','#facc15','#38bdf8','#c084fc','#94a3b8'];
const communityIds = [...new Set(nodes.map(n => n.community).filter(c => c !== null))].sort((a,b)=>a-b);
const colorOf = n => n.community === null || n.community === undefined
  ? '#52525b' : PALETTE[communityIds.indexOf(n.community) % PALETTE.length];

// 社区环形布局：社区按数量排布在外圈，社区内节点排成小簇（O(n)，无物理迭代）
const positions = new Map();
const R = Math.max(320, Math.sqrt(nodes.length) * 46);
communityIds.forEach((cid, index) => {
  const members = nodes.filter(n => n.community === cid);
  const angle = (index / communityIds.length) * Math.PI * 2;
  const cx = Math.cos(angle) * R, cy = Math.sin(angle) * R;
  const inner = Math.max(14, Math.sqrt(members.length) * 11);
  members.forEach((n, i) => {
    const a = (i / members.length) * Math.PI * 2;
    const r = inner * Math.sqrt(i / Math.max(1, members.length));
    positions.set(n.id, { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r });
  });
});
const orphan = nodes.filter(n => n.community === null || n.community === undefined);
orphan.forEach((n, i) => positions.set(n.id, { x: (i % 20) * 16 - 160, y: Math.floor(i / 20) * 16 + R + 80 }));

const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
let scale = 1, offsetX = 0, offsetY = 0, selected = null, highlight = new Set();
function resize() {
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  draw();
}
function draw() {
  const w = canvas.width, h = canvas.height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.setTransform(devicePixelRatio * scale, 0, 0, devicePixelRatio * scale,
                   w / 2 + offsetX * devicePixelRatio, h / 2 + offsetY * devicePixelRatio);
  const radius = n => 2.5 + Math.min(9, Math.sqrt(n.degree || 0) * 0.9);
  ctx.lineWidth = 0.6;
  for (const e of edges) {
    const a = positions.get(e.source), b = positions.get(e.target);
    if (!a || !b) continue;
    const active = selected && (e.source === selected || e.target === selected);
    ctx.strokeStyle = active ? '#fafafa' : (e.confidence === 'AMBIGUOUS' ? '#3f3f4666' : '#3f3f4699');
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }
  for (const n of nodes) {
    const p = positions.get(n.id);
    if (!p) continue;
    const dim = highlight.size && !highlight.has(n.id);
    ctx.globalAlpha = dim ? 0.18 : 1;
    ctx.fillStyle = n.id === selected ? '#fafafa' : colorOf(n);
    ctx.beginPath(); ctx.arc(p.x, p.y, radius(n), 0, Math.PI * 2); ctx.fill();
    if (n.id === selected) { ctx.strokeStyle = '#fafafa'; ctx.lineWidth = 1.5; ctx.stroke(); }
    ctx.globalAlpha = 1;
  }
  ctx.lineWidth = 1;
}
function pick(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const x = (clientX - rect.left - rect.width / 2 - offsetX) / scale;
  const y = (clientY - rect.top - rect.height / 2 - offsetY) / scale;
  let best = null, bestDist = 14;
  for (const n of nodes) {
    const p = positions.get(n.id); if (!p) continue;
    const d = Math.hypot(p.x - x, p.y - y);
    if (d < bestDist) { best = n; bestDist = d; }
  }
  return best;
}
function showDetail(node) {
  const panel = document.getElementById('detail');
  if (!node) { panel.style.display = 'none'; return; }
  const related = (adjacency.get(node.id) || []).slice(0, 40).map(e => {
    const other = e.source === node.id ? e.target : e.source;
    const dir = e.source === node.id ? '→' : '←';
    const name = byId.get(other)?.name || other;
    return `${dir} ${e.relation} ${name} (${e.confidence})`;
  });
  panel.innerHTML = `<h2>${node.name} <span class="kind">${node.kind}</span></h2>
    <pre>${node.qualified || ''}\\n${node.file || ''}:${(node.start || [0,0])[0] + 1}\\n度数 ${node.degree || 0}（入 ${node.inDegree || 0} / 出 ${node.outDegree || 0}）\\n社区 ${node.community ?? '-'}</pre>
    <pre>${related.join('\\n') || '（没有关系）'}</pre>`;
  panel.style.display = 'block';
}
function renderList(filter = '') {
  const list = document.getElementById('list');
  const items = nodes.filter(n => !filter ||
    (n.name + ' ' + (n.qualified || '') + ' ' + (n.file || '')).toLowerCase().includes(filter.toLowerCase()))
    .slice(0, 200);
  list.innerHTML = items.map(n =>
    `<div class="item" data-id="${n.id}">${n.name} <span class="kind">${n.kind}</span></div>`).join('');
  list.querySelectorAll('.item').forEach(el => el.addEventListener('click', () => select(el.dataset.id)));
}
function select(id) {
  selected = id;
  const node = byId.get(id);
  highlight = new Set([id, ...(adjacency.get(id) || []).flatMap(e => [e.source, e.target])]);
  showDetail(node); draw(); renderList(document.getElementById('search').value);
}
document.getElementById('search').addEventListener('input', e => renderList(e.target.value));
canvas.addEventListener('click', e => { const n = pick(e.clientX, e.clientY); if (n) select(n.id); });
let dragging = false, lastX = 0, lastY = 0;
canvas.addEventListener('mousedown', e => { dragging = true; lastX = e.clientX; lastY = e.clientY; canvas.classList.add('dragging'); });
canvas.addEventListener('mouseup', () => { dragging = false; canvas.classList.remove('dragging'); });
canvas.addEventListener('mousemove', e => {
  if (!dragging) return;
  offsetX += e.clientX - lastX; offsetY += e.clientY - lastY;
  lastX = e.clientX; lastY = e.clientY; draw();
});
canvas.addEventListener('wheel', e => {
  e.preventDefault();
  scale = Math.max(0.2, Math.min(6, scale * (e.deltaY < 0 ? 1.12 : 0.89)));
  draw();
}, { passive: false });
document.getElementById('stats').textContent =
  `${DATA.repo?.target || ''} · ${nodes.length} 节点 / ${edges.length} 边 · 调用解析率 ` +
  (DATA.stats?.graph?.resolvedCallRate != null ? Math.round(DATA.stats.graph.resolvedCallRate * 100) + '%' : '—');
document.getElementById('legend').innerHTML = communityIds.slice(0, 12).map((cid, i) =>
  `<span class="swatch" style="background:${PALETTE[i % PALETTE.length]}"></span>` +
  (DATA.communities.find(c => c.id === cid)?.name || ('社区 ' + cid))).join(' &nbsp; ');
window.addEventListener('resize', resize);
renderList(); resize();
</script>
</body>
</html>
"""


def _trim_document(document: dict[str, Any]) -> dict[str, Any]:
    nodes = sorted(document["nodes"], key=lambda n: -(n.get("degree") or 0))[:MAX_HTML_NODES]
    keep = {node["id"] for node in nodes}
    edges = [
        edge for edge in document["edges"] if edge["source"] in keep and edge["target"] in keep
    ][:MAX_HTML_EDGES]
    return {**document, "nodes": nodes, "edges": edges}


def export_html(
    store: Store,
    settings: Settings,
    *,
    output: Path | None = None,
    repo_id: str | None = None,
) -> Path:
    """写出单文件 ``graph.html``（内联数据，离线可打开）。"""
    meta = store.load_repo_meta()
    identifier = repo_id or (meta.repo_id if meta else "repo")
    target = output or (settings.exports_dir / identifier / "graph.html")
    target.parent.mkdir(parents=True, exist_ok=True)

    document = _trim_document(build_document(store))
    document["exportedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    title = meta.target if meta else identifier
    payload = json.dumps(document, ensure_ascii=False).replace("</", "<\\/")
    html = (
        _PAGE.replace("__TITLE__", _escape(title))
        .replace("__DATA__", payload)
        .replace("{{VERSION}}", __version__)
    )
    target.write_text(html, encoding="utf-8")
    return target


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
