"""Render the dataset to ONE self-contained HTML file (no CDN, no network): inline CSS, vanilla JS, SVG charts.

All dynamic text is inserted with textContent, and the embedded JSON is escaped so '</script>' inside data cannot break out.
"""
from __future__ import annotations

import json

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpsPilot Operations Dashboard</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16202b;--mute:#5d6b7a;--line:#e1e5ea;--a:#1f6feb;--b:#e5832b;--err:#c62828;--warn:#b7791f;--info:#5d6b7a;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#0f141a;--card:#171e26;--ink:#e7edf3;--mute:#9aa8b6;--line:#2a3441;--a:#58a6ff;--b:#f0a05a;--err:#ff7b72;--warn:#e3b341;--info:#9aa8b6;--ok:#56d364}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}
header{padding:20px 16px 8px;max-width:1200px;margin:auto}h1{margin:0;font-size:22px}.sub{color:var(--mute);font-size:12px}
main{max-width:1200px;margin:auto;padding:8px 16px 40px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}
.tile,.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.tile b{display:block;font-size:22px}.tile span{color:var(--mute);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:12px}h2{font-size:14px;margin:0 0 8px}
svg{width:100%;height:auto;display:block}.axis{stroke:var(--line)}.lbl{fill:var(--mute);font-size:10px}.leg{display:flex;gap:12px;font-size:12px;color:var(--mute);margin-top:4px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
table{width:100%;border-collapse:collapse}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--mute);font-weight:600;font-size:12px}
tbody tr{cursor:pointer}tbody tr:hover,tbody tr:focus{background:color-mix(in srgb,var(--a) 10%,transparent);outline:none}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;border:1px solid currentColor}.error{color:var(--err)}.warning{color:var(--warn)}.info{color:var(--info)}
.bar{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:12px}.bar i{display:block;height:12px;border-radius:3px;background:var(--a)}.bar .n{min-width:150px}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}input,select{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
#detail{position:sticky;top:8px}.empty{color:var(--mute);font-style:italic}.num{text-align:right;font-variant-numeric:tabular-nums}
</style></head><body>
<header><h1>OpsPilot operations dashboard</h1><div class="sub" id="meta"></div></header>
<main>
<section class="tiles" id="tiles" aria-label="Key figures"></section>
<section class="grid">
<div class="card"><h2>Monthly spend vs budget (CAD, pre-tax)</h2><div id="c_spend"></div></div>
<div class="card"><h2>Invoice cycle time, received to approved (days)</h2><div id="c_cycle"></div></div>
<div class="card"><h2>Exceptions by type (invoices)</h2><div id="c_exc"></div></div>
<div class="card"><h2>Top suppliers by spend</h2><div id="c_top"></div></div>
<div class="card"><h2>On-time payment (%)</h2><div id="c_pay"></div></div>
<div class="card"><h2>Three-way match rate (%)</h2><div id="c_match"></div></div>
<div class="card"><h2>Workflow outcomes (agent)</h2><div id="agent"></div></div>
<div class="card"><h2>Open commitments by category</h2><div id="c_open"></div></div>
</section>
<section class="grid" style="margin-top:12px;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))">
<div class="card"><h2>Investigation queue</h2>
<div class="tools"><input id="q" placeholder="Search invoice, supplier, PO" aria-label="Search"><select id="sev" aria-label="Severity"><option value="">All severities</option><option>error</option><option>warning</option><option>info</option></select><select id="code" aria-label="Exception"><option value="">All exceptions</option></select><span class="sub" id="count"></span></div>
<div style="overflow-x:auto"><table><thead><tr><th>Invoice</th><th>Supplier</th><th>Date</th><th class="num">Total</th><th>Issues</th></tr></thead><tbody id="rows"></tbody></table></div></div>
<div class="card" id="detail"><h2>Invoice detail</h2><div id="d" class="empty">Select an invoice to see why it was flagged and what to do next.</div></div>
</section></main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D=JSON.parse(document.getElementById("data").textContent),NS="http://www.w3.org/2000/svg";
const $=id=>document.getElementById(id),fmt=n=>n==null?"-":Number(n).toLocaleString("en-CA",{maximumFractionDigits:0}),f1=n=>n==null?"-":Number(n).toFixed(1);
function el(t,a,txt){const e=document.createElement(t);for(const k in a||{})e.setAttribute(k,a[k]);if(txt!=null)e.textContent=txt;return e}
function sv(t,a,txt){const e=document.createElementNS(NS,t);for(const k in a||{})e.setAttribute(k,a[k]);if(txt!=null)e.textContent=txt;return e}
function empty(c,m){c.replaceChildren(el("div",{class:"empty"},m||"No data for this period."))}
function line(c,labels,series){c.replaceChildren();if(!labels.length)return empty(c);
 const W=520,H=210,L=44,R=10,T=10,B=26,vals=series.flatMap(s=>s.v).filter(v=>v!=null),mx=Math.max(...vals,1)*1.1,
 x=i=>L+(labels.length===1?(W-L-R)/2:i*(W-L-R)/(labels.length-1)),y=v=>T+(H-T-B)*(1-v/mx),s=sv("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":"line chart"});
 for(let g=0;g<=4;g++){const v=mx*g/4;s.append(sv("line",{x1:L,x2:W-R,y1:y(v),y2:y(v),class:"axis"}),sv("text",{x:L-4,y:y(v)+3,"text-anchor":"end",class:"lbl"},fmt(v)))}
 labels.forEach((l,i)=>{if(labels.length<=14||i%2===0)s.append(sv("text",{x:x(i),y:H-8,"text-anchor":"middle",class:"lbl"},l.slice(2,7)))});
 series.forEach(se=>{const pts=se.v.map((v,i)=>v==null?null:[x(i),y(v)]).filter(Boolean);
  s.append(sv("polyline",{points:pts.map(p=>p.join(",")).join(" "),fill:"none",stroke:se.c,"stroke-width":2}));pts.forEach(p=>s.append(sv("circle",{cx:p[0],cy:p[1],r:2.5,fill:se.c})))});
 c.append(s);const lg=el("div",{class:"leg"});series.forEach(se=>{const i=el("span");const sw=el("span",{class:"sw"});sw.style.background=se.c;i.append(sw,se.n);lg.append(i)});c.append(lg)}
function bars(c,items,color){c.replaceChildren();if(!items.length)return empty(c);const mx=Math.max(...items.map(i=>i.v),1);
 items.forEach(i=>{const r=el("div",{class:"bar"}),n=el("span",{class:"n"},i.n),b=el("i");b.style.width=(i.v/mx*60)+"%";b.style.background=i.c||color||"var(--a)";r.append(n,b,el("span",{},i.t||fmt(i.v)));c.append(r)})}
const K=D.kpis,T=D.tiles;$("meta").textContent=`Period ${D.period.start} to ${D.period.end} · generated ${D.generated_at} · every figure comes from a named, parameterised query`;
[["Invoices",fmt(T.invoices)],["Three-way match rate",T.match_rate_pct==null?"-":T.match_rate_pct+"%"],["Avg monthly median cycle",T.cycle_p50_days==null?"-":T.cycle_p50_days+" d"],
 ["Paid on time",T.on_time_pct==null?"-":T.on_time_pct+"%"],["Supplier concentration (HHI)",f1(T.hhi)],["Open commitments","$"+fmt(T.open_commitment)]].forEach(([l,v])=>{const t=el("div",{class:"tile"});t.append(el("b",{},v),el("span",{},l));$("tiles").append(t)});
line($("c_spend"),D.monthly_spend.map(r=>r.month),[{n:"Actual",c:"var(--a)",v:D.monthly_spend.map(r=>r.actual)},{n:"Budget",c:"var(--b)",v:D.monthly_spend.map(r=>r.budget||null)}]);
line($("c_cycle"),K.invoice_cycle_time.map(r=>r.month),[{n:"Median",c:"var(--a)",v:K.invoice_cycle_time.map(r=>r.p50_days)},{n:"90th percentile",c:"var(--b)",v:K.invoice_cycle_time.map(r=>r.p90_days)}]);
bars($("c_exc"),K.exception_counts.map(r=>({n:r.exception_code,v:r.invoices,t:r.invoices+" ("+r.pct_of_invoices+"%)",c:"var(--"+(r.severity==="error"?"err":r.severity==="warning"?"warn":"info")+")"})));
bars($("c_top"),K.top_suppliers_by_spend.map(r=>({n:r.name,v:r.spend,t:r.share_pct+"%"})));
line($("c_pay"),K.payment_timeliness.map(r=>r.month),[{n:"On time %",c:"var(--ok)",v:K.payment_timeliness.map(r=>r.on_time_pct)}]);
line($("c_match"),K.three_way_match_rate.map(r=>r.month),[{n:"Matched %",c:"var(--a)",v:K.three_way_match_rate.map(r=>r.match_rate_pct)}]);
bars($("c_open"),K.open_commitments.map(r=>({n:r.category,v:r.open_commitment,t:"$"+fmt(r.open_commitment)+" ("+r.open_pos+" POs)"})));
(function(){const a=D.agent,c=$("agent");if(!a||!a.cases){empty(c,"No agent cases recorded yet. Run investigations to populate this panel.");return}
 const rows=[["Cases",a.cases],["Cleared automatically",(a.auto_cleared_rate*100).toFixed(0)+"%"],["Needed a human",(a.needs_human_rate*100).toFixed(0)+"%"],
 ["Human override rate",a.override_rate==null?"n/a":(a.override_rate*100).toFixed(0)+"%"],["Failed",(a.failure_rate*100).toFixed(0)+"%"],["Avg tool calls",f1(a.avg_tool_calls)],["Cost per case","$"+a.cost_per_case.toFixed(4)]];
 const t=el("table");rows.forEach(r=>{const tr=el("tr");tr.append(el("td",{},r[0]),el("td",{class:"num"},String(r[1])));t.append(tr)});c.replaceChildren(t)})();
const codes=[...new Set(D.drill.flatMap(d=>d.exceptions.map(e=>e.code)))].sort();codes.forEach(c=>$("code").append(el("option",{},c)));
function show(d){const box=$("d");box.className="";box.replaceChildren();box.append(el("div",{},d.invoice_id+" · "+d.supplier),el("div",{class:"sub"},`Invoice no. ${d.invoice_number} · PO ${d.po_number||"none"} · ${d.date} · $${fmt(d.total)} · status ${d.status}`));
 d.exceptions.forEach(e=>{const w=el("div",{style:"margin-top:10px"});const p=el("span",{class:"pill "+e.severity},e.severity);w.append(p,el("b",{},"  "+e.code),el("div",{},e.detail),el("div",{class:"sub"},"Next step: "+e.action));box.append(w)})}
function draw(){const q=$("q").value.toLowerCase(),sv_=$("sev").value,cd=$("code").value,tb=$("rows");tb.replaceChildren();let n=0;
 D.drill.forEach(d=>{if(sv_&&!d.exceptions.some(e=>e.severity===sv_))return;if(cd&&!d.exceptions.some(e=>e.code===cd))return;
  if(q&&!(d.invoice_id+" "+d.supplier_id+" "+d.supplier+" "+(d.po_number||"")+" "+d.invoice_number).toLowerCase().includes(q))return;
  if(++n>200)return;const tr=el("tr",{tabindex:"0"});tr.append(el("td",{},d.invoice_id),el("td",{},d.supplier),el("td",{},d.date),el("td",{class:"num"},"$"+fmt(d.total)));
  const td=el("td");d.exceptions.forEach(e=>td.append(el("span",{class:"pill "+e.severity,title:e.detail},e.code+" "),document.createTextNode(" ")));tr.append(td);
  const go=()=>show(d);tr.addEventListener("click",go);tr.addEventListener("keydown",ev=>{if(ev.key==="Enter")go()});tb.append(tr)});
 $("count").textContent=`${Math.min(n,200)} of ${n} shown`+(D.drill_truncated?" (list truncated)":"")}
["q","sev","code"].forEach(i=>$(i).addEventListener("input",draw));draw();
</script></body></html>
"""


def safe_json(obj) -> str:
    return json.dumps(obj, default=str, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render(dataset: dict) -> str:
    return TEMPLATE.replace("__DATA__", safe_json(dataset))
