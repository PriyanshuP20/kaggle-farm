const $ = (id) => document.getElementById(id);
const CROP_LETTER = { WHEAT: "w", CARROT: "c", TOMATO: "t", STRAWBERRY: "s", MELON: "m" };
const ANIMAL_LETTER = { GOOSE: "G", COW: "C", SHEEP: "S" };
const PRODUCTS = ["WHEAT","CARROT","TOMATO","STRAWBERRY","MELON","EGG","MILK","WOOL","FERTILIZER"];

let lastStep = -1;
let pollTimer = null;
let state = null;

async function loadAgents(selectAfter) {
  const res = await fetch("/api/agents");
  const data = await res.json();
  const agentSel = $("agent"), oppSel = $("opponent");
  const prevA = agentSel.value, prevO = oppSel.value;
  agentSel.innerHTML = "";
  oppSel.innerHTML = "";
  for (const f of data.files) {
    agentSel.add(new Option(f, f));
    oppSel.add(new Option(f, f));
  }
  const og = document.createElement("optgroup");
  og.label = "built-in";
  for (const b of data.builtins) { og.appendChild(new Option(b, b)); }
  oppSel.appendChild(og);
  agentSel.value = selectAfter || prevA || "main.py";
  oppSel.value = prevO || "starter";
}

function shedTiles(board) {
  const c = Math.floor(board / 2);
  return new Set([`${c-1},${c-1}`, `${c},${c-1}`, `${c-1},${c}`, `${c},${c}`]);
}

function renderBoard(p) {
  const tiles = p.tiles;
  const board = tiles.length;
  const sheds = shedTiles(board);
  const units = {}; // "x,y" -> {farmer:bool, hands:n}
  const key = (x, y) => `${x},${y}`;
  if (p.farmer) {
    units[key(p.farmer[0], p.farmer[1])] = { farmer: true, hands: 0 };
  }
  for (const h of p.hands || []) {
    const k = key(h[0], h[1]);
    units[k] = units[k] || { farmer: false, hands: 0 };
    units[k].hands++;
  }

  let html = `<div class="phead">
    <span class="pname">P${p.index} · ${p.name}</span>
    <span class="pmoney">$${Math.round(p.money)}</span>
    <span class="pstatus ${p.status}">${p.status}</span>
  </div>`;
  html += '<div class="board">';
  for (let y = 0; y < board; y++) {
    for (let x = 0; x < board; x++) {
      const t = tiles[y][x];
      let cls = "tile", txt = "", extra = "";
      if (t === "LOCKED") { cls += " locked"; txt = ""; }
      else if (t === null) { cls += " empty"; }
      else if (t.kind === "WEED") { cls += " weed"; txt = "✕"; }
      else if (t.kind === "PLANT") {
        cls += " " + t.crop;
        txt = CROP_LETTER[t.crop] || "?";
        if (t.yield_units > 0) extra += `<span class="yield">${t.yield_units}</span>`;
        if (t.watered_today) extra += `<span class="water">💧</span>`;
        else cls += " dry";
      } else if (t.kind === "COOP" || t.kind === "PASTURE") {
        cls += t.kind === "COOP" ? " coop" : " pasture";
        txt = t.animal ? ANIMAL_LETTER[t.animal] : (t.kind === "COOP" ? "c" : "p");
        if (t.yield_units > 0) extra += `<span class="yield">${t.yield_units}</span>`;
      }
      if (sheds.has(key(x, y))) cls += " shed";
      const u = units[key(x, y)];
      if (u) {
        if (u.farmer) extra += `<span class="unit farmer">F</span>`;
        for (let i = 0; i < Math.min(u.hands, 3); i++)
          extra += `<span class="unit hand">h</span>`;
      }
      html += `<div class="${cls}" title="${titleFor(t, x, y)}">${txt}${extra}</div>`;
    }
  }
  html += "</div>";

  const seeds = Object.entries(p.seeds || {}).filter(([, n]) => n > 0)
    .map(([k, n]) => `<span class="chip">${k}×${n}</span>`).join("") || '<span class="chip">none</span>';
  const shed = Object.entries(p.shed || {}).filter(([, n]) => n > 0)
    .map(([k, n]) => `<span class="chip">${k}×${n}</span>`).join("") || '<span class="chip">empty</span>';
  html += `<div class="pinfoline">
    hands <b>${(p.hands || []).length}</b> · unlocked <b>${(p.unlocked || []).join(" ")}</b><br>
    seeds ${seeds}<br>shed ${shed}
  </div>`;
  return html;
}

function titleFor(t, x, y) {
  if (t === null || t === undefined) return `(${x},${y}) empty`;
  if (t === "LOCKED") return `(${x},${y}) locked`;
  if (t.kind === "WEED") return `(${x},${y}) weed`;
  if (t.kind === "PLANT")
    return `(${x},${y}) ${t.crop} age? yield=${t.yield_units} watered=${t.watered_today} unw=${t.consecutive_unwatered}`;
  if (t.kind === "COOP" || t.kind === "PASTURE")
    return `(${x},${y}) ${t.kind}${t.animal ? " " + t.animal : ""} yield=${t.yield_units} fed=${t.fed_today}`;
  return `(${x},${y})`;
}

function renderMarket(s) {
  const inv = (s.market && s.market.inventory) || {};
  const pr = (s.market && s.market.prices) || {};
  let html = "<h3>Market</h3><table class='mkt'><tr><th>item</th><th>price</th><th>inv</th></tr>";
  for (const it of PRODUCTS)
    html += `<tr><td>${it}</td><td class="price">$${pr[it] ?? "-"}</td><td>${inv[it] ?? "-"}</td></tr>`;
  html += "</table>";
  $("market").innerHTML = html;

  const shops = (s.town && s.town.unlocked_shops) || [];
  $("town").innerHTML = `<h3>Town (${shops.length} shops)</h3>` +
    (shops.length ? shops.map(x => `<span class="chip">${x}</span>`).join("") : "—");
}

function render(s) {
  $("panel0").innerHTML = renderBoard(s.players[0]);
  $("panel1").innerHTML = renderBoard(s.players[1]);
  renderMarket(s);
  const sb = $("statusbar");
  const p0 = s.players[0], p1 = s.players[1];
  let txt = `step <b>${s.step}/${s.episodeSteps}</b> · day <b>${s.day}</b> · hour <b>${s.hour}</b> · ` +
    `<b>${p0.name}</b> $${Math.round(p0.money)} vs <b>${p1.name}</b> $${Math.round(p1.money)}`;
  if (s.done) {
    const winner = p0.money > p1.money ? p0.name : p1.money > p0.money ? p1.name : null;
    txt += winner ? ` · DONE — winner: <b>${winner}</b>` : " · DONE — tie";
  } else if (s.running) {
    txt += " · running…";
  }
  if (s.error) txt += ` <span class="err">error: ${String(s.error).split("\n").pop() || s.error}</span>`;
  sb.innerHTML = txt;
}

async function poll() {
  try {
    const res = await fetch("/api/state");
    const s = await res.json();
    state = s;
    if (!s.players) return; // idle, no game yet
    if (s.step !== lastStep || s.done !== render._lastDone || !s.running) {
      lastStep = s.step;
      render._lastDone = s.done;
      render(s);
    }
    if (!s.running) {
      $("stopBtn").disabled = true;
      $("run").disabled = false;
    }
  } catch (e) { /* server restarting */ }
}

async function start() {
  const body = {
    agent: $("agent").value,
    opponent: $("opponent").value,
    seed: parseInt($("seed").value || "0"),
    steps: parseInt($("steps").value || "720"),
    speed: $("turbo").checked ? 0 : parseInt($("speed").value),
  };
  await fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  lastStep = -1;
  $("run").disabled = true;
  $("stopBtn").disabled = false;
}

$("run").onclick = start;
$("stopBtn").onclick = () => fetch("/api/stop", { method: "POST" });
$("speed").oninput = () => { $("speedLabel").textContent = $("speed").value + "/s"; };
$("upload").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const data = await res.json();
  if (data.saved) { await loadAgents(data.saved); }
  e.target.value = "";
};

loadAgents();
pollTimer = setInterval(poll, 150);
poll();
