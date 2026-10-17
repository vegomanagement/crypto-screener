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

test('previous day levels use only prior complete period, not future highs', () => {
  assert.equal(typeof extras.computePreviousLevel,'function');
  const input = bars(18);
  input[5].high = 120;
  input[8].high = 1000;
  const high = extras.computePreviousLevel(input,{anchor:'day',field:'high'});
  assert.deepEqual(high.slice(0,6),input.slice(0,6).map(c=>({time:c.time})));
  high.slice(6,12).forEach(p => assert.equal(p.value,120));
  high.slice(12).forEach(p => assert.equal(p.value,1000));
  const low = extras.computePreviousLevel(input,{anchor:'day',field:'low'});
  low.slice(6).forEach(p=>assert.equal(p.value,100));
  assert.deepEqual(high.slice(0,9),extras.computePreviousLevel(input.slice(0,9),{anchor:'day',field:'high'}));
});

test('previous periods omit first partial days, internal gaps and missing intervening periods', () => {
  const input = bars(24).filter((_,i)=>i!==0 && i!==14);
  const out = extras.computePreviousLevel(input,{anchor:'day',field:'high',intervalSeconds:14400});
  const byTime = new Map(out.map(p=>[p.time,p.value]));
  assert.equal(byTime.get(epoch('2026-01-02T00:00:00Z')),undefined);
  assert.equal(byTime.get(epoch('2026-01-03T00:00:00Z')),100);
  assert.equal(byTime.get(epoch('2026-01-04T00:00:00Z')),undefined);
  const missing = [...bars(6),...bars(6,epoch('2026-01-03T00:00:00Z'))];
  extras.computePreviousLevel(missing,{anchor:'day',field:'high'}).forEach(p=>assert.equal(p.value,undefined));
});

test('sessions return separated edge-timed rectangles with scoped OHLC domain', () => {
  assert.equal(typeof extras.computeSession,'function');
  const input = bars(18,epoch('2026-01-05T00:00:00Z'));
  input[0].high=125; input[0].low=80;
  const out = extras.computeSession(input,{timezone:'Asia/Tokyo',startHour:9,endHour:18});
  assert.equal(out.segments.length,3);
  assert.deepEqual(out.upper,[]); assert.deepEqual(out.lower,[]);
  assert.equal(out.segments[0].upper[0].time,epoch('2026-01-05T00:00:00Z'));
  assert.equal(out.segments[0].upper[1].time,epoch('2026-01-05T09:00:00Z'));
  assert.equal(out.segments[0].upper[0].value,125);
  assert.equal(out.segments[0].lower[0].value,80);
  assert.equal(out.timeCoordinates,'edges');
});

test('London and New York sessions follow independent DST changes', () => {
  for (const [date,zone,expected] of [
    ['2026-03-06','America/New_York','13:00'], ['2026-03-09','America/New_York','12:00'],
    ['2026-03-27','Europe/London','08:00'], ['2026-03-30','Europe/London','07:00'],
    ['2026-10-23','Europe/London','07:00'], ['2026-10-26','Europe/London','08:00'],
    ['2026-10-30','America/New_York','12:00'], ['2026-11-02','America/New_York','13:00']]) {
    const out=extras.computeSession(bars(6,epoch(`${date}T00:00:00Z`)),{timezone:zone,startHour:8,endHour:17});
    assert.equal(out.segments[0].upper[0].time,epoch(`${date}T${expected}:00Z`));
  }
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

test('UMD exports 11 disabled schema-driven definitions, no DOM or network dependencies', () => {
  assert.ok(Array.isArray(extras.definitions));
  const expected=['turtle-upper','turtle-lower','vwap-daily','vwap-weekly','prev-day-high','prev-day-low','prev-week-high','prev-week-low','session-asia','session-london','session-ny'];
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
  assert.equal(sandbox.WorkspaceIndicatorExtras.definitions.length,11);
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
  const prev=extras.computePreviousLevel(input,{anchor:'week',field:'high'});
  assert.equal(prev[7].value,undefined); assert.equal(prev[8].value,107);
  assert.deepEqual(extras.computeVWAP(bars(3,epoch('2026-01-05T00:00:00Z'),604800),{anchor:'day'}),[]);
  assert.ok(extras.getWarnings('vwap-daily',bars(3,epoch('2026-01-05T00:00:00Z'),604800)).some(x=>/boundary|coarse/i.test(x)));
  assert.ok(extras.getWarnings('vwap-weekly',input).some(x=>/partial/i.test(x)));
  assert.ok(extras.getWarnings('turtle-upper',bars(199)).some(x=>/200/ .test(x)));
});

test('previous-level definitions request step rendering rather than diagonal transitions', () => {
  extras.definitions.filter(d=>d.id.startsWith('prev-')).forEach(d=>assert.equal(d.stepLine,true));
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
  assert.ok(extras.getWarnings('turtle-upper',explosive).some(x=>/overflow|underflow/.test(x)));
  for (const source of ['hlc3','open','high','low','close','hl2','ohlc4']) assert.doesNotThrow(()=>extras.computeTurtleZone(input,{source}));
  assert.equal(extras.computeTurtleZone(bars(250),{length:300}).upper.upper.length,0);
});

test('native UTC daily/weekly candles support previous matching-period levels', () => {
  for (const [anchor,step,start] of [['day',86400,'2026-01-01'],['week',604800,'2026-01-05']]) {
    const input=bars(3,epoch(`${start}T00:00:00Z`),step); input[0].high=130;
    const out=extras.computePreviousLevel(input,{anchor,field:'high'});
    assert.equal(out[0].value,undefined); assert.equal(out[1].value,130); assert.equal(out[2].value,100);
  }
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

test('session shading splits missing bars and handles overnight hours without bridges', () => {
  const input=bars(12,epoch('2026-01-05T00:00:00Z')).filter((_,i)=>i!==1);
  const out=extras.computeSession(input,{timezone:'Asia/Tokyo',startHour:9,endHour:18,intervalSeconds:14400});
  assert.equal(out.segments.length,3);
  assert.equal(out.segments[0].upper[1].time,epoch('2026-01-05T04:00:00Z'));
  assert.equal(out.segments[1].upper[0].time,epoch('2026-01-05T08:00:00Z'));
  const overnight=extras.computeSession(bars(6),{timezone:'Asia/Tokyo',startHour:22,endHour:6});
  assert.equal(overnight.segments.length,1);
  assert.equal(overnight.segments[0].upper[0].time,epoch('2026-01-01T13:00:00Z'));
  assert.equal(overnight.segments[0].upper[1].time,epoch('2026-01-01T21:00:00Z'));
  assert.equal(extras.computeSession(bars(3,undefined,86400)).segments.length,0);
});

test('calendar warning detects wholly absent UTC periods, not just missing bars inside a group', () => {
  const input=[...bars(12),...bars(6,epoch('2026-01-04T00:00:00Z'))];
  assert.ok(extras.getWarnings('prev-day-high',input).some(x=>/gapped|missing/i.test(x)));
  assert.ok(extras.getWarnings('vwap-daily',input).some(x=>/gapped|missing/i.test(x)));
});

test('Turtle definitions mark the correct inner edge and default outer boundary hidden', () => {
  const upper=extras.definitions.find(d=>d.id==='turtle-upper');
  const lower=extras.definitions.find(d=>d.id==='turtle-lower');
  assert.equal(upper.innerBoundary,'lower'); assert.equal(lower.innerBoundary,'upper');
  for (const d of [upper,lower]) assert.equal(d.settingsSchema.find(f=>f.key==='showOuter').default,false);
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
