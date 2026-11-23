// Reading map for a series page: Map / List views over the JSON in #map-data (books_db.map_json).
// Clicking a book cycles To read -> Reading -> Read and saves it right away.
(() => {
  const dataEl = document.getElementById("map-data");
  if (!dataEl) return;
  const M = JSON.parse(dataEl.textContent);
  const map = document.getElementById("map");
  const listEl = document.getElementById("list-view");
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const NEXT = { want: "reading", reading: "read", read: "want", skip: "want" };
  const LABEL = M.statuses;
  const PRED_KINDS = new Set(["seq", "rowseq", "elbow", "vdash", "branch", "logic"]);
  const DONE = new Set(["read", "skip"]);
  let view = "map";

  // --- model ---------------------------------------------------------------------------------------
  const sectionOf = {};
  const groupOf = {};
  M.sections.forEach((s, si) => s.groups.forEach((g) => g.rows.forEach((r) => r.ids.forEach((id) => {
    sectionOf[id] = si;
    groupOf[id] = g;
  }))));
  const arcs = (si) => M.sections[si].groups.filter((g) => !g.loose && g.rows[0].ids.length);
  const resolve = (ref) => {
    const [kind, v] = ref.split(":");
    const i = Number(v);
    if (kind === "b") return [i];
    if (kind === "gate") return [{ gate: i }];
    if (kind === "colsfirst") return arcs(i).map((g) => g.rows[0].ids[0]);
    if (kind === "colslast") return arcs(i).map((g) => g.rows[g.rows.length - 1].ids.at(-1));
    return [];
  };
  const edges = [];
  M.sections.forEach((s) => s.groups.forEach((g) => {
    if (g.loose || s.kind === "loose") return;
    g.rows.forEach((r) => {
      if (s.kind === "chains" || s.kind === "row") {
        if (r.seq) r.ids.slice(1).forEach((id, k) => edges.push({ from: r.ids[k], to: id, kind: "rowseq" }));
      } else {
        r.ids.slice(1).forEach((id, k) => edges.push({ from: r.ids[k], to: id, kind: "seq" }));
      }
    });
  }));
  M.links.forEach(([a, b, kind]) => resolve(a).forEach((f) => resolve(b).forEach((t) => edges.push({ from: f, to: t, kind }))));

  const node = (id) => M.nodes[id];
  const preds = (id) => edges.filter((e) => e.to === id && PRED_KINDS.has(e.kind)).map((e) => e.from);
  const isLoose = (id) => groupOf[id].loose || M.sections[sectionOf[id]].kind === "loose";
  // Loose groups (anthology strips, standalones, unplaced books) never get "Up next".
  const upNext = (id) => node(id).status === "want" && !isLoose(id) && preds(id).every((p) => DONE.has(node(p).status));
  const dots = (codes) => (codes.length ? `<span class="dots">${codes.filter((c) => M.lines[c]).map((c) =>
    `<i class="dot" style="--f:${M.lines[c][1]};--b:${M.lines[c][2]}" title="${esc(M.lines[c][0])}"></i>`).join("")}</span>` : "");

  // --- map view -------------------------------------------------------------------------------------
  function nodeHtml(id, color) {
    const n = node(id);
    const badge = n.status === "reading" ? '<span class="badge reading">Reading</span>' : upNext(id) ? '<span class="badge">Up next</span>' : "";
    const tip = [n.title, n.sub, LABEL[n.status], n.lines.length ? "Lines: " + n.lines.filter((c) => M.lines[c]).map((c) => M.lines[c][0]).join(", ") : "",
      n.stories.length ? "Stories: " + n.stories.join(", ") : ""].filter(Boolean).join("\n");
    return `<li><button type="button" class="node st-${n.status}" data-id="${id}" style="--arc:${color}" title="${esc(tip)}">${badge}` +
      `<span class="t">${esc(n.title)}</span><span class="s"><span>${esc(n.sub)}</span>${dots(n.lines)}` +
      `<a class="open" href="/books/book/${id}" title="Open book page" aria-label="Open ${esc(n.title)}">&#8599;</a></span></button></li>`;
  }

  function headHtml(name, hint, ids) {
    const done = ids.filter((i) => node(i).status === "read").length;
    return `<div class="arc-head"><h3>${esc(name)}</h3><div class="sub"><span>${ids.length} title${ids.length === 1 ? "" : "s"}</span><span>${done}/${ids.length}</span></div></div>` +
      (hint ? `<div class="loose-hint">${esc(hint)}</div>` : "");
  }

  function groupHtml(s, g, layout) {
    const ids = g.rows.flatMap((r) => r.ids);
    const loose = g.loose || s.kind === "loose";
    const cls = ["arc", layout === "row" ? "row" : "", layout === "solo" ? "solo" : "", loose ? "loose" : ""].join(" ");
    const hint = g.hint || (loose && !s.hint ? "Any order, any time" : layout === "col" ? "" : s.hint);
    if (layout === "chains") {
      const rows = g.rows.map((r) => `<ol>${'<li class="slot"></li>'.repeat(r.indent)}${r.ids.map((i) => nodeHtml(i, g.color)).join("")}</ol>`).join("");
      return `<section class="arc chains" style="--arc:${g.color}">${headHtml(s.name, s.hint, ids)}<div class="chain-rows">${rows}</div></section>`;
    }
    return `<section class="${cls}" style="--arc:${g.color}">${headHtml(layout === "col" ? g.name : s.name, hint, ids)}<ol>${ids.map((i) => nodeHtml(i, g.color)).join("")}</ol></section>`;
  }

  function renderMap() {
    let html = '<svg class="wires" id="wires" aria-hidden="true"></svg>';
    M.sections.forEach((s, i) => {
      if (s.kind === "cols") {
        const gs = s.groups.filter((g) => g.rows.some((r) => r.ids.length));
        html += `<div class="cols" data-s="${i}" style="grid-template-columns: repeat(${gs.length}, minmax(var(--col), 1fr))">${gs.map((g) => groupHtml(s, g, "col")).join("")}</div>`;
      } else if (!s.groups[0].rows.some((r) => r.ids.length)) {
        return;
      } else {
        const layout = s.kind === "chains" ? "chains" : s.kind === "solo" ? "solo" : "row";
        html += `<div class="band" data-s="${i}">${groupHtml(s, s.groups[0], layout)}</div>`;
      }
    });
    map.innerHTML = html;
    requestAnimationFrame(drawWires);
  }

  function drawWires() {
    const svg = document.getElementById("wires");
    if (!svg || view !== "map") return;
    const base = map.getBoundingClientRect();
    const rel = (r) => ({ l: r.left - base.left, r: r.right - base.left, t: r.top - base.top, b: r.bottom - base.top,
      cx: (r.left + r.right) / 2 - base.left, cy: (r.top + r.bottom) / 2 - base.top });
    const box = (id) => { const el = map.querySelector(`.node[data-id="${id}"]`); return el ? rel(el.getBoundingClientRect()) : null; };
    const secBox = (i) => { const el = map.querySelector(`[data-s="${i}"]`); return el ? rel(el.getBoundingClientRect()) : null; };
    const gateBox = (i) => { const el = map.querySelector(`[data-s="${i}"] .arc-head`); return el ? rel(el.getBoundingClientRect()) : null; };
    svg.setAttribute("width", map.scrollWidth);
    svg.setAttribute("height", map.scrollHeight);
    const sectionDone = (i) => M.sections[i].groups.every((g) => g.rows.every((r) => r.ids.every((id) => DONE.has(node(id).status))));
    const colorOf = (id) => groupOf[id].color;
    const used = new Map();
    let paths = "";
    edges.forEach((e) => {
      if (e.kind === "logic") return;
      let d, color, travelled;
      if (e.kind === "fromGate") {
        const g = secBox(e.from.gate), b = box(e.to), t = secBox(sectionOf[e.to]);
        if (!g || !b || !t) return;
        const y = (g.b + t.t) / 2;
        d = `M${g.cx},${g.b} V${y} H${b.cx} V${b.t - 2}`;
        travelled = sectionDone(e.from.gate);
        color = M.sections[e.from.gate].color;
      } else {
        const a = box(e.from);
        if (!a) return;
        travelled = DONE.has(node(e.from).status);
        color = colorOf(e.from);
        if (e.kind === "toGate") {
          const g = gateBox(e.to.gate), src = secBox(sectionOf[e.from]);
          if (!g || !src) return;
          const y = (src.b + g.t) / 2;
          d = `M${a.cx},${a.b} V${y} H${g.cx} V${g.t - 2}`;
        } else {
          const b = box(e.to);
          if (!b) return;
          if (e.kind === "seq" || e.kind === "vdash") d = `M${a.cx},${a.b} V${b.t - 2}`;
          else if (e.kind === "rowseq") d = `M${a.r},${a.cy} H${b.l - 2}`;
          else if (e.kind === "elbow") { const y = (a.b + b.t) / 2; d = `M${a.cx},${a.b} V${y} H${b.cx} V${b.t - 2}`; }
          else if (e.kind === "branch") {
            const sa = secBox(sectionOf[e.from]), sb = secBox(sectionOf[e.to]);
            const y = sa && sb && sa.b < sb.t ? (sa.b + sb.t) / 2 : (a.b + b.t) / 2;
            d = `M${a.cx},${a.b} V${y} H${b.cx} V${b.t - 2}`;
          } else { // crossover: out of the side, down the gutter, into the target's side
            const right = b.cx > a.cx;
            const x = right ? a.r + 9 : a.l - 9;
            d = `M${right ? a.r : a.l},${a.cy} H${x} V${b.cy} H${right ? b.l - 2 : b.r + 2}`;
          }
        }
      }
      const stroke = travelled ? color : "var(--wire)";
      const mk = "m" + (used.has(stroke) ? used.get(stroke) : used.set(stroke, used.size).get(stroke));
      const dash = e.kind === "cross" || e.kind === "vdash" ? ' stroke-dasharray="5 5"' : "";
      const w = e.kind === "seq" || e.kind === "rowseq" ? 2 : 1.5;
      paths += `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="${w}"${dash} stroke-linejoin="round" marker-end="url(#ar-${mk})"/>`;
    });
    let defs = "<defs>";
    used.forEach((i, stroke) => {
      defs += `<marker id="ar-m${i}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="${stroke}"/></marker>`;
    });
    svg.innerHTML = defs + "</defs>" + paths;
  }

  // --- list view ------------------------------------------------------------------------------------
  function listBlocks() {
    const blocks = [];
    M.sections.forEach((s) => {
      const ids = s.groups.flatMap((g) => g.rows.flatMap((r) => r.ids));
      if (!ids.length) return;
      if (s.kind === "cols" && ids.some((i) => node(i).o != null)) {
        const sorted = [...ids].sort((a, b) => (node(a).o ?? 1e9) - (node(b).o ?? 1e9));
        blocks.push({ name: s.name + " · reading order", ids: sorted, color: s.color, numbered: true, big: true });
      } else if (s.kind === "cols") {
        s.groups.forEach((g) => {
          const gids = g.rows.flatMap((r) => r.ids);
          if (gids.length) blocks.push({ name: g.name, ids: gids, color: g.color, hint: g.loose ? (g.hint || "Any order, any time") : "" });
        });
      } else {
        blocks.push({ name: s.name, ids, color: s.groups[0].color, hint: s.kind === "loose" ? s.hint : "" });
      }
    });
    return blocks;
  }

  function renderList() {
    if (!listEl) return;
    listEl.innerHTML = `<div class="list-arcs">${listBlocks().map((blk) => {
      const done = blk.ids.filter((i) => node(i).status === "read").length;
      const items = blk.ids.map((id, k) => {
        const n = node(id);
        const next = upNext(id);
        const st = n.status === "reading" ? "Reading" : next ? "Up next" : LABEL[n.status];
        const label = blk.numbered ? `${k + 1}.` : n.sub;
        const small = [blk.numbered ? n.sub : "", n.stories.join(", ")].filter(Boolean).join(" · ");
        return `<li><button type="button" class="li-btn st-${n.status}${next ? " next" : ""}" data-id="${id}"><span class="n">${esc(label)}</span>` +
          `<span class="tt">${esc(n.title)}${dots(n.lines)}${small ? `<small>${esc(small)}</small>` : ""}</span><span class="st">${st}</span></button></li>`;
      }).join("");
      return `<section class="list-arc${blk.big ? " big" : ""}" style="--arc:${blk.color}"><h4>${esc(blk.name)}<span>${done}/${blk.ids.length}</span></h4>` +
        `${blk.hint ? `<div class="loose-hint">${esc(blk.hint)}</div>` : ""}<ol>${items}</ol></section>`;
    }).join("")}</div>`;
  }

  // --- legend + progress ----------------------------------------------------------------------------
  function renderMeta() {
    const legend = document.getElementById("line-legend");
    if (legend) {
      const usedCodes = new Set(Object.values(M.nodes).flatMap((n) => n.lines));
      const codes = Object.keys(M.lines).filter((c) => usedCodes.has(c));
      legend.innerHTML = codes.length ? '<span class="lab">Lines:</span>' + codes.map((c) => `<span>${dots([c])}${esc(M.lines[c][0])}</span>`).join("") : "";
    }
    const all = Object.values(M.nodes).filter((n) => n.status !== "skip");
    const done = all.filter((n) => n.status === "read").length;
    const count = document.getElementById("map-count");
    if (count) count.innerHTML = `${done}<span>/${all.length}</span>`;
    const bar = document.getElementById("map-bar");
    if (bar) bar.style.width = all.length ? `${Math.round((100 * done) / all.length)}%` : "0%";
  }

  function renderAll() {
    renderMeta();
    if (view === "map") renderMap(); else if (view === "list") renderList();
  }

  // --- status changes --------------------------------------------------------------------------------
  const notice = document.getElementById("map-notice");
  async function cycle(ev) {
    if (ev.target.closest("a")) return; // the little open-book link
    const el = ev.target.closest("[data-id]");
    if (!el) return;
    const id = Number(el.dataset.id);
    const n = node(id);
    const before = n.status;
    n.status = NEXT[n.status] || "want";
    renderAll();
    const again = document.querySelector(`${view === "map" ? ".node" : ".li-btn"}[data-id="${id}"]`);
    if (again) again.focus();
    try {
      const resp = await fetch(`/books/book/${id}/set-status`, {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ status: n.status }),
      });
      const ok = resp.ok && (resp.headers.get("content-type") || "").includes("application/json");
      if (!ok) { location.reload(); return; }
      if (notice) notice.hidden = true;
    } catch {
      n.status = before;
      renderAll();
      if (notice) { notice.textContent = "Couldn't save that change. Check the connection and try again."; notice.hidden = false; }
    }
  }
  map.addEventListener("click", cycle);
  if (listEl) listEl.addEventListener("click", cycle);

  // --- view switching ----------------------------------------------------------------------------
  const panes = { map: document.getElementById("map-scroll"), list: listEl, order: document.getElementById("order-view") };
  const buttons = [...document.querySelectorAll("[data-view]")];
  const key = `rl-series-view-${M.series_id}`;
  function setView(v) {
    view = panes[v] ? v : "map";
    buttons.forEach((b) => b.classList.toggle("on", b.dataset.view === view));
    Object.entries(panes).forEach(([k, el]) => { if (el) el.hidden = k !== view; });
    const legend = document.getElementById("line-legend");
    if (legend) legend.hidden = view === "order";
    try { localStorage.setItem(key, view); } catch {}
    renderAll();
  }
  buttons.forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  map.style.setProperty("--col", `${M.col}px`);
  new ResizeObserver(() => { if (view === "map") requestAnimationFrame(drawWires); }).observe(map);
  let initial = "map";
  try { initial = new URLSearchParams(location.search).get("view") || localStorage.getItem(key) || "map"; } catch {}
  setView(initial);
})();
