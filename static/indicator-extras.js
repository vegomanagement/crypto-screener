/* This source code is subject to the terms of the Mozilla Public License 2.0
 * at https://mozilla.org/MPL/2.0/
 * Turtle Zone © declarative; Ehlers_Super_Smoother/2 © KevanoTrades.
 * JavaScript port and independent calendar/session helpers: project contributors.
 * twoPole credits: John Ehlers; original library thanks Alex Orekhov (everget).
 * See docs/INDICATOR_EXTRAS.md for source provenance and port limitations.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceIndicatorExtras = factory();
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SOURCES = ['hlc3', 'open', 'high', 'low', 'close', 'hl2', 'ohlc4'];
  function lookback(value, name = 'length') {
    if (!Number.isInteger(value) || value < 1 || value > 1000) throw new RangeError(`${name} must be an integer from 1 to 1000`);
    return value;
  }
  function validateCandles(candles) {
    if (!Array.isArray(candles)) throw new TypeError('candles must be an array of closed OHLCV bars');
    let previous = -Infinity;
    candles.forEach((c, i) => {
      if (!c || !Number.isFinite(c.time) || !Number.isInteger(c.time) || c.time <= previous) throw new RangeError(`candle ${i}: time must be increasing integer Unix seconds`);
      previous = c.time;
      for (const key of ['open', 'high', 'low', 'close']) if (!Number.isFinite(c[key]) || c[key] <= 0) throw new RangeError(`candle ${i}: ${key} must be a finite positive price`);
      if (c.low > Math.min(c.open, c.close) || c.high < Math.max(c.open, c.close) || c.low > c.high) throw new RangeError(`candle ${i}: inconsistent OHLC range`);
      if (!Number.isFinite(c.volume) || c.volume < 0) throw new RangeError(`candle ${i}: volume must be finite and nonnegative`);
    });
  }
  function price(c, source) {
    if (source === 'hlc3') return c.high / 3 + c.low / 3 + c.close / 3;
    if (source === 'hl2') return c.high / 2 + c.low / 2;
    if (source === 'ohlc4') return c.open / 4 + c.high / 4 + c.low / 4 + c.close / 4;
    return c[source];
  }
  // Verified against the published KevanoTrades/Ehlers_Super_Smoother/2 source:
  // nz(source[1], source), nz(source[2], sourceReplace1), then SSF fallbacks
  // to those source replacements (NOT zero seeds or a two-bar SMA seed).
  function twoPole(values, length) {
    lookback(length);
    const arg = Math.SQRT2 * Math.PI / length;
    const a = Math.exp(-arg), c2 = 2 * a * Math.cos(arg), c3 = -(a * a), c1 = 1 - c2 - c3;
    const output = [];
    values.forEach((x, i) => {
      if (!Number.isFinite(x)) throw new RangeError('twoPole source must be finite');
      const p1 = i ? values[i - 1] : x, p2 = i > 1 ? values[i - 2] : p1;
      const y = c1 * (x / 2 + p1 / 2) + c2 * (i ? output[i - 1] : p1) + c3 * (i > 1 ? output[i - 2] : p2);
      if (!Number.isFinite(y)) throw new RangeError('twoPole numerical overflow');
      output.push(y);
    });
    return output;
  }
  function logTrueRange(candles) {
    return candles.map((c, i) => {
      const h = Math.log(c.high), l = Math.log(c.low), prev = Math.log(i ? candles[i - 1].close : c.close);
      return Math.max(h - l, Math.abs(h - prev), Math.abs(l - prev));
    });
  }
  function computeTurtleZone(candles, settings = {}) {
    validateCandles(candles);
    const {length = 200, inner = 5.6, outer = 9.6, source = 'hlc3'} = settings;
    lookback(length);
    if (!Number.isFinite(inner) || !Number.isFinite(outer) || inner < 0 || outer < inner) throw new RangeError('Turtle amplitudes must be finite, nonnegative, and outer >= inner');
    if (!SOURCES.includes(source)) throw new RangeError(`Unknown Turtle price source: ${source}`);
    const result = {upper: {upper: [], lower: []}, lower: {upper: [], lower: []}};
    const mean = twoPole(candles.map(c => Math.log(price(c, source))), length);
    const tr = twoPole(logTrueRange(candles), length);
    const start = Math.max(200, length) - 1;
    for (let i = start; i < candles.length; i++) {
      // A recursive filter may overshoot below zero even on nonnegative TR.
      // Keep raw filtered TR (Pine behavior); reject inversion rather than hide it.
      if (tr[i] < -1e-12) throw new RangeError('Turtle filtered true range is negative; band ordering cannot be represented');
      const range = Math.max(0, tr[i]);
      const values = [mean[i] + outer * range, mean[i] + inner * range, mean[i] - inner * range, mean[i] - outer * range].map(Math.exp);
      if (values.some(v => !Number.isFinite(v) || v <= 0)) throw new RangeError('Turtle exponential overflow or underflow; reduce amplitudes or use more stable price history');
      const [uo, ui, li, lo] = values;
      result.upper.upper.push({time: candles[i].time, value: uo});
      result.upper.lower.push({time: candles[i].time, value: ui});
      result.lower.upper.push({time: candles[i].time, value: li});
      result.lower.lower.push({time: candles[i].time, value: lo});
    }
    return result;
  }
  const DAY = 86400, WEEK = 7 * DAY;
  function periodSeconds(anchor) {
    if (anchor !== 'day' && anchor !== 'week') throw new RangeError('anchor must be day or week');
    return anchor === 'day' ? DAY : WEEK;
  }
  function periodStart(time, anchor) {
    const day = Math.floor(time / DAY) * DAY;
    return anchor === 'day' ? day : day - ((new Date(day * 1000).getUTCDay() + 6) % 7) * DAY;
  }
  function barSeconds(candles, settings) {
    if (settings.intervalSeconds !== undefined) {
      if (!Number.isInteger(settings.intervalSeconds) || settings.intervalSeconds <= 0) throw new RangeError('intervalSeconds must be a positive integer');
      return settings.intervalSeconds;
    }
    let step = Infinity;
    for (let i = 1; i < candles.length; i++) step = Math.min(step, candles[i].time - candles[i-1].time);
    return Number.isFinite(step) ? step : null;
  }
  function calendarGroups(candles, anchor, step) {
    const size = periodSeconds(anchor), groups = [];
    for (const c of candles) {
      const start = periodStart(c.time, anchor);
      let g = groups.at(-1);
      if (!g || g.start !== start) {
        g = {start, end:start+size, bars:[], valid:c.time === start, next:start};
        groups.push(g);
      }
      if (c.time !== g.next || c.time + step > g.end) g.valid = false;
      g.bars.push({c, valid:g.valid});
      g.next = c.time + step;
    }
    return groups;
  }
  function computeVWAP(candles, settings = {}) {
    validateCandles(candles);
    const anchor = settings.anchor || 'day', size = periodSeconds(anchor), step = barSeconds(candles, settings);
    if (!step || step > size || size % step !== 0) return [];
    const result = [];
    for (const g of calendarGroups(candles, anchor, step)) {
      let mean = 0, scale = 0, weight = 0;
      for (const {c, valid} of g.bars) {
        if (!valid) { result.push({time:c.time}); continue; }
        if (c.volume > 0) {
          if (c.volume > scale) { weight *= scale / c.volume; scale = c.volume; }
          const w = c.volume / scale, r = w / (weight + w);
          mean = (1-r)*mean + r*price(c, 'hlc3');
          weight += w;
        }
        result.push(weight ? {time:c.time,value:mean} : {time:c.time});
      }
    }
    return result;
  }
  function computePreviousLevel(candles, settings = {}) {
    validateCandles(candles);
    const anchor = settings.anchor || 'day', field = settings.field || 'high';
    const size = periodSeconds(anchor), step = barSeconds(candles, settings);
    if (field !== 'high' && field !== 'low') throw new RangeError('previous level field must be high or low');
    if (!step || step > size || size % step !== 0) return [];
    const result = [], complete = new Map();
    for (const g of calendarGroups(candles, anchor, step)) {
      const value = complete.get(g.start - size);
      for (const {c} of g.bars) result.push(value === undefined ? {time:c.time} : {time:c.time,value});
      if (g.valid && g.next === g.end) {
        const level = g.bars.reduce((v,{c}) => field === 'high' ? Math.max(v,c.high) : Math.min(v,c.low),field === 'high' ? -Infinity : Infinity);
        complete.set(g.start, level);
      }
    }
    return result;
  }
  function computeSession(candles, settings = {}) {
    validateCandles(candles);
    const {timezone = 'Asia/Tokyo', startHour = 9, endHour = 18} = settings;
    if (![startHour,endHour].every(h => Number.isInteger(h) && h >= 0 && h <= 23) || startHour === endHour) throw new RangeError('session start/end hours must be distinct integers from 0 to 23 (end <= start means overnight)');
    const formatter = new Intl.DateTimeFormat('en-GB', {timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
    const localParts = seconds => {
      const p = {};
      formatter.formatToParts(new Date(seconds*1000)).forEach(x => { if (x.type !== 'literal') p[x.type] = Number(x.value); });
      return p;
    };
    const localEpoch = seconds => {
      const p=localParts(seconds);
      return Date.UTC(p.year,p.month-1,p.day,p.hour,p.minute,p.second)/1000;
    };
    // Try both sides of any offset transition. Earliest matching instant wins
    // for repeated local hours; nonexistent local hours are never fabricated.
    const localToUTC = target => {
      const offsets = new Set([-DAY-43200,0,DAY+43200].map(d => localEpoch(target+d)-(target+d)));
      const matches = [...offsets].map(offset => target-offset).filter(t => localEpoch(t) === target);
      if (!matches.length) throw new RangeError(`Session boundary falls in a nonexistent local DST hour in ${timezone}`);
      return Math.min(...matches);
    };
    const result = {upper:[],lower:[],segments:[],timeCoordinates:'edges'};
    const step = barSeconds(candles, settings);
    if (!candles.length || !step || step >= DAY) return result;
    const first = candles[0].time, last = candles.at(-1).time+step;
    const top = candles.reduce((v,c)=>Math.max(v,c.high),-Infinity), bottom = candles.reduce((v,c)=>Math.min(v,c.low),Infinity);
    for (let day = Math.floor(first/DAY)*DAY-2*DAY; day <= last+DAY; day += DAY) {
      const start = localToUTC(day+startHour*3600);
      const end = localToUTC(day+(endHour <= startHour ? DAY : 0)+endHour*3600);
      if (end <= first || start >= last) continue;
      const spans = [];
      for (const c of candles) {
        const left=Math.max(start,c.time), right=Math.min(end,c.time+step);
        if (left >= right) continue;
        const previous=spans.at(-1);
        if (previous && previous[1] === left) previous[1]=right;
        else spans.push([left,right]);
      }
      for (const [left,right] of spans) result.segments.push({
        upper:[{time:left,value:top},{time:right,value:top}],
        lower:[{time:left,value:bottom},{time:right,value:bottom}]
      });
    }
    return result;
  }
  function computeemaBias(candles, periods = [7,14,28]) {
    validateCandles(candles);
    if (!Array.isArray(periods) || periods.length !== 3) throw new RangeError('EMA bias needs three increasing periods');
    periods.forEach(p => lookback(p, 'EMA period'));
    if (!(periods[0] < periods[1] && periods[1] < periods[2])) throw new RangeError('EMA periods must be strictly increasing');
    if (candles.length < periods[2]) return 'insufficient';
    const values = periods.map(period => {
      let value = 0;
      for (let i=0;i<period;i++) value += candles[i].close/period;
      const alpha=2/(period+1);
      for (let i=period;i<candles.length;i++) value = (1-alpha)*value + alpha*candles[i].close;
      return value;
    });
    const [fast,medium,slow]=values;
    const tolerance = Math.max(...values)*1e-12;
    if (fast-medium > tolerance && medium-slow > tolerance) return 'up';
    if (medium-fast > tolerance && slow-medium > tolerance) return 'down';
    return 'mixed';
  }
  const colorField = color => ({key:'color',label:'Color',type:'color',default:color});
  const opacityField = {key:'opacity',label:'Fill opacity',type:'number',default:0.1,min:0,max:1,step:0.01};
  function definition(id, label, kind, settingsSchema, compute) {
    return {id,label,kind,defaultEnabled:false,...(id.startsWith('prev-') ? {stepLine:true} : {}),settingsSchema,compute,
      warnings:(candles,settings) => getWarnings(id,candles,settings)};
  }
  function turtleDefinition(side, color) {
    return {...definition(`turtle-${side}`,`Turtle Zone ${side}`, 'band',[
      {key:'length',label:'Lookback period (minimum 200 bars warmup)',type:'number',default:200,min:1,max:1000,step:1},
      {key:'inner',label:'Inner amplitude',type:'number',default:5.6,min:0,max:100,step:0.1},
      {key:'outer',label:'Outer amplitude (>= inner)',type:'number',default:9.6,min:0,max:100,step:0.1},
      {key:'source',label:'Price source',type:'select',default:'hlc3',options:SOURCES.slice()},colorField(color),{...opacityField},
      {key:'showOuter',label:'Show outer boundary',type:'boolean',default:false}
    ],(candles,settings) => computeTurtleZone(candles,settings)[side]), innerBoundary:side === 'upper' ? 'lower' : 'upper'};
  }
  function sessionDefinition(id,label,timezone,startHour,endHour,color) {
    return definition(id,label,'band',[
      {key:'timezone',label:'Session timezone (DST-aware)',type:'select',default:timezone,options:['Asia/Tokyo','Europe/London','America/New_York']},
      {key:'startHour',label:'Start hour (local, inclusive)',type:'number',default:startHour,min:0,max:23,step:1},
      {key:'endHour',label:'End hour (local, exclusive)',type:'number',default:endHour,min:0,max:23,step:1},
      colorField(color),{...opacityField,default:0.08}
    ],(candles,settings = {})=>computeSession(candles,{timezone,startHour,endHour,...settings}));
  }
  const definitions = [
    turtleDefinition('upper','#ef4444'),turtleDefinition('lower','#22c55e'),
    ...['day','week'].map(anchor => definition(`vwap-${anchor === 'day' ? 'daily' : 'weekly'}`,`VWAP ${anchor === 'day' ? 'daily' : 'weekly'} (UTC)`,'line',[
      colorField(anchor === 'day' ? '#fbbf24' : '#c084fc'),
      {key:'width',label:'Line width',type:'number',default:2,min:1,max:5,step:1}
    ],(candles,settings = {})=>computeVWAP(candles,{...settings,anchor}))),
    ...['day','week'].flatMap(anchor => ['high','low'].map(field => definition(`prev-${anchor}-${field}`,`Previous ${anchor} ${field} (UTC)`,'line',[
      colorField(field === 'high' ? '#fb7185' : '#34d399'),
      {key:'width',label:'Line width',type:'number',default:1,min:1,max:5,step:1}
    ],(candles,settings = {})=>computePreviousLevel(candles,{...settings,anchor,field})))),
    sessionDefinition('session-asia','Asia session (Tokyo 09–18)','Asia/Tokyo',9,18,'#60a5fa'),
    sessionDefinition('session-london','London session (08–17 local)','Europe/London',8,17,'#fbbf24'),
    sessionDefinition('session-ny','New York session (08–17 local)','America/New_York',8,17,'#c084fc')
  ];
  function getWarnings(id, candles, settings = {}) {
    const def=definitions.find(d=>d.id===id);
    if (!def) return [`Unknown indicator: ${id}`];
    const defaults=Object.fromEntries(def.settingsSchema.map(f=>[f.key,f.default]));
    const config={...defaults,...settings};
    try {
      validateCandles(candles);
      if (!candles.length) return ['Insufficient closed candle history'];
      if (id.startsWith('turtle-')) {
        def.compute(candles,config);
        const required=Math.max(200,config.length);
        return candles.length < required ? [`Insufficient history: Turtle Zone requires ${required} closed bars; received ${candles.length}`] : [];
      }
      const step=barSeconds(candles,config);
      if (!step) return ['Insufficient history to determine bar duration; pass intervalSeconds explicitly'];
      if (id.startsWith('session-')) {
        if (step >= DAY) return ['Source timeframe is too coarse for intraday session shading; use intraday closed bars'];
        def.compute(candles,config);
        return [];
      }
      const anchor=id.includes('week') ? 'week' : 'day', size=periodSeconds(anchor);
      if (step > size || size % step !== 0) return ['Source bars are too coarse or cross an anchor boundary; lower-timeframe data is required'];
      const groups=calendarGroups(candles,anchor,step), warnings=[];
      if (groups.some((g,i)=>!g.valid || (i > 0 && g.start !== groups[i-1].end))) warnings.push('Partial or gapped calendar period omitted; fetch earlier contiguous bars from the UTC anchor boundary');
      const output=def.compute(candles,config);
      if (!output.some(p=>Number.isFinite(p.value))) warnings.push(id.startsWith('prev-') ? 'Insufficient history: no prior complete UTC calendar period' : 'No complete anchored history with positive volume');
      return warnings;
    } catch (error) { return [error.message]; }
  }
  return {definitions, computeTurtleZone, twoPole, logTrueRange, validateCandles, computeVWAP, computePreviousLevel, computeSession, computeemaBias, computeEMABias:computeemaBias, getWarnings, warningsFor:getWarnings};
}));
