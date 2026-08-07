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
    <div class="panel" style="margin-top:14px"><h2>recent decisions</h2>
      <div class="ev" id="events"></div></div>
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

  const recent = D.decisions.filter(d => d[0] <= f.t).slice(-14).reverse();
  document.getElementById('events').innerHTML = recent.map(d =>
    `<div><span class="${d[2]==='idle'?'':'think'}">t${d[0]}</span> ${d[1]}
     <span style="color:var(--dim)">${d[2]}</span></div>`).join('') ||
    '<div style="color:var(--dim)">none yet</div>';

  document.getElementById('tickLabel').textContent = `t ${f.t} / ${D.frames[N-1].t}`;
  drawTrace(i);
}

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
draw(0);
</script>
"""


def build(events: list[dict], out_path, stride: int = 1,
          model: str | None = None) -> Path:
    """Render an event log to a standalone HTML file."""
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

    data = {
        "seed": start.get("seed"), "scenario": start.get("scenario"),
        "policy": start.get("policy", "?"), "model": model,
        "w": start["config"]["w"], "h": start["config"]["h"],
        "walls": start["walls"], "kinds": kinds,
        "frames": frames, "decisions": decisions, "tickIndex": tick_index,
    }
    html = _HTML.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8", newline="\n")
    return p
