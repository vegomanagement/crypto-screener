/* Locally collected order-book snapshots; never liquidation or tick history. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WorkspaceLiquidity = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_FRAMES = 360, MAX_LEVELS = 1000, MAX_RECTS = 40000, MIN_ROW_PX = 3;
  function aggregate(frame, step, minimum = 0) {
    const side = name => {
      const bins = new Map();
      (Array.isArray(frame?.[name]) ? frame[name] : []).slice(0, MAX_LEVELS).forEach(row => {
        const price = Number(row?.[0]), qty = Number(row?.[1]);
        if (!(price > 0 && qty > 0 && Number.isFinite(price) && Number.isFinite(qty))) return;
        const ratio = price / step;
        const bucket = Number(((name === 'bids' ? Math.floor(ratio + 1e-9) : Math.ceil(ratio - 1e-9)) * step).toPrecision(14));
        bins.set(bucket, (bins.get(bucket) || 0) + qty);
      });
      return [...bins].map(([price,qty]) => ({price,qty})).filter(row => row.qty >= minimum).sort((a,b) => a.price-b.price);
    };
    return {bids:side('bids'), asks:side('asks')};
  }
  function timeCoordinate(ms, candles, scale, interval) {
    if (!candles.length || !Number.isFinite(ms)) return null;
    const seconds = ms / 1000;
    let lo = 0, hi = candles.length;
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (candles[mid].time < seconds) lo = mid + 1; else hi = mid; }
    const logical = lo === 0 ? (seconds - candles[0].time) / interval : lo === candles.length ? candles.length - 1 + (seconds - candles.at(-1).time) / interval : lo - 1 + (seconds - candles[lo-1].time) / (candles[lo].time - candles[lo-1].time);
    const anchor = Math.round(logical), x = scale.logicalToCoordinate(anchor);
    return x === null || !Number.isFinite(x) ? null : x + (logical - anchor) * scale.options().barSpacing;
  }
  // Price span of `px` CSS pixels near `reference` on the live price scale.
  function pixelPriceSpan(reference, priceToCoordinate, px) {
    if (!(reference > 0) || typeof priceToCoordinate !== 'function') return 0;
    const a = priceToCoordinate(reference), b = priceToCoordinate(reference * 1.001);
    if (a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b) || a === b) return 0;
    return px * reference * 0.001 / Math.abs(b - a);
  }
  // Automatic bucket: 1/80 of the returned book span, but never thinner than
  // MIN_ROW_PX on screen. A 200-row BTC book bucketed by span alone gave
  // 0.04-0.9 px rows (invisible) on 15m-4h charts.
  function automaticStep(frame, minimumStep = 0) {
    const prices = [...(frame?.bids || []), ...(frame?.asks || [])].map(r=>Number(r[0])).filter(p=>p>0 && Number.isFinite(p));
    if (!prices.length) return 1;
    const span = Math.max(...prices)-Math.min(...prices);
    const raw = Math.max(span > 0 ? span / 80 : prices[0] * 0.0001, minimumStep > 0 && Number.isFinite(minimumStep) ? minimumStep : 0);
    const power = 10 ** Math.floor(Math.log10(raw));
    return Math.max(Number.MIN_VALUE, Math.ceil(raw / power - 1e-9) * power);
  }
  function color(qty, maximum, settings = {}) {
    const stops = [settings.lowColor || '#632ca6', settings.midColor || '#ed4eaa', settings.highColor || '#ffe66d'];
    const t = Math.max(0, Math.min(1, Math.log1p(qty) / Math.log1p(maximum || 1))) * 2;
    const index = Math.min(1, Math.floor(t)), f = t-index;
    const rgb = hex => [1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));
    const a=rgb(stops[index]), b=rgb(stops[index+1]);
    return `rgba(${a.map((v,i)=>Math.round(v+(b[i]-v)*f)).join(',')},${Math.max(0,Math.min(0.6, settings.opacity ?? 0.3))})`;
  }
  function geometry(data, settings, view) {
    const {candles,scale,interval,priceToCoordinate,width,height,now} = view;
    const frames = [...new Map((data.frames || []).filter(f=>Number.isFinite(f?.observed_at)).map(f=>[f.observed_at,f])).values()].sort((a,b)=>a.observed_at-b.observed_at).slice(-MAX_FRAMES);
    const book = data.latest || frames.at(-1);
    const reference = Number(book?.bids?.[0]?.[0]) || Number(book?.asks?.[0]?.[0]) || 0;
    // A manual step thinner than one CSS pixel is raised to one pixel (disclosed via stepRaised).
    const onePixel = pixelPriceSpan(reference, priceToCoordinate, 1);
    const manual = settings.step > 0 ? Math.max(settings.step, onePixel) : 0;
    const step = manual || automaticStep(book, pixelPriceSpan(reference, priceToCoordinate, MIN_ROW_PX));
    const period = Math.min(20000, Math.max(1, Number(data.sample_period_ms) || 10000));
    const cells = [], profile = [];
    const x = ms => timeCoordinate(ms,candles,scale,interval);
    const rect = (row,side,x1,x2,time) => {
      const y1=priceToCoordinate(row.price-step/2),y2=priceToCoordinate(row.price+step/2);
      if ([x1,x2,y1,y2].some(v=>v === null || !Number.isFinite(v))) return null;
      const left=Math.max(0,x1),right=Math.min(width,x2),top=Math.max(0,Math.min(y1,y2)),bottom=Math.min(height,Math.max(y1,y2));
      if (right<=left || bottom<=top) return null;
      return {x:left,y:top,width:right-left,height:bottom-top,price:row.price,qty:row.qty,side,time};
    };
    frames.forEach((frame,index) => {
      const next=frames[index+1]?.observed_at ?? now;
      let end=Math.min(frame.observed_at+period,next,now);
      (data.gaps || []).forEach(g=>{if(g.start>frame.observed_at && g.start<end) end=g.start; else if(g.start<=frame.observed_at && g.end>frame.observed_at) end=frame.observed_at;});
      if (end<=frame.observed_at) return;
      const bins=aggregate(frame,step,settings.minQty || 0);
      ['bids','asks'].forEach(side=>bins[side].forEach(row=>{
        if(cells.length>=MAX_RECTS) return;
        const cell=rect(row,side,x(frame.observed_at),x(end),frame.observed_at);
        if(cell) {
          cell.endTime=end; cell.actualWidth=cell.width;
          // A subpixel glyph marks a real observation, not inferred persistence.
          const minimum=1/(view.pixelRatio || 1);
          cell.marker=cell.width<minimum;
          if(cell.marker) cell.width=Math.min(minimum,width-cell.x);
          cells.push(cell);
        }
      }));
    });
    const latest=data.latest;
    if (latest && Number.isFinite(latest.observed_at)) {
      const bins=aggregate(latest,step,settings.minQty || 0), max=Math.max(1,...bins.bids.concat(bins.asks).map(r=>r.qty));
      // Profile goes into the empty right-offset space after the last candle, so it
      // no longer covers the newest heatmap columns; old right-edge slot if too narrow.
      const lastCenter=candles.length ? scale.logicalToCoordinate(candles.length-1) : null;
      const after=Number.isFinite(lastCenter) ? lastCenter+scale.options().barSpacing/2+2 : Infinity;
      const left=width-after>=24 ? Math.max(0,after) : Math.max(0,width-82), span=Math.min(80,width-left);
      ['bids','asks'].forEach(side=>bins[side].forEach(row=>{
        const cell=rect(row,side,left,left+span*row.qty/max,latest.observed_at);
        if(cell) profile.push(cell);
      }));
    }
    const covered=cells.concat(profile);
    const bookPx=covered.length ? Math.max(...covered.map(c=>c.y+c.height))-Math.min(...covered.map(c=>c.y)) : 0;
    return {cells,profile,step,stepRaised:settings.step>0 && step>settings.step,bookPx,maximum:Math.max(1,...cells.map(c=>c.qty)),truncated:cells.length>=MAX_RECTS};
  }
  function hitTest(model,x,y) {
    const contains=c=>x>=c.x && x<c.x+c.width && y>=c.y && y<c.y+c.height;
    return model.profile.find(contains) || model.cells.find(contains) || null;
  }
  class HeatmapPrimitive {
    constructor(getModel, getSettings) {
      this.getModel=getModel; this.getSettings=getSettings;
      this.view={zOrder:()=> 'bottom',renderer:()=>({draw:target=>target.useMediaCoordinateSpace(scope=>this.draw(scope.context,scope.mediaSize))})};
    }
    attached({requestUpdate}) {this.requestUpdate=requestUpdate;requestUpdate();}
    detached() {this.requestUpdate=null;}
    paneViews() {return [this.view];}
    draw(context,size) {
      const model=this.getModel(size),settings=this.getSettings(); if(!model) return;
      context.save(); context.beginPath();context.rect(0,0,size.width,size.height);context.clip();
      model.cells.forEach(cell=>{context.fillStyle=color(cell.qty,model.maximum,settings);context.fillRect(cell.x,cell.y,cell.width,cell.height);});
      model.profile.forEach(cell=>{context.fillStyle=cell.side==='bids'?'rgba(0,192,135,0.55)':'rgba(239,102,112,0.55)';context.fillRect(cell.x,cell.y,cell.width,cell.height);});
      context.restore();
    }
  }
  class CollectionSession {
    constructor({owner,fetch,visible,range,onData,onStatus,now=Date.now}) {
      Object.assign(this,{owner,fetch,visible,range,onData,onStatus,now});
      this.generation=0; this.config=null; this.lease=null; this.queue=Promise.resolve(); this.lastWatch=-Infinity; this.lastRead=-Infinity;
    }
    post(action,config) {
      // Serialize mutations per owner, including a late watch before its unwatch.
      const operation=async()=>{
        const response=await this.fetch(`/api/liquidity/${action}`,{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify({...config,owner:this.owner}),keepalive:true});
        const data=await response.json();
        if(!response.ok) throw new Error(typeof data.error==='string'?data.error:data.error?.message || `Liquidity ${action} unavailable (${response.status})`);
        return data;
      };
      const result=this.queue.then(operation); this.queue=result.catch(()=>{}); return result;
    }
    flush() { return this.queue; }
    stop() {
      this.generation++; this.controller?.abort(); this.controller=null; this.busy=null;
      if(this.lease) { this.post('unwatch',this.lease).catch(()=>{}); this.lease=null; }
      this.lastWatch=-Infinity; this.lastRead=-Infinity;
    }
    configure(config) {
      if(JSON.stringify(config)===JSON.stringify(this.config)) return;
      this.stop(); this.config=config ? {...config} : null;
    }
    invalidate() { this.lastRead=-Infinity; this.generation++; this.controller?.abort(); this.controller=null; this.busy=null; }
    async tick() {
      if(this.disposed || !this.config) return;
      if(!this.visible()) { this.stop(); this.onStatus({collecting:false,paused:true}); return; }
      if(this.busy) return;
      const generation=this.generation, config={...this.config}, flight={}; this.busy=flight;
      const current=()=>!this.disposed && generation===this.generation && this.visible();
      try {
        if(this.now()-this.lastWatch>=30000) {
          this.lease=config; this.lastWatch=this.now();
          const status=await this.post('watch',config); if(!current()) return; this.onStatus(status);
        }
        if(!current() || this.now()-this.lastRead<10000) return;
        this.lastRead=this.now();
        const raw=this.range(),end=Math.min(this.now(),Math.floor(raw.end_ms)),start=Math.max(end-7*86400000,Math.floor(raw.start_ms));
        const controller=new AbortController();this.controller=controller;
        const timeout=setTimeout(()=>controller.abort(),20000);
        try {
          const query=new URLSearchParams({...config,start_ms:String(Math.min(start,end-1)),end_ms:String(end),max_frames:String(MAX_FRAMES)});
          const response=await this.fetch(`/api/liquidity/history?${query}`,{signal:controller.signal,cache:'no-store',headers:{Accept:'application/json'}});
          const data=await response.json(); if(!current() || controller.signal.aborted) return;
          if(!response.ok) throw new Error(typeof data.error==='string'?data.error:data.error?.message || `Liquidity history unavailable (${response.status})`);
          if(data.exchange!==config.exchange || data.symbol!==config.symbol || !Array.isArray(data.frames)) throw new Error('Liquidity response market mismatch');
          this.onData(data); if(data.status) this.onStatus(data.status);
        } finally {clearTimeout(timeout); if(this.controller===controller) this.controller=null;}
      } catch(error) {if(current() && error.name!=='AbortError') this.onStatus({collecting:false,error:error.message});}
      finally {if(this.busy===flight) this.busy=null;}
    }
    dispose() {this.stop();this.disposed=true;this.config=null;}
  }
  return {aggregate, timeCoordinate, automaticStep, pixelPriceSpan, color, geometry, hitTest, HeatmapPrimitive, CollectionSession, MAX_FRAMES, MAX_LEVELS, MAX_RECTS, MIN_ROW_PX};
});
