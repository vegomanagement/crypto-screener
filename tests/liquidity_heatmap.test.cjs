'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const file = path.join(__dirname, '../static/liquidity-heatmap.js');
const api = () => fs.existsSync(file) ? require(file) : {};

test('price bins floor bids, ceil asks, preserve native quantities within one snapshot only', () => {
  assert.equal(typeof api().aggregate, 'function', 'snapshot aggregation exists');
  const frame = {observed_at:1000, bids:[[99.9,2],[99.8,3],[99,0],['bad',9]], asks:[[100.1,4],[100.2,6]]};
  const b = api().aggregate(frame, 1, 0);
  assert.deepEqual(b.bids, [{price:99,qty:5}]);
  assert.deepEqual(b.asks, [{price:101,qty:10}]);
  assert.equal(api().aggregate(frame,1,6).bids.length, 0);
  assert.equal(api().aggregate({...frame,observed_at:2000},1,0).bids[0].qty,5, 'never sum repeated snapshots across time');
  assert.deepEqual(api().aggregate({bids:[[0.3,2]],asks:[[0.3,3]]},0.1,0), {bids:[{price:0.3,qty:2}],asks:[{price:0.3,qty:3}]}, 'decimal exact bucket boundaries do not slip');
});

test('geometry maps exact milliseconds through integer native anchors and leaves sampling gaps transparent', () => {
  assert.equal(typeof api().geometry, 'function');
  const scale = {options:()=>({barSpacing:60}),logicalToCoordinate:i=>{assert.ok(Number.isInteger(i));return 100+i*60;}};
  const candles = [{time:100},{time:160},{time:220}];
  assert.equal(api().timeCoordinate(135500,candles,scale,60),135.5);
  assert.equal(api().timeCoordinate(235000,candles,scale,60),235);
  const frames = [{observed_at:135500,bids:[[99.5,7]],asks:[]},{observed_at:145500,bids:[[99.5,9]],asks:[]},{observed_at:185500,bids:[[99.5,2]],asks:[]}];
  const model=api().geometry({frames,latest:frames[2],sample_period_ms:10000}, {step:1,minQty:0,opacity:0.3}, {candles,scale,interval:60,priceToCoordinate:p=>200-p,width:400,height:200,now:200000});
  assert.equal(model.cells[0].x,135.5);
  assert.equal(model.cells[0].width,10);
  assert.equal(model.cells[1].width,10,'do not stretch snapshot through 40s gap');
  assert.deepEqual(model.cells.map(c=>c.qty),[7,9,2]);
  assert.equal(model.profile[0].qty,2,'profile is latest only');
  assert.equal(model.profile[0].x,318,'right profile stays inside plot');
  assert.equal(api().geometry({frames:[]}, {}, {candles,scale,interval:60,priceToCoordinate:p=>p,width:400,height:200,now:200000}).cells.length,0);
  assert.equal(api().hitTest(model,170,100),null,'no fabricated data in gap');
  assert.equal(api().hitTest(model,140,101).qty,7);
  const clipped=api().geometry({frames}, {step:1,minQty:8}, {candles,scale,interval:60,priceToCoordinate:p=>200-p,width:145,height:200,now:200000});
  assert.equal(clipped.cells.length,0,'outside view/quantity filter is respected');
});

test('subpixel samples are one-device-pixel markers with explicit actual time windows, never duration interpolation',()=>{
  const candles=[{time:100},{time:14500}],scale={options:()=>({barSpacing:60}),logicalToCoordinate:i=>100+i*60};
  const model=api().geometry({frames:[{observed_at:135500,bids:[[99,7]],asks:[]}],sample_period_ms:10000},{step:1},{candles,scale,interval:14400,priceToCoordinate:p=>200-p,width:400,height:200,now:200000,pixelRatio:2});
  assert.equal(model.cells[0].width,0.5);
  assert.ok(model.cells[0].actualWidth<0.5);
  assert.deepEqual([model.cells[0].time,model.cells[0].endTime],[135500,145500]);
  assert.equal(model.cells[0].marker,true);
});

function sessionHarness() {
  const calls=[], paints=[]; let now=100000, visible=true, config;
  const pending=[];
  const fetch=async (url,options={})=>{calls.push({url,options});if(url.startsWith('/api/liquidity/history') && pending.length) return pending.shift()(url,options);return {ok:true,json:async()=>url.startsWith('/api/liquidity/history')?{exchange:config.exchange,symbol:config.symbol,frames:[],latest:null}: {collecting:true}};};
  assert.equal(typeof api().CollectionSession,'function');
  const session = new (api().CollectionSession)({owner:'browser:pane0',fetch,now:()=>now,visible:()=>visible,range:()=>({start_ms:0,end_ms:now}),onData:d=>paints.push(d),onStatus:()=>{}});
  return {session,calls,paints,pending,setConfig:c=>{config=c;session.configure(c);},advance:n=>now+=n,visible:v=>visible=v};
}
test('collection leases are opt-in, pane-owned, renew at 30s, read at 10s and pause/unwatch invisibility',async()=>{
  const h=sessionHarness();
  await h.session.tick(); assert.equal(h.calls.length,0);
  h.setConfig({exchange:'bybit',symbol:'BTCUSDT'}); await h.session.tick();
  assert.equal(h.calls.filter(c=>c.url.endsWith('/watch')).length,1);
  assert.deepEqual(JSON.parse(h.calls[0].options.body),{exchange:'bybit',symbol:'BTCUSDT',owner:'browser:pane0'});
  h.advance(10000);await h.session.tick(); assert.equal(h.paints.length,2);
  h.advance(20000);await h.session.tick(); assert.equal(h.calls.filter(c=>c.url.endsWith('/watch')).length,2);
  h.visible(false);await h.session.tick();await h.session.flush();
  assert.equal(h.calls.at(-1).url,'/api/liquidity/unwatch');assert.equal(h.calls.at(-1).options.keepalive,true);
  const count=h.calls.length;h.advance(40000);await h.session.tick();assert.equal(h.calls.length,count);
  h.visible(true);await h.session.tick();assert.equal(h.calls.filter(c=>c.url.endsWith('/watch')).length,3);
  h.session.dispose();await h.session.flush();assert.equal(h.calls.at(-1).url,'/api/liquidity/unwatch');
});
test('symbol switch aborts history; even transports ignoring cancellation cannot paint stale payload',async()=>{
  const h=sessionHarness();let release,signal;
  h.pending.push((url,options)=>new Promise(resolve=>{signal=options.signal;release=()=>resolve({ok:true,json:async()=>({exchange:'bybit',symbol:'BTCUSDT',frames:[{observed_at:1}],latest:null})});}));
  h.setConfig({exchange:'bybit',symbol:'BTCUSDT'});const old=h.session.tick();
  while(!release) await new Promise(r=>setImmediate(r));
  h.setConfig({exchange:'binance',symbol:'ETHUSDT'});assert.equal(signal.aborted,true);
  await h.session.tick();release();await old;await h.session.flush();
  assert.equal(h.paints.some(d=>d.symbol==='BTCUSDT'),false);
  assert.equal(h.paints.at(-1).symbol,'ETHUSDT');
  const mutations=h.calls.filter(c=>!c.url.includes('/history')).map(c=>[c.url,JSON.parse(c.options.body).symbol]);
  assert.deepEqual(mutations,[['/api/liquidity/watch','BTCUSDT'],['/api/liquidity/unwatch','BTCUSDT'],['/api/liquidity/watch','ETHUSDT']]);
});

test('browser: local depth primitive behind native candles, opt-in leases, gaps, profile and lifecycle', {skip:!process.env.WORKSPACE_PLAYWRIGHT},async()=>{
  const {chromium}=require(process.env.WORKSPACE_PLAYWRIGHT);
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1500,height:950}}),requests=[],errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.routeWebSocket(/wss:/,s=>s.close());
    const base=Math.floor(Date.now()/60000)*60000-20*60000;
    let empty=false,delayBTC=false;
    await page.addInitScript(()=>{
      localStorage.setItem('crypto-screener.workspace.v4',JSON.stringify({version:4,layout:'2cols',panes:[{exchange:'bybit',symbol:'BTCUSDT',interval:'1'},{exchange:'bybit',symbol:'BTCUSDT',interval:'1'}],fvg:false,ema:{7:false,14:false,28:false,200:false}}));
      const original=fetch; window.fetch=(url,options)=>original(url,{...options,signal:undefined}); // hostile cancellation transport
    });
    await page.route('http://liquidity.test/**',async route=>{
      const req=route.request(),url=new URL(req.url());requests.push({url:url.pathname,query:url.search,body:req.postDataJSON?.(),method:req.method()});
      if(url.pathname==='/api/chart') return route.fulfill({json:{exchange:url.searchParams.get('exchange'),source:url.searchParams.get('exchange'),closed_only:true,candles:Array.from({length:20},(_,i)=>({ts:base+i*60000,o:100,h:105,l:95,c:102,v:10})),zones:[]}});
      if(url.pathname==='/api/chart/intervals') return route.fulfill({json:{intervals:[{value:'1',label:'1m'}]}});
      if(url.pathname==='/api/markets') return route.fulfill({json:{exchange:'bybit',markets:[{exchange:'bybit',symbol:'ETHUSDT',base:'ETH',quote:'USDT'}],total:1}});
      if(url.pathname==='/api/liquidity/watch' || url.pathname==='/api/liquidity/unwatch') return route.fulfill({json:{...req.postDataJSON(),collecting:url.pathname.endsWith('/watch'),period_seconds:10,lease_seconds:90}});
      if(url.pathname==='/api/liquidity/history') {
        const symbol=url.searchParams.get('symbol'),exchange=url.searchParams.get('exchange');
        if(delayBTC && symbol==='BTCUSDT') await new Promise(r=>setTimeout(r,250));
        const frames=empty?[]:[0,1,5].map(i=>({observed_at:base+18*60000+i*10000,bids:[[100,10],[98,3]],asks:[[103,8],[104,2]]}));
        return route.fulfill({json:{exchange,symbol,frames,latest:frames.at(-1)||null,coverage_start:frames[0]?.observed_at||null,coverage_end:frames.at(-1)?.observed_at||null,sample_period_ms:10000,gaps:[{start:base+18*60000+20000,end:base+18*60000+50000}],warnings:['Native quantity unit unknown; depth limited to 20 rows per side'],status:{collecting:true}}});
      }
      if(url.pathname.startsWith('/api/')) return route.fulfill({status:404,json:{}});
      return route.fulfill({path:path.join(__dirname,'..',url.pathname==='/'?'templates/workspace.html':url.pathname.slice(1))});
    });
    await page.goto('http://liquidity.test/');
    await page.waitForFunction(()=>window.__workspaceDebug?.panes.get(0)?.candles.length===20);
    const toggle=page.locator('[data-indicator-toggle="liquidity"]');
    assert.equal(await toggle.isChecked(),false);
    assert.equal(requests.filter(r=>r.url.startsWith('/api/liquidity/')).length,0,'OFF is completely silent');
    await toggle.check();
    await page.waitForFunction(()=>window.__workspaceDebug.panes.get(0).liquidityModel?.cells.length>0);
    const watches=requests.filter(r=>r.url==='/api/liquidity/watch');
    assert.equal(watches.length,2,'each actual pane watches, not the watchlist');
    assert.notEqual(watches[0].body.owner,watches[1].body.owner,'same market distinct pane lease owners');
    assert.equal(await page.locator('.pane-liquidity-status').count(),2);
    assert.match(await page.locator('.pane-liquidity-status').first().innerText(),/Collecting.*history from/);
    assert.match(await page.locator('.pane-liquidity-status').first().innerText(),/no history before collection.*10s.*not tick.*20 rows/i);
    await page.evaluate(()=>{
      const p=window.__workspaceDebug.panes.get(0);
      p.chart.applyOptions({grid:{vertLines:{visible:false},horzLines:{visible:false}}});
      p.chart.timeScale().setVisibleLogicalRange({from:13,to:21});p.chart.timeScale().applyOptions({barSpacing:60});
      window.__workspaceDebug.setIndicatorSettings('liquidity',{step:1});p.applyIndicators();
    });
    await page.waitForTimeout(200);
    const pixels=await page.evaluate(base=>{
      const p=window.__workspaceDebug.panes.get(0),m=p.liquidityModel,ratio=devicePixelRatio||1;
      const x=ms=>WorkspaceLiquidity.timeCoordinate(ms,p.candles,p.chart.timeScale(),60),y=p.series.priceToCoordinate(98);
      const canvases=[...p.host.querySelectorAll('canvas')];
      const sample=(xc,yc)=>canvases.map(c=>[...c.getContext('2d').getImageData(Math.round(xc*ratio),Math.round(yc*ratio),1,1).data]);
      // The snapshot crosses candle 18's native up-body: the candle must remain its EXACT original color.
      const candle=sample(x(base+18*60000),p.series.priceToCoordinate(101));
      const heat=sample(x(base+18*60000+15000),y);
      const gap=sample(x(base+18*60000+35000),y);
      const profile=m.profile.find(c=>c.side==='bids' && c.price===100);
      return {candle,heat,gap,profile:sample(profile.x+profile.width/2,profile.y+profile.height/2),cells:m.cells.length,layer:p.liquidityLayer,spacing:p.chart.timeScale().options().barSpacing};
    },base);
    console.log('Liquidity native canvas evidence:',JSON.stringify(pixels));
    if(process.env.WORKSPACE_SCREENSHOT) {await page.screenshot({path:process.env.WORKSPACE_SCREENSHOT.replace('.png','-liquidity.png')});fs.writeFileSync(process.env.WORKSPACE_SCREENSHOT.replace('.png','-liquidity.json'),JSON.stringify(pixels,null,2));}
    assert.equal(pixels.layer,'below-candles');
    assert.ok(pixels.candle.some(([r,g,b,a])=>r===0 && g===192 && b===135 && a===255),'native candle raster unchanged above primitive');
    assert.ok(pixels.heat.some(([r,g,b,a])=>r>50 && b>20 && a>0),'real heat cells paint colored pixels');
    assert.ok(pixels.gap.some(([r,g,b,a])=>r===16 && g===20 && b===28 && a===255),'sample gap remains chart background');
    assert.ok(pixels.profile.some(([r,g,b,a])=>g>60 && g>r && a>0),'current bid profile is green inside plot');
    if(process.env.WORKSPACE_SCREENSHOT) {await page.screenshot({path:process.env.WORKSPACE_SCREENSHOT.replace('.png','-liquidity.png')});fs.writeFileSync(process.env.WORKSPACE_SCREENSHOT.replace('.png','-liquidity.json'),JSON.stringify(pixels,null,2));}
    await page.locator('.indicator-settings-btn[data-indicator="liquidity"]').click();
    await page.locator('[data-key="minQty"]').fill('999');await page.locator('.indicator-settings-apply').click();
    await page.waitForFunction(()=>window.__workspaceDebug.panes.get(0).liquidityModel.cells.length===0);
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('crypto-screener.workspace.v4')).indicatorSettings.liquidity.minQty),999);
    await page.locator('.indicator-settings-reset').click();await page.keyboard.press('Escape');
    empty=true;await page.evaluate(()=>{const p=window.__workspaceDebug.panes.get(0);p.liquiditySession.invalidate();return p.liquiditySession.tick();});
    await page.waitForFunction(()=>window.__workspaceDebug.panes.get(0).liquidityModel.cells.length===0);
    assert.match(await page.locator('.pane-liquidity-status').first().innerText(),/No history yet/);
    empty=false;delayBTC=true;
    await page.evaluate(()=>{const p=window.__workspaceDebug.panes.get(0);p.liquiditySession.invalidate();p.liquiditySession.tick();});
    await page.locator('.watchlist-row[data-symbol="ETHUSDT"]').click();await page.waitForTimeout(400);
    assert.equal(await page.evaluate(()=>window.__workspaceDebug.panes.get(0).liquidityData.symbol),'ETHUSDT','old BTC history cannot repaint new symbol');
    assert.ok(requests.some(r=>r.url==='/api/liquidity/unwatch' && r.body.symbol==='BTCUSDT'));
    const widths=await page.evaluate(()=>{const p=window.__workspaceDebug.panes.get(0);return [p.host.clientWidth,p.liquidityModel.profile[0]?.x];});
    await page.setViewportSize({width:1200,height:850});await page.waitForTimeout(150);
    assert.notDeepEqual(await page.evaluate(()=>{const p=window.__workspaceDebug.panes.get(0);return [p.host.clientWidth,p.liquidityModel.profile[0]?.x];}),widths,'resize rebuilds exact live geometry');
    await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));});
    await page.waitForTimeout(100);
    const hiddenReads=requests.filter(r=>r.url==='/api/liquidity/history').length;
    await page.evaluate(()=>Promise.all([...window.__workspaceDebug.panes.values()].map(p=>p.liquiditySession.tick())));
    assert.equal(requests.filter(r=>r.url==='/api/liquidity/history').length,hiddenReads);
    await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));});
    await toggle.uncheck();await page.waitForTimeout(100);
    assert.equal(await page.evaluate(()=>window.__workspaceDebug.panes.get(0).liquidityData),null);
    await page.locator('#layout-select').selectOption('1');
    await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));
    await page.waitForTimeout(100);assert.deepEqual(errors,[]);
  } finally {await browser.close();}
});
