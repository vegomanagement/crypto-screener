import json
import sys

sys.path.insert(0, "/Users/maxbelov/.hermes/cache/scratch")
from uifix_cdp import Page  # noqa: E402

p = Page()
print(json.dumps(p.ev("""(() => { const pane=[...window.__workspaceDebug.panes.values()][0];
 const d=pane.liquidityData, m=pane.liquidityModel, cfg=window.__workspaceDebug.state.panes[pane.id];
 if(!d) return {cfg, noData:true, state:pane.liquidityState};
 const L=d.latest; const prices=[...L.bids,...L.asks].map(r=>r[0]);
 const vr=pane.chart.timeScale().getVisibleLogicalRange();
 const bars=pane.chart.timeScale().options().barSpacing;
 const ps=pane.series.priceScale(); const h=pane.host.clientHeight;
 const top=pane.series.coordinateToPrice(0), bot=pane.series.coordinateToPrice(h-30);
 const hgt = m ? m.cells.map(c=>c.height) : [];
 const wid = m ? m.cells.map(c=>c.width) : [];
 return {cfg, frames:d.frames.length, samples:d.samples, latestAt:L.observed_at, ageS:(Date.now()-L.observed_at)/1000,
   bookLo:Math.min(...prices), bookHi:Math.max(...prices), bestBid:L.bids[0], bestAsk:L.asks[0], rows:[L.bids.length,L.asks.length],
   visiblePriceTop:top, visiblePriceBottom:bot, plotH:h, barSpacing:bars, visibleLogical:vr, candles:pane.candles.length,
   step:m&&m.step, cells:m&&m.cells.length, profile:m&&m.profile.length, markers:m?m.cells.filter(c=>c.marker).length:null,
   cellHeightMax:Math.max(...hgt), cellHeightMin:Math.min(...hgt), cellWidthSum: wid.reduce((a,b)=>a+b,0)/Math.max(1,d.frames.length),
   cellsXRange: m&&m.cells.length ? [Math.min(...m.cells.map(c=>c.x)), Math.max(...m.cells.map(c=>c.x+c.width))] : null,
   cellsYRange: m&&m.cells.length ? [Math.min(...m.cells.map(c=>c.y)), Math.max(...m.cells.map(c=>c.y+c.height))] : null,
   settings: window.__workspaceDebug.getIndicatorSettings('liquidity'), dpr: devicePixelRatio};
})()"""), indent=1))
