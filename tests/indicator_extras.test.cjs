'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const pluginPath = path.join(__dirname, '../static/indicator-extras.js');
const extras = fs.existsSync(pluginPath) ? require(pluginPath) : {};
const epoch = text => Date.parse(text) / 1000;
const bars = (n, start = epoch('2026-01-01T00:00:00Z'), step = 14400, price = 100) =>
  Array.from({length:n}, (_, i) => ({time:start+i*step,open:price,high:price,low:price,close:price,volume:1}));
const near = (a,b,tol=1e-9) => assert.ok(Math.abs(a-b) <= tol*Math.max(1,Math.abs(b)), `${a} != ${b}`);

test('official twoPole source-replacement initialization and log TR previous-close leap', () => {
  const x = [2,8,3,9], length = 10;
  const a = Math.exp(-Math.SQRT2*Math.PI/length), c2=2*a*Math.cos(Math.SQRT2*Math.PI/length), c3=-a*a, c1=1-c2-c3;
  const out = extras.twoPole(x,length);
  near(out[0],2);
  near(out[1],c1*5+c2*out[0]+c3*2);
  near(out[2],c1*5.5+c2*out[1]+c3*out[0]);
  const leap = bars(2); leap[1] = {...leap[1],open:200,high:220,low:180,close:200};
  near(extras.logTrueRange(leap)[1],Math.log(220)-Math.log(100));
});

test('UTC daily VWAP resets on calendar boundaries, weights volume and omits zero-volume start', () => {
  assert.equal(typeof extras.computeVWAP,'function');
  const input = bars(13);
  input.forEach((c,i) => { const p=100+i; c.open=c.high=c.low=c.close=p; c.volume=i%6===0?0:2; });
  const out = extras.computeVWAP(input, {anchor:'day'});
  assert.deepEqual(out[0],{time:input[0].time});
  near(out[2].value,101.5);
  assert.deepEqual(out[6],{time:input[6].time});
  near(out[7].value,107);
  input.forEach(Object.freeze); Object.freeze(input);
  assert.doesNotThrow(() => extras.computeVWAP(input,{anchor:'day'}));
});

test('EMA bias uses warmed SMA-seeded EMAs and reports up/down/mixed/insufficient', () => {
  assert.equal(typeof extras.computeemaBias,'function');
  const input=bars(80);
  input.forEach((c,i)=>{c.open=c.high=c.low=c.close=100+i;});
  assert.equal(extras.computeemaBias(input),'up');
  input.forEach((c,i)=>{c.open=c.high=c.low=c.close=200-i;});
  assert.equal(extras.computeemaBias(input),'down');
  assert.equal(extras.computeemaBias(bars(80)),'mixed');
  assert.equal(extras.computeemaBias(bars(27)),'insufficient');
  assert.equal(extras.computeemaBias(bars(4),[2,3,5]),'insufficient');
  assert.throws(()=>extras.computeemaBias(input,[7,14,0]),/integer/);
});

test('UMD exports only the two VWAP definitions (old split overlays removed), no DOM or network dependencies', () => {
  assert.ok(Array.isArray(extras.definitions));
  const expected=['vwap-daily','vwap-weekly'];
  for (const gone of ['computeSession','computePreviousLevel']) assert.equal(gone in extras,false,gone);
  assert.deepEqual(extras.definitions.map(d=>d.id),expected);
  for (const d of extras.definitions) {
    assert.equal(d.defaultEnabled,false);
    assert.ok(['line','band'].includes(d.kind));
    assert.ok(d.settingsSchema.some(f=>f.key==='color'));
    assert.equal(typeof d.compute,'function');
    assert.equal(typeof d.warnings,'function');
  }
  const sandbox={Intl}; vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(pluginPath,'utf8'),sandbox);
  assert.equal(sandbox.WorkspaceIndicatorExtras.definitions.length,2);
  const input=bars(210); input.forEach(Object.freeze); Object.freeze(input);
  for (const d of extras.definitions) assert.doesNotThrow(()=>d.compute(input, Object.freeze({})));
});

test('weekly anchors are Monday UTC, partial prefix and oversized source bars warn honestly', () => {
  assert.equal(typeof extras.getWarnings,'function');
  const input=bars(15,epoch('2026-01-04T00:00:00Z'),86400);
  input.forEach((c,i)=>{c.open=c.high=c.low=c.close=100+i;});
  const out=extras.computeVWAP(input,{anchor:'week'});
  assert.equal(out[0].value,undefined);
  near(out[1].value,101); near(out[7].value,104); near(out[8].value,108);
  assert.deepEqual(extras.computeVWAP(bars(3,epoch('2026-01-05T00:00:00Z'),604800),{anchor:'day'}),[]);
  assert.ok(extras.getWarnings('vwap-daily',bars(3,epoch('2026-01-05T00:00:00Z'),604800)).some(x=>/boundary|coarse/i.test(x)));
  assert.ok(extras.getWarnings('vwap-weekly',input).some(x=>/partial/i.test(x)));
  assert.deepEqual(extras.getWarnings('turtle-upper',bars(199)),['Unknown indicator: turtle-upper']);
});

test('Turtle rejects invalid settings/inputs and never emits nonfinite exponential prices', () => {
  const input=bars(210);
  for (const length of [0,-1,1.5,1001,Infinity,NaN]) assert.throws(()=>extras.computeTurtleZone(input,{length}),/integer/);
  for (const settings of [{inner:10,outer:5},{inner:-1},{outer:Infinity},{source:'bad'}]) assert.throws(()=>extras.computeTurtleZone(input,settings),/amplitude|source/);
  for (const value of [0,-1,NaN,Infinity]) {
    const bad=input.map(c=>({...c})); bad[0].high=value;
    assert.throws(()=>extras.computeTurtleZone(bad),/positive|OHLC/);
  }
  const explosive=input.map(c=>({...c,high:1e300,low:1e-300}));
  assert.throws(()=>extras.computeTurtleZone(explosive),/overflow|underflow/);
  for (const source of ['hlc3','open','high','low','close','hl2','ohlc4']) assert.doesNotThrow(()=>extras.computeTurtleZone(input,{source}));
  assert.equal(extras.computeTurtleZone(bars(250),{length:300}).upper.upper.length,0);
});

test('VWAP does not bridge incomplete starts/gaps and remains finite at huge volume/price', () => {
  const input=bars(18).filter((_,i)=>i!==0&&i!==8);
  const out=extras.computeVWAP(input,{anchor:'day',intervalSeconds:14400});
  const map=new Map(out.map(p=>[p.time,p.value]));
  assert.equal(map.get(epoch('2026-01-01T04:00:00Z')),undefined);
  assert.equal(map.get(epoch('2026-01-02T00:00:00Z')),100);
  assert.equal(map.get(epoch('2026-01-02T12:00:00Z')),undefined);
  assert.equal(map.get(epoch('2026-01-03T00:00:00Z')),100);
  const huge=bars(6,epoch('2026-01-01T00:00:00Z'),14400,1e300).map(c=>({...c,volume:1e308}));
  extras.computeVWAP(huge).forEach(p=>near(p.value,1e300));
});

test('calendar warning detects wholly absent UTC periods, not just missing bars inside a group', () => {
  const input=[...bars(12),...bars(6,epoch('2026-01-04T00:00:00Z'))];
  assert.ok(extras.getWarnings('vwap-daily',input).some(x=>/gapped|missing/i.test(x)));
});

test('Turtle Zone has 200 real bars warmup, constant-price convergence, pure inputs', () => {
  assert.equal(typeof extras.computeTurtleZone, 'function');
  const input = bars(250); input.forEach(Object.freeze); Object.freeze(input);
  const result = extras.computeTurtleZone(input);
  assert.equal(result.upper.upper.length, 51);
  assert.equal(result.upper.upper[0].time, input[199].time);
  for (const side of ['upper','lower']) for (const edge of ['upper','lower'])
    result[side][edge].forEach(p => near(p.value,100));
  assert.deepEqual(extras.computeTurtleZone(input.slice(0,199)).upper, {upper:[],lower:[]});
});
