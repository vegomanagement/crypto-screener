import json
import sys
import time

sys.path.insert(0, "/Users/maxbelov/.hermes/cache/scratch")
from uifix_cdp import Page  # noqa: E402

ex, sym, iv, tag = sys.argv[1:5]
wait = int(sys.argv[5]) if len(sys.argv) > 5 else 40
p = Page()
p.size()
p.nav("/static/liquidity-heatmap.js", 1)
print(p.ev("""(() => { const k='crypto-screener.workspace.v5'; const s=JSON.parse(localStorage.getItem(k)||'null');
 if(!s) return 'no state '+Object.keys(localStorage);
 s.layout='1'; s.active=0; s.panes[0]={...s.panes[0], symbol:%s, interval:%s, exchange:%s};
 s.indicators = s.indicators || {}; 
 localStorage.setItem(k, JSON.stringify(s)); return JSON.stringify(Object.keys(s)); })()""" % (json.dumps(sym), json.dumps(iv), json.dumps(ex))))
p.nav("/ui", 1)
time.sleep(6)
# turn on liquidity via the UI chip
print(p.ev("""(() => { const inputs=[...document.querySelectorAll('input[type=checkbox]')];
 const el = inputs.find(i => (i.closest('label')?.textContent||'').trim().startsWith('Liquidity'));
 if(!el) return 'no liquidity toggle: '+inputs.map(i=>i.closest('label')?.textContent.trim()).join('|');
 if(!el.checked) el.click(); return 'checked='+el.checked; })()"""))
for i in range(wait // 10):
    time.sleep(10)
    print(p.ev("""(() => { const s=document.querySelector('.pane-liquidity-status'); return s ? (s.hidden?'HIDDEN ':'')+s.textContent.slice(0,260) : 'no status'; })()"""))
print(p.shot(f"{tag}.png"))
