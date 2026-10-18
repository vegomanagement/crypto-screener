import json, sys, time
sys.path.insert(0, "/Users/maxbelov/.hermes/cache/scratch")
from uifix_cdp import Page
tag = sys.argv[1]
import os; W,H=map(int,os.environ.get("SZ","1600x1000").split("x")); p = Page(); p.size(W, H); tag = tag + ("" if (W,H)==(1600,1000) else f"-{W}x{H}")
out = {}
for layout in ["1", "4grid", "6grid"]:
    p.nav("/static/liquidity-heatmap.js", 1)
    p.ev("(() => { const k='crypto-screener.workspace.v5'; const s=JSON.parse(localStorage.getItem(k)); s.layout=%s; s.sidebar=true; s.indicatorEnabled.liquidity=false; localStorage.setItem(k, JSON.stringify(s)); })()" % json.dumps(layout))
    p.nav("/ui", 7)
    m = p.ev("""(() => { const a=document.querySelector('#watchlist'), r=document.querySelector('#watchlist-rows'); const rows=[...r.querySelectorAll('.watchlist-row')];
      const rr=r.getBoundingClientRect(), ar=a.getBoundingClientRect(); const rowH=rows[0]?rows[0].getBoundingClientRect().height:0;
      return {asideH:Math.round(ar.height), rowsH:Math.round(rr.height), rowsTop:Math.round(rr.top-ar.top), rowsScrollH:r.scrollHeight, rowCount:rows.length,
        visibleRows: rowH ? Math.floor(rr.height/rowH) : 0, rowH, asideScrolls: a.scrollHeight>a.clientHeight+1,
        children:[...a.children].filter(c=>c.offsetParent!==null).map(c=>c.id||c.className),
        analystPanel: !!document.querySelector('#analyst-panel'), analystText: /Hermes analyst|Open full analysis/.test(a.textContent),
        dashboard: !!document.querySelector('#analysis-dashboard .ad-section, #analysis-dashboard *'),
        jumpTop: !!document.querySelector('.topbar .analysis-jump'), jumpFooter: !!document.querySelector('.workspace-footer .footer-analysis')}; })()""")
    out[layout] = m
    print(layout, m)
    p.shot(f"{tag}-sidebar-{layout}.png")
# toggle
t = p.ev("""(async () => { const b=document.querySelector('#watchlist-toggle'), a=document.querySelector('#watchlist'); const res=[];
  const snap=()=>({expanded:b.getAttribute('aria-expanded'), asideDisplay:getComputedStyle(a).display, gridW:Math.round(document.querySelector('#chart-grid').getBoundingClientRect().width), saved:JSON.parse(localStorage.getItem('crypto-screener.workspace.v5')).sidebar});
  res.push(snap()); b.click(); await new Promise(r=>setTimeout(r,400)); res.push(snap()); b.click(); await new Promise(r=>setTimeout(r,400)); res.push(snap()); return res; })()""")
print("toggle", t); out["toggle"] = t
p.ev("document.querySelector('#watchlist-toggle').click()"); time.sleep(0.5); p.shot(f"{tag}-sidebar-hidden.png"); p.ev("document.querySelector('#watchlist-toggle').click()")
json.dump(out, open(f"/Users/maxbelov/Projects/wt/ui-fixes/docs/evidence/ui-fixes/{tag}-sidebar.json", "w"), indent=1)
