"""Replay viewer: a self-contained HTML timeline of a run.

The point is to make the invisible visible — specifically the gap between the
world and the agent's belief about it. A run is only legible if you can see
where the agent thought it was versus where it actually was, what surprised it,
and which of those surprises were worth a thought.

Output is one HTML file with the data inlined: no server, no build step, no
network. Open it and scrub.
"""

from __future__ import annotations

import json
from pathlib import Path

_HTML = """<!doctype html>
<meta charset="utf-8">
<title>HOMUNCULUS — replay</title>
<style>
  :root {
    --bg:#0f1115; --fg:#e6e6e6; --dim:#8a8f98; --line:#232833;
    --truth:#4da3ff; --belief:#ff9f4d; --food:#5ad17f; --warm:#ff6b6b;
    --lm:#b58cff; --critter:#8a8f98; --think:#ffd24d;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:14px 18px; border-bottom:1px solid var(--line); }
  h1 { margin:0; font-size:15px; letter-spacing:.06em; text-transform:uppercase; }
  .sub { color:var(--dim); margin-top:4px; }
  .wrap { display:grid; grid-template-columns:minmax(320px,1fr) minmax(300px,420px);
          gap:18px; padding:18px; align-items:start; }
  canvas { width:100%; height:auto; background:#0b0d11; border:1px solid var(--line);
           border-radius:6px; image-rendering:pixelated; }
  .panel { border:1px solid var(--line); border-radius:6px; padding:12px; }
  .panel h2 { margin:0 0 10px; font-size:11px; letter-spacing:.1em;
              text-transform:uppercase; color:var(--dim); font-weight:600; }
  .row { display:flex; justify-content:space-between; gap:10px; padding:3px 0; }
  .row span:last-child { color:var(--dim); }
  .bar { height:6px; background:#1a1f28; border-radius:3px; overflow:hidden; margin-top:3px; }
  .bar i { display:block; height:100%; }
  .controls { display:flex; gap:10px; align-items:center; padding:0 18px 6px; }
  input[type=range] { flex:1; }
  button { background:#1a1f28; color:var(--fg); border:1px solid var(--line);
           border-radius:4px; padding:5px 12px; cursor:pointer; font:inherit; }
  button:hover { background:#232833; }
  .legend { display:flex; flex-wrap:wrap; gap:12px; color:var(--dim);
            padding:0 18px 12px; }
  .legend b { font-weight:400; color:var(--fg); }
  .sw { display:inline-block; width:9px; height:9px; border-radius:2px;
        vertical-align:middle; margin-right:5px; }
  .ev { max-height:210px; overflow:auto; }
  .ev div { padding:2px 0; border-bottom:1px solid #171b23; }
  .think { color:var(--think); }

  /* conscious stream */
  .stream { max-height:560px; overflow:auto; padding-right:4px; }
  .entry { padding:8px 0 8px 12px; border-left:2px solid #232833; margin-bottom:2px;
           opacity:.45; transition:opacity .15s; }
  .entry.seen { opacity:1; }
  .entry.now { background:#141922; border-left-color:#fff; }
  .entry .t { color:var(--dim); font-size:11px; }
  .entry .head { margin-top:1px; }
  .entry .body { color:var(--dim); margin-top:2px; }
  .entry .saw { color:#6f7681; margin-top:2px; font-size:12px; }
  .entry .note { margin-top:4px; padding-left:8px; border-left:2px solid #2b3240;
                 color:#cfd4dc; font-style:italic; }
  .entry .exp { color:var(--dim); font-size:11px; margin-top:2px; }
  .k-thought { border-left-color:var(--think); }
  .k-thought .head { color:var(--think); }
  .k-habit   { border-left-color:#2f3542; }
  .k-outcome { border-left-color:var(--truth); }
  .k-percept { border-left-color:var(--warm); }
  .k-speech  { border-left-color:var(--food); }
  .k-speech .head { color:var(--food); }
  .k-sleep   { border-left-color:var(--lm); }
  .filters { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px;
             align-items:center; }
  .filters label { color:var(--dim); cursor:pointer; user-select:none; }
  .filters input { vertical-align:middle; margin-right:3px; }
  .filters .spacer { flex:1; }
  .counts { color:var(--dim); }
  .counts b { color:var(--fg); font-weight:400; }

  /* memory */
  .mem { display:grid; grid-template-columns:1.6fr 1fr 1fr; gap:16px; }
  @media (max-width:1000px){ .mem { grid-template-columns:1fr; } }
  .mcol h3 { margin:0 0 8px; font-size:11px; letter-spacing:.08em;
             text-transform:uppercase; color:var(--dim); font-weight:600; }
  .mlist { max-height:300px; overflow:auto; }
  .mitem { padding:6px 0 6px 10px; border-left:2px solid #232833;
           border-bottom:1px solid #171b23; }
  .mitem .mt { color:var(--dim); font-size:11px; }
  .mitem.hot { border-left-color:var(--think); }
  .mitem.recalled { border-left-color:var(--food); }
  .mitem.gone { opacity:.32; border-left-color:#3a2020; text-decoration:line-through; }
  .mitem .ents { color:#6f7681; font-size:11px; }
  .mbar { color:var(--dim); margin-bottom:8px; }
  .mbar b { color:var(--fg); font-weight:400; }
  @media (max-width:900px){ .wrap { grid-template-columns:1fr; } }
</style>
<header>
  <h1>Homunculus — replay</h1>
  <div class="sub" id="meta"></div>
</header>
<div class="controls">
  <button id="play">play</button>
  <input type="range" id="scrub" min="0" value="0">
  <span id="tickLabel" class="sub"></span>
</div>
<div class="legend">
  <span><i class="sw" style="background:var(--truth)"></i><b>true position</b></span>
  <span><i class="sw" style="background:var(--belief)"></i><b>believed position</b></span>
  <span><i class="sw" style="background:var(--food)"></i>food</span>
  <span><i class="sw" style="background:var(--warm)"></i>warmth</span>
  <span><i class="sw" style="background:var(--lm)"></i>landmark</span>
  <span><i class="sw" style="background:var(--critter)"></i>critter</span>
  <span><i class="sw" style="background:var(--think)"></i>thought</span>
</div>
<div class="wrap">
  <div>
    <canvas id="grid" width="640" height="640"></canvas>
    <div class="panel" style="margin-top:14px">
      <h2>surprise · thoughts</h2>
      <canvas id="trace" width="640" height="110" style="height:110px"></canvas>
    </div>
  </div>
  <div>
    <div class="panel"><h2>body</h2><div id="drives"></div></div>
    <div class="panel" style="margin-top:14px"><h2>state</h2><div id="state"></div></div>
  </div>
</div>
<div style="padding:0 18px 24px">
  <div class="panel">
    <h2>conscious stream</h2>
    <div class="filters" id="filters"></div>
    <div class="stream" id="stream"></div>
  </div>
  <div class="panel" id="memPanel" style="margin-top:18px; display:none">
    <h2>memory</h2>
    <div class="mbar" id="mbar"></div>
    <div class="mem">
      <div class="mcol">
        <h3>episodic</h3>
        <div class="mlist" id="memEpisodic"></div>
      </div>
      <div class="mcol">
        <h3>semantic</h3>
        <div class="mlist" id="memSemantic"></div>
      </div>
      <div class="mcol">
        <h3>procedural</h3>
        <div class="mlist" id="memProcedural"></div>
      </div>
    </div>
  </div>
</div>
<script>
const D = __DATA__;
const g = document.getElementById('grid').getContext('2d');
const tr = document.getElementById('trace').getContext('2d');
const S = 640 / D.w, N = D.frames.length;
document.getElementById('meta').textContent =
  `seed ${D.seed} · ${D.scenario} · ${N} ticks · policy ${D.policy}` +
  (D.model ? ` · ${D.model}` : '');
const scrub = document.getElementById('scrub'); scrub.max = N - 1;

const KIND = {food:'--food', warmth:'--warm', landmark:'--lm',
              critter:'--critter', resident:'--critter', item:'--dim',
              agent:'--truth'};
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n);

function draw(i){
  const f = D.frames[i];
  g.fillStyle = '#0b0d11'; g.fillRect(0,0,640,640);
  g.fillStyle = '#161b24';
  for (const [x,y] of D.walls) g.fillRect(x*S, y*S, S, S);

  for (const e of f.ents){
    const [id,x,y] = e, kind = D.kinds[id] || 'item';
    g.fillStyle = css(KIND[kind] || '--dim').trim();
    g.globalAlpha = kind === 'agent' ? 1 : .8;
    g.beginPath(); g.arc((x+.5)*S, (y+.5)*S, S*0.3, 0, 7); g.fill();
    g.globalAlpha = 1;
  }
  // true vs believed position of the agent: the gap IS the story
  g.strokeStyle = css('--truth').trim(); g.lineWidth = 2;
  g.beginPath(); g.arc((f.tx+.5)*S, (f.ty+.5)*S, S*0.45, 0, 7); g.stroke();
  g.strokeStyle = css('--belief').trim(); g.setLineDash([4,3]);
  g.beginPath(); g.arc((f.bx+.5)*S, (f.by+.5)*S, S*0.45, 0, 7); g.stroke();
  g.setLineDash([]);

  // drives
  const bars = [['energy',f.d[0],'--food'],['warmth',f.d[1],'--warm'],
                ['fatigue',f.d[2],'--belief']];
  document.getElementById('drives').innerHTML = bars.map(([n,v,c]) =>
    `<div class="row"><span>${n}</span><span>${v.toFixed(2)}</span></div>
     <div class="bar"><i style="width:${Math.round(v*100)}%;
      background:${css(c).trim()}"></i></div>`).join('');

  document.getElementById('state').innerHTML = [
    ['tick', f.t], ['pose error', f.err.toFixed(2)],
    ['pose conf', f.pc.toFixed(2)], ['surprise', f.s.toFixed(2)],
    ['valence', f.v.toFixed(2)], ['thoughts so far', f.nd],
  ].map(([k,v]) => `<div class="row"><span>${k}</span><span>${v}</span></div>`).join('');

  document.getElementById('tickLabel').textContent = `t ${f.t} / ${D.frames[N-1].t}`;
  drawTrace(i);
  syncStream(f.t);
  renderMemory(f.t);
}

/* ---- memory ---------------------------------------------------------- */
function renderMemory(t){
  const M = D.memory;
  if (!M) return;
  document.getElementById('memPanel').style.display = '';

  // What the agent HELD at tick t: stored by then, not yet forgotten.
  const held = M.episodic.filter(e => e.t <= t && (e.gone === null || e.gone > t));
  const lost = M.episodic.filter(e => e.gone !== null && e.gone <= t);
  const maxS = Math.max(1, ...held.map(e => e.s));

  document.getElementById('mbar').innerHTML =
    `<b>${held.length}</b>/${M.capacity} episodes held · ` +
    `<b>${lost.length}</b> forgotten · policy <b>${M.policy}</b> · ` +
    `<b>${M.semantic.filter(f=>f.first<=t).length}</b> facts`;

  // Most surprising first — the ones the policy is keeping on purpose.
  const shownEp = held.slice().sort((a,b) => b.s - a.s).slice(0, 60);
  const recentlyLost = lost.slice(-8).reverse();
  document.getElementById('memEpisodic').innerHTML =
    shownEp.map(e => {
      const recalled = e.lastRecall !== null && e.lastRecall <= t;
      const cls = recalled ? 'recalled' : (e.s >= maxS * 0.6 ? 'hot' : '');
      return `<div class="mitem ${cls}">
        <div class="mt">t${e.t} · surprise ${e.s}${recalled ? ' · recalled' : ''}</div>
        <div>${esc(e.text)}</div>
        ${e.ents.length ? `<div class="ents">${esc(e.ents.join(' '))}</div>` : ''}
      </div>`;
    }).join('') +
    recentlyLost.map(e => `<div class="mitem gone">
        <div class="mt">t${e.t} · forgotten at t${e.gone}</div>
        <div>${esc(e.text)}</div>
      </div>`).join('') ||
    '<div style="color:var(--dim)">nothing stored yet</div>';

  const facts = M.semantic.filter(f => f.first <= t);
  document.getElementById('memSemantic').innerHTML = facts.map(f =>
    `<div class="mitem">
       <div class="mt">learned t${f.first} · support ${f.support}</div>
       <div>${esc(f.text)}</div>
     </div>`).join('') ||
    '<div style="color:var(--dim)">no facts distilled yet</div>';

  document.getElementById('memProcedural').innerHTML = M.procedural.map(p =>
    `<div class="mitem">
       <div class="mt">when ${esc(p.situation)}</div>
       <div>${esc(p.verb)}${p.target ? ' ' + esc(p.target) : ''}
         <span style="color:var(--dim)">${p.wins}/${p.n} worked</span></div>
     </div>`).join('') ||
    '<div style="color:var(--dim)">nothing learned yet</div>';
}

/* ---- conscious stream ---------------------------------------------- */
const KINDS = ['thought','habit','outcome','percept','speech','sleep'];
const shown = new Set(['thought','outcome','percept','speech','sleep']);
const esc = s => (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

const counts = {};
for (const e of D.stream) counts[e.kind] = (counts[e.kind]||0) + 1;
document.getElementById('filters').innerHTML =
  KINDS.filter(k => counts[k]).map(k =>
    `<label><input type="checkbox" data-k="${k}" ${shown.has(k)?'checked':''}>` +
    `${k} <span style="color:var(--dim)">${counts[k]}</span></label>`
  ).join('') +
  `<span class="spacer"></span><span class="counts">` +
  `<b>${counts.thought||0}</b> thoughts · <b>${counts.habit||0}</b> habitual · ` +
  `${D.frames[D.frames.length-1].t} ticks</span>`;
document.getElementById('filters').onchange = e => {
  const k = e.target.dataset.k;
  e.target.checked ? shown.add(k) : shown.delete(k);
  renderStream(); syncStream(D.frames[+scrub.value].t);
};

function renderStream(){
  document.getElementById('stream').innerHTML = D.stream
    .map((e,i) => shown.has(e.kind) ? `
      <div class="entry k-${e.kind}" data-t="${e.t}" data-i="${i}">
        <div class="t">t${e.t} · ${e.kind}</div>
        <div class="head">${esc(e.head)}</div>
        ${e.body ? `<div class="body">${esc(e.body)}</div>` : ''}
        ${e.saw ? `<div class="saw">saw: ${esc(e.saw)}</div>` : ''}
        ${e.note ? `<div class="note">${esc(e.note)}</div>` : ''}
        ${e.expect ? `<div class="exp">expected surprise: ${esc(e.expect)}</div>` : ''}
      </div>` : '').join('') || '<div style="color:var(--dim)">nothing yet</div>';
}

// Click any stream entry to jump the whole view to that moment.
document.getElementById('stream').addEventListener('click', ev => {
  const el = ev.target.closest('.entry');
  if (!el) return;
  const t = +el.dataset.t;
  let best = 0, bd = Infinity;
  D.frames.forEach((f,i) => { const d = Math.abs(f.t - t); if (d < bd){ bd=d; best=i; } });
  scrub.value = best; draw(best);
});

let lastNow = null;
function syncStream(t){
  const box = document.getElementById('stream');
  let current = null;
  for (const el of box.querySelectorAll('.entry')){
    const et = +el.dataset.t;
    el.classList.toggle('seen', et <= t);
    el.classList.remove('now');
    if (et <= t) current = el;
  }
  if (current && current !== lastNow){
    current.classList.add('now');
    // Keep the newest entry in view without hijacking manual scrolling.
    const top = current.offsetTop - box.clientHeight * 0.6;
    box.scrollTo({top: Math.max(0, top), behavior: 'smooth'});
    lastNow = current;
  } else if (current) current.classList.add('now');
}
renderStream();

function drawTrace(cur){
  tr.fillStyle='#0b0d11'; tr.fillRect(0,0,640,110);
  const max = Math.max(1, ...D.frames.map(f=>f.s));
  tr.strokeStyle = css('--dim').trim(); tr.globalAlpha=.5;
  tr.beginPath(); tr.moveTo(0,109); tr.lineTo(640,109); tr.stroke();
  tr.globalAlpha=1;
  tr.strokeStyle = css('--truth').trim(); tr.beginPath();
  D.frames.forEach((f,i)=>{ const x=i/(N-1)*640, y=108-(f.s/max)*96;
    i? tr.lineTo(x,y) : tr.moveTo(x,y); });
  tr.stroke();
  // a tick mark wherever the mind was actually consulted
  tr.strokeStyle = css('--think').trim(); tr.globalAlpha=.75;
  for (const d of D.decisions){
    if (d[2]==='habit') continue;
    const i = D.tickIndex[d[0]]; if (i===undefined) continue;
    const x = i/(N-1)*640;
    tr.beginPath(); tr.moveTo(x,100); tr.lineTo(x,108); tr.stroke();
  }
  tr.globalAlpha=1;
  tr.strokeStyle='#fff'; tr.beginPath();
  const cx=cur/(N-1)*640; tr.moveTo(cx,0); tr.lineTo(cx,110); tr.stroke();
}

let playing=false, timer=null;
document.getElementById('play').onclick = () => {
  playing = !playing;
  document.getElementById('play').textContent = playing ? 'pause' : 'play';
  if (playing) timer = setInterval(()=>{
    let i = (+scrub.value + 1) % N; scrub.value = i; draw(i);
  }, 40); else clearInterval(timer);
};
scrub.oninput = () => draw(+scrub.value);

// Arrow keys scrub; shift jumps ten frames at a time.
addEventListener('keydown', e => {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  const step = (e.shiftKey ? 10 : 1) * (e.key === 'ArrowRight' ? 1 : -1);
  const i = Math.min(N-1, Math.max(0, +scrub.value + step));
  scrub.value = i; draw(i); e.preventDefault();
});

// Clicking the surprise trace seeks to that point in the run.
document.getElementById('trace').addEventListener('click', e => {
  const r = e.target.getBoundingClientRect();
  const i = Math.round((e.clientX - r.left) / r.width * (N - 1));
  scrub.value = Math.min(N-1, Math.max(0, i)); draw(+scrub.value);
});

draw(0);
</script>
"""


def build(events: list[dict], out_path, stride: int = 1,
          model: str | None = None, max_stream: int = 1200,
          memory=None) -> Path:
    """Render an event log to a standalone HTML file."""
    from .narrate import stream as narrate_stream

    start = next(e for e in events if e["type"] == "run_start")
    ticks = [e for e in events if e["type"] == "tick"]
    kinds = {e["id"]: e["kind"] for e in start["entities"]}
    pos = {e["id"]: (e["x"], e["y"]) for e in start["entities"]}

    frames, decisions, tick_index = [], [], {}
    ndec = 0
    for ev in ticks:
        for m in ev.get("moves", ()):
            pos[m["id"]] = (m["to"][0], m["to"][1])
        if ev.get("decision"):
            ndec += 1
            d = ev["decision"]
            label = d["verb"] + (f" {d['target']}" if d.get("target") else "")
            decisions.append([ev["t"], label, ev.get("gate", "idle")])
        if (ev["t"] - 1) % stride:
            continue
        bx, by = ev.get("pose", [0, 0, 0])[:2]
        tx, ty = pos.get("agent", (0, 0))
        drives = ev.get("drives") or {}
        tick_index[ev["t"]] = len(frames)
        frames.append({
            "t": ev["t"],
            "tx": tx, "ty": ty,
            "bx": round(bx, 2), "by": round(by, 2),
            "err": round(((bx - tx) ** 2 + (by - ty) ** 2) ** 0.5, 3),
            "pc": ev.get("pose_conf", 1.0),
            "s": round((ev.get("surprise") or {}).get("scalar", 0.0), 3),
            "v": ev.get("valence", 0.0),
            "nd": ndec,
            "d": [drives.get("energy", 0), drives.get("warmth", 0),
                  drives.get("fatigue", 0)],
            "ents": [[i, p[0], p[1]] for i, p in sorted(pos.items())],
        })

    entries = narrate_stream(events)
    if len(entries) > max_stream:
        # Keep the thinking; habitual repetition is what gets dropped first.
        rank = {"thought": 0, "speech": 1, "sleep": 1, "percept": 2,
                "outcome": 3, "habit": 4}
        keep = sorted(entries, key=lambda e: (rank.get(e["kind"], 5), e["t"]))
        entries = sorted(keep[:max_stream], key=lambda e: e["t"])

    data = {
        "seed": start.get("seed"), "scenario": start.get("scenario"),
        "policy": start.get("policy", "?"), "model": model,
        "w": start["config"]["w"], "h": start["config"]["h"],
        "walls": start["walls"], "kinds": kinds,
        "frames": frames, "decisions": decisions, "tickIndex": tick_index,
        "stream": entries,
        "memory": memory.export() if memory is not None else None,
    }
    html = _HTML.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8", newline="\n")
    return p
