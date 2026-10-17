/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 *
 * JavaScript port of the TradingView script "FX Market Sessions" by boitoki
 * (© boitoki), version 54 (Pine v6),
 * https://www.tradingview.com/script/IijBXaGM-FX-Market-Sessions/
 * Original source with its notice: third_party/tradingview/boitoki-fx-market-sessions-v54.pine
 * License text: third_party/tradingview/LICENSE-MPL-2.0.
 *
 * The original imports boitoki/AwesomeColor and boitoki/Utilities; this port
 * re-implements the few helpers it needs (colours, weekday names, history
 * trimming) instead of using those libraries. Sessions are computed only from
 * the chart's real closed candles. Port differences (no bar colouring, info /
 * opening-range tables or alerts; "pips" shown as range %) are listed in
 * docs/INDICATOR_GROUPS_RESULT.md. Additions over the script (not in v54): an
 * optional per-session time zone, including DST-aware IANA zones resolved in
 * the browser with Intl.DateTimeFormat (docs/POLISH_RESULT.md). The default is
 * still the script's GMT+0 windows.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceSessions = factory();
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const DAY = 86400;
  const TIMEZONES = ['GMT-11', 'GMT-10', 'GMT-9', 'GMT-8', 'GMT-7', 'GMT-6', 'GMT-5', 'GMT-4', 'GMT-3', 'GMT-2', 'GMT-1', 'GMT+0', 'GMT+1', 'GMT+2', 'GMT+3',
    'GMT+330', 'GMT+4', 'GMT+430', 'GMT+5', 'GMT+530', 'GMT+6', 'GMT+7', 'GMT+8', 'GMT+9', 'GMT+10', 'GMT+11', 'GMT+12'];
  // DST-aware zones offered in addition to the script's fixed offsets.
  const IANA_ZONES = ['Europe/London', 'America/New_York', 'Asia/Tokyo', 'Australia/Sydney'];
  const INHERIT = 'inherit'; // per-session zone: use the General timezone
  // Script defaults (name, colour, window, enabled). Monokai-like colours from the source.
  const SESSION_DEFAULTS = [['London', '#66d9ef', '0800-1700', true], ['New York', '#fd971f', '1300-2200', true],
    ['Tokyo', '#ae81ff', '0000-0900', true], ['Sydney', '#fb71a3', '2000-0500', false]];
  const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']; // Utilities.get_day equivalent
  const FIB_LEVELS = [0.5, 0.628, 0.382]; // as in the source (0.628, not 0.618)
  const INFO_PERIOD = 50; // i_info_period: ranges kept for the target average
  // AwesomeColor.tradingview(...) equivalents used by the opening range.
  const TV = { blue: '#2962ff', red: '#f23645', yellow: '#ffeb3b', gray: '#787b86' };

  // 'GMT+330' → +12600 s, 'GMT-5' → -18000 s. Fixed offsets, no DST.
  function parseOffset(tz) {
    const m = /^GMT([+-])(\d{1,2})(\d{2})?$/.exec(tz || '');
    if (!m) throw new RangeError(`timezone must be one of the fixed GMT offsets, got ${tz}`);
    const hours = Number(m[2]), minutes = m[3] ? Number(m[3]) : 0;
    if (hours > 14 || minutes >= 60) throw new RangeError(`invalid GMT offset ${tz}`);
    return (m[1] === '-' ? -1 : 1) * (hours * 3600 + minutes * 60);
  }
  // ── Time zones ──
  // A zone is either a fixed 'GMT±H[MM]' offset (as in the script) or one of
  // IANA_ZONES, whose UTC offset at an instant comes from Intl.DateTimeFormat.
  const formatters = new Map();
  function zoneFormatter(tz) {
    if (!formatters.has(tz)) formatters.set(tz, new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric',
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    return formatters.get(tz);
  }
  function resolveZone(tz) {
    if (/^GMT[+-]/.test(tz || '')) return { id: tz, fixed: parseOffset(tz) };
    if (!IANA_ZONES.includes(tz)) throw new RangeError(`timezone must be a fixed GMT offset or one of ${IANA_ZONES.join(', ')}, got ${tz}`);
    zoneFormatter(tz); // throws RangeError if the runtime lacks the zone
    return { id: tz, fixed: null };
  }
  // Seconds east of UTC at the instant t (unix seconds).
  function zoneOffset(zone, t) {
    if (zone.fixed !== null) return zone.fixed;
    const whole = Math.floor(t), p = {};
    zoneFormatter(zone.id).formatToParts(new Date(whole * 1000)).forEach(part => { p[part.type] = Number(part.value); });
    return Date.UTC(p.year, p.month - 1, p.day, p.hour % 24, p.minute, p.second) / 1000 - whole;
  }
  // Local wall-clock seconds (expressed as if UTC) → unix seconds. Ambiguous
  // times (clocks back) take the earlier instant; skipped times (clocks
  // forward) keep the pre-transition offset, i.e. land after the gap. This is
  // what Python's zoneinfo does with fold=0.
  function localToUtc(zone, wall) {
    if (zone.fixed !== null) return wall - zone.fixed;
    const before = zoneOffset(zone, wall - DAY), after = zoneOffset(zone, wall + DAY);
    const valid = [...new Set([before, after])].map(o => wall - o).filter(u => u + zoneOffset(zone, u) === wall);
    return valid.length ? Math.min(...valid) : wall - before;
  }
  const localDay = (zone, t) => Math.floor((t + zoneOffset(zone, t)) / DAY);

  // Pine session string 'HHMM-HHMM'; end <= start crosses midnight.
  function parseSession(value) {
    const m = /^(\d{2})(\d{2})-(\d{2})(\d{2})$/.exec(value || '');
    if (!m) throw new RangeError(`session must look like 0800-1700, got ${value}`);
    const [sh, sm, eh, em] = m.slice(1).map(Number);
    if (sh > 23 || eh > 24 || sm > 59 || em > 59 || (eh === 24 && em)) throw new RangeError(`invalid session time ${value}`);
    const start = sh * 60 + sm, end = eh * 60 + em;
    if (start === end) throw new RangeError(`session ${value} has zero length`);
    return { start, end, minutes: end > start ? end - start : end + 1440 - start };
  }
  function settingsDefaults() {
    const s = { timezone: 'GMT+0', history: 10, boxStyle: 'Box', borderStyle: 'dashed', borderWidth: 1, bgColor: true, dots: true, includeLastBar: true,
      labelShow: true, labelPosition: 'top', labelSide: 'outside', labelName: true, labelDay: false, labelPrice: false, labelPct: false,
      orMinutes: 15, orNowOnly: false, orBreakout: false, orTargets: false, orTargetBoxes: false, orColor: 'blue-red',
      fibStyle: 'solid', fibWidth: 1 };
    SESSION_DEFAULTS.forEach(([name, color, time, on], i) => Object.assign(s, { [`s${i + 1}Show`]: on, [`s${i + 1}Name`]: name, [`s${i + 1}Color`]: color,
      [`s${i + 1}Time`]: time, [`s${i + 1}Tz`]: INHERIT, [`s${i + 1}Extend`]: false, [`s${i + 1}Op`]: false, [`s${i + 1}Fib`]: false }));
    return s;
  }

  // Pure computation over CLOSED chart candles with a known bar `step` (s).
  // A bar belongs to a session when its open time is inside [start, end),
  // which is how Pine's time(timeframe.period, session, tz) assigns bars.
  function compute(candles, settings, step) {
    const s = { ...settingsDefaults(), ...(settings || {}) };
    const out = { instances: [], warnings: [], hidden: false, step };
    if (!Array.isArray(candles) || !candles.length) return out;
    if (!Number.isFinite(step) || step <= 0) { out.warnings.push('Unknown bar duration'); return out; }
    if (step >= DAY) { out.hidden = true; out.warnings.push('Sessions are intraday-only and are hidden on daily and higher intervals'); return out; }
    const history = Math.max(1, Math.min(500, Math.trunc(s.history) || 10));
    const first = candles[0].time, last = candles.at(-1).time;
    const times = candles.map(c => c.time);
    const lowerBound = t => { let lo = 0, hi = times.length; while (lo < hi) { const mid = (lo + hi) >>> 1; if (times[mid] < t) lo = mid + 1; else hi = mid; } return lo; };
    for (let n = 1; n <= 4; n += 1) {
      if (!s[`s${n}Show`]) continue;
      let window, zone;
      const tz = s[`s${n}Tz`] && s[`s${n}Tz`] !== INHERIT ? s[`s${n}Tz`] : s.timezone;
      try { window = parseSession(s[`s${n}Time`]); zone = resolveZone(tz); } catch (error) { out.warnings.push(`${s[`s${n}Name`]}: ${error.message}`); continue; }
      const extend = Boolean(s[`s${n}Extend`]);
      const list = [], ranges = [];
      const firstDay = localDay(zone, first) - 1, lastDay = localDay(zone, last);
      for (let day = firstDay; day <= lastDay; day += 1) {
        // Start and end are resolved separately, so a window spanning a DST
        // change is one hour shorter or longer in UTC, like Pine's time(..., tz).
        const start = localToUtc(zone, day * DAY + window.start * 60);
        const end = localToUtc(zone, (window.end > window.start ? day : day + 1) * DAY + window.end * 60);
        const i0 = lowerBound(start), i1 = lowerBound(end); // bars [i0, i1)
        if (i1 <= i0) continue;
        const bars = candles.slice(i0, i1);
        let high = -Infinity, low = Infinity;
        bars.forEach(c => { if (c.high > high) high = c.high; if (c.low < low) low = c.low; });
        const ended = last + step >= end; // the last closed bar reaches the window end
        const inst = { n, name: s[`s${n}Name`], color: s[`s${n}Color`], start, end, firstIndex: i0, lastIndex: i1 - 1,
          firstTime: bars[0].time, lastTime: bars.at(-1).time, open: bars[0].open, close: bars.at(-1).close, high, low,
          range: high - low, rangePct: (high - low) / low * 100, day: WEEKDAYS[new Date((day * DAY) * 1000).getUTCDay()], timezone: zone.id,
          ended, inProgress: !ended, extend,
          avgRange: ranges.length ? ranges.reduce((a, b) => a + b, 0) / ranges.length : null };
        inst.fib = s[`s${n}Fib`] ? FIB_LEVELS.map(level => ({ level, price: (high - low) * level + low })) : [];
        // The loaded history starts after this session's start: its first N minutes
        // are not in the data, so no opening range is drawn (Pine would take the
        // first chart bar as the start; that range would not be the session's).
        inst.truncated = i0 === 0 && candles[0].time > start;
        if (s[`s${n}Op`]) inst.or = inst.truncated ? { truncated: true } : openingRange(bars, inst, s, step);
        inst.label = labelText(inst, s);
        list.push(inst);
        if (ended) { ranges.unshift(inst.range); if (ranges.length > INFO_PERIOD) ranges.pop(); }
      }
      // History: the script keeps i_history_period drawings per session,
      // or only the latest one when the session is extended to the right.
      let kept = list.slice(-(extend ? 1 : history));
      if (s[`s${n}Op`] && s.orNowOnly) kept = kept.map((inst, i) => i === kept.length - 1 && !inst.ended ? inst : { ...inst, or: null });
      out.instances.push(...kept);
    }
    out.instances.sort((a, b) => a.start - b.start || a.n - b.n);
    return out;
  }

  // Opening range, as in f_render_oprange: N is forced to at least the chart
  // timeframe + 1 minute (i_o_minutes := max(i_o_minutes, timeframe.multiplier + 1)),
  // the session start is the first session bar's own time, and the range is
  // fixed on the bar(s) whose open time is in (start + N − 1 bar, start + N],
  // with top/bottom = highest/lowest since the session start. So it includes
  // the bar opening exactly at start + N (15m chart, N = 15 → 16 → 2 bars).
  // Pine's f_get_time_by_bar is `multiplier × 60 s`; on seconds charts that is
  // multiplier minutes, which makes several bars "fix" the range in a row; the
  // last one wins, i.e. the range is every bar with time ≤ start + N.
  function openingRange(bars, inst, s, step) {
    const requested = Math.max(1, Math.trunc(s.orMinutes) || 15);
    const multiplier = step >= 60 ? step / 60 : step; // timeframe.multiplier (minutes, or seconds on S charts)
    const minutes = Math.max(requested, Math.ceil(multiplier) + 1);
    const startTime = bars[0].time, limit = startTime + minutes * 60, delay = multiplier * 60;
    let k = -1; // last fixing bar
    for (let i = 1; i < bars.length && bars[i].time <= limit; i += 1) if (bars[i].time > limit - delay) k = i;
    const lastSeen = bars.at(-1).time;
    if (!inst.ended && lastSeen + step <= limit) return { pending: true, minutes, requestedMinutes: requested };
    if (k === -1) return { missing: true, minutes, requestedMinutes: requested }; // no bar at start + N: Pine never fixes it
    const orBars = bars.slice(0, k + 1);
    let top = -Infinity, btm = Infinity;
    orBars.forEach(c => { top = Math.max(top, c.high); btm = Math.min(btm, c.low); });
    const mdl = (top + btm) / 2;
    const or = { minutes, requestedMinutes: requested, bars: orBars.length, top, btm, mdl, endTime: orBars.at(-1).time + step, lastOrTime: orBars.at(-1).time, breakouts: [], targets: null };
    if (inst.avgRange !== null) {
      const avg1 = inst.avgRange * 0.786 * 0.5, avg2 = inst.avgRange * 1.272 * 0.5;
      or.targets = { R1: (btm + mdl) / 2 + avg1, R2: (btm + mdl) / 2 + avg2, S1: (top + mdl) / 2 - avg1, S2: (top + mdl) / 2 - avg2 };
      // ta.crossover(high, R) / ta.crossunder(low, S): first crossing per level
      // on the session bars after the fixing bar, against the previous chart bar.
      const reached = new Set();
      for (let i = k + 1; i < bars.length; i += 1) {
        const c = bars[i], p = bars[i - 1];
        [['R1', 'high', 1], ['R2', 'high', 1], ['S1', 'low', -1], ['S2', 'low', -1]].forEach(([name, field, dir]) => {
          const level = or.targets[name];
          if (reached.has(name)) return;
          if (dir > 0 ? c.high > level && p.high <= level : c.low < level && p.low >= level) {
            reached.add(name); or.breakouts.push({ level: name, time: c.time, price: c[field], side: dir > 0 ? 'up' : 'down' });
          }
        });
      }
    }
    return or;
  }
  function labelText(inst, s) {
    const parts = [];
    if (s.labelName && inst.name) parts.push(inst.name);
    if (s.labelDay) parts.push(inst.day);
    if (s.labelPrice) parts.push(formatNumber(inst.range));
    if (s.labelPct) parts.push(`${inst.rangePct.toFixed(2)}%`);
    return parts.join(' • ');
  }
  function formatNumber(value) {
    if (!Number.isFinite(value)) return '—';
    const digits = Math.abs(value) >= 100 ? 2 : Math.abs(value) >= 1 ? 4 : 6;
    return String(Number(value.toFixed(digits)));
  }

  function settingsSchema() {
    const d = settingsDefaults(), f = [];
    f.push({ key: 'timezone', label: 'Timezone (GMT±N fixed; city zones follow DST)', type: 'select', options: [...TIMEZONES, ...IANA_ZONES], default: 'GMT+0', group: 'General' },
      { key: 'history', label: 'History (sessions)', type: 'number', min: 1, max: 500, step: 1, default: 10, group: 'General' });
    for (let n = 1; n <= 4; n += 1) {
      const group = `Session #${n}`;
      f.push({ key: `s${n}Show`, label: 'Enabled', type: 'boolean', default: d[`s${n}Show`], group },
        { key: `s${n}Name`, label: 'Name', type: 'text', maxLength: 24, default: d[`s${n}Name`], group },
        { key: `s${n}Color`, label: 'Color', type: 'color', default: d[`s${n}Color`], group },
        { key: `s${n}Time`, label: 'Time (HHMM-HHMM)', type: 'session', default: d[`s${n}Time`], group },
        { key: `s${n}Tz`, label: 'Time zone', type: 'select', options: [INHERIT, ...IANA_ZONES, ...TIMEZONES], optionLabels: { [INHERIT]: 'General timezone' }, default: INHERIT, group },
        { key: `s${n}Extend`, label: 'Extend right', type: 'boolean', default: false, group },
        { key: `s${n}Op`, label: 'Opening range', type: 'boolean', default: false, group },
        { key: `s${n}Fib`, label: 'Fibonacci levels', type: 'boolean', default: false, group });
    }
    f.push({ key: 'boxStyle', label: 'Style', type: 'select', options: ['Box', 'Background', 'Hamburger', 'Sandwich'], default: 'Box', group: 'Box' },
      { key: 'borderStyle', label: 'Border', type: 'select', options: ['solid', 'dashed', 'dotted'], default: 'dashed', group: 'Box' },
      { key: 'borderWidth', label: 'Border width', type: 'number', min: 0, max: 4, step: 1, default: 1, group: 'Box' },
      { key: 'bgColor', label: 'BG color', type: 'boolean', default: true, group: 'Box' },
      { key: 'dots', label: 'Dots (open → close)', type: 'boolean', default: true, group: 'Box' },
      { key: 'includeLastBar', label: 'Include the latest bar in the session end', type: 'boolean', default: true, group: 'Box' },
      { key: 'labelShow', label: 'Show labels', type: 'boolean', default: true, group: 'Labels' },
      { key: 'labelPosition', label: 'Position', type: 'select', options: ['top', 'bottom'], default: 'top', group: 'Labels' },
      { key: 'labelSide', label: 'Side', type: 'select', options: ['outside', 'inside'], default: 'outside', group: 'Labels' },
      { key: 'labelName', label: 'Name', type: 'boolean', default: true, group: 'Labels' },
      { key: 'labelDay', label: 'Day', type: 'boolean', default: false, group: 'Labels' },
      { key: 'labelPrice', label: 'Price (high − low)', type: 'boolean', default: false, group: 'Labels' },
      { key: 'labelPct', label: 'Range % (replaces pips)', type: 'boolean', default: false, group: 'Labels' },
      { key: 'orMinutes', label: 'Period (minutes)', type: 'number', min: 1, max: 1440, step: 1, default: 15, group: 'Opening range' },
      { key: 'orColor', label: 'Colors', type: 'select', options: ['blue-red', 'session'], default: 'blue-red', group: 'Opening range' },
      { key: 'orNowOnly', label: 'Current session only', type: 'boolean', default: false, group: 'Opening range' },
      { key: 'orBreakout', label: 'Breakout flag', type: 'boolean', default: false, group: 'Opening range' },
      { key: 'orTargets', label: 'Target lines', type: 'boolean', default: false, group: 'Opening range' },
      { key: 'orTargetBoxes', label: 'Target boxes', type: 'boolean', default: false, group: 'Opening range' },
      { key: 'fibStyle', label: 'Line style', type: 'select', options: ['solid', 'dashed', 'dotted'], default: 'solid', group: 'Fibonacci levels' },
      { key: 'fibWidth', label: 'Line width', type: 'number', min: 1, max: 4, step: 1, default: 1, group: 'Fibonacci levels' });
    return f;
  }

  const DASH = { solid: [], dashed: [5, 4], dotted: [1, 3] };
  function rgba(hex, alpha) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex || ''); if (!m) return hex;
    const v = parseInt(m[1], 16);
    return `rgba(${v >> 16 & 255},${v >> 8 & 255},${v & 255},${alpha})`;
  }
  // view = { x(time) → px at a bar boundary, y(price), barSpacing, plotWidth, plotHeight, format(price) }
  function draw(context, view, model, settings) {
    const s = { ...settingsDefaults(), ...(settings || {}) };
    if (!model || model.hidden) return 0;
    let drawn = 0;
    const seg = (x1, y1, x2, y2, color, width, dash) => {
      if (![x1, y1, x2, y2].every(Number.isFinite) || width <= 0) return;
      context.save(); context.strokeStyle = color; context.lineWidth = width; context.setLineDash?.(dash || []);
      context.beginPath(); context.moveTo(x1, y1); context.lineTo(x2, y2); context.stroke(); context.restore();
    };
    const rect = (x1, y1, x2, y2, fill) => { if ([x1, y1, x2, y2].every(Number.isFinite)) { context.fillStyle = fill; context.fillRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1)); } };
    const text = (value, x, y, color, align = 'left', baseline = 'bottom', size = 10) => {
      if (!value || !Number.isFinite(x) || !Number.isFinite(y)) return;
      context.save(); context.fillStyle = color; context.font = `${size}px SFMono-Regular, Consolas, monospace`; context.textAlign = align; context.textBaseline = baseline;
      context.fillText(value, x, y); context.restore();
    };
    const step = model.step;
    model.instances.forEach(inst => {
      const x1 = view.x(inst.start);
      const endTime = inst.ended ? (s.includeLastBar ? inst.lastTime + step : inst.lastTime) : inst.end; // in progress: projected window
      const x2 = inst.extend ? view.plotWidth : view.x(endTime);
      const yTop = view.y(inst.high), yBot = view.y(inst.low);
      if (![x1, x2, yTop, yBot].every(Number.isFinite)) return;
      drawn += 1;
      const bg = rgba(inst.color, s.bgColor ? 0.06 : 0), width = s.boxStyle === 'Background' ? 0 : s.borderWidth, dash = DASH[s.borderStyle];
      if (s.boxStyle === 'Hamburger') {
        rect(x1, yTop, x2, yBot, bg); seg(x1, yTop, x2, yTop, inst.color, width, dash); seg(x1, yBot, x2, yBot, inst.color, width, dash);
      } else if (s.boxStyle === 'Sandwich') {
        rect(x1, yTop, x2, yBot, bg); seg(x1, yTop, x1, yBot, inst.color, width, dash); seg(x2, yTop, x2, yBot, inst.color, width, dash);
      } else {
        rect(x1, yTop, x2, yBot, bg);
        if (width > 0) { context.save(); context.strokeStyle = inst.color; context.lineWidth = width; context.setLineDash?.(dash); context.strokeRect(x1, yTop, x2 - x1, yBot - yTop); context.restore(); }
        if (s.dots) {
          const yo = view.y(inst.open), yc = view.y(inst.close);
          rect(x1, yo, x2, yc, rgba(inst.color, s.bgColor ? 0.1 : 0));
          seg(x1, yo, x2, yo, rgba(inst.color, 0.3), 1, DASH.dotted); seg(x1, yc, x2, yc, rgba(inst.color, 0.3), 1, DASH.dotted);
          if (inst.ended) { text('●', x1, yo, inst.color, 'center', 'middle', 9); text('◉', view.x(s.includeLastBar ? inst.lastTime + step : inst.lastTime), yc, inst.color, 'center', 'middle', 9); }
        }
      }
      if (s.labelShow && inst.label) {
        const top = s.labelPosition !== 'bottom', outside = s.labelSide !== 'inside';
        const y = top ? yTop : yBot;
        text(inst.label, x1 + 2, top === outside ? y - 2 : y + 2, inst.color, 'left', top === outside ? 'bottom' : 'top');
      }
      inst.fib.forEach(f => seg(x1, view.y(f.price), x2, view.y(f.price), rgba(inst.color, 0.7), f.level === 0.5 ? 2 : s.fibWidth, f.level === 0.5 ? [] : DASH[s.fibStyle]));
      const or = inst.or;
      if (or && !or.pending && !or.missing && !or.truncated) {
        const topColor = s.orColor === 'session' ? inst.color : TV.blue, btmColor = s.orColor === 'session' ? inst.color : TV.red;
        const xr = view.x(or.endTime), yt = view.y(or.top), yb = view.y(or.btm);
        rect(x1, yt, xr, yb, rgba(TV.yellow, 0.5));
        seg(x1, yt, x2, yt, topColor, 1, DASH.dotted); seg(x1, yb, x2, yb, btmColor, 1, DASH.dotted);
        text(view.format(or.top), x1 - 3, yt, topColor, 'right', 'middle', 9); text(view.format(or.btm), x1 - 3, yb, btmColor, 'right', 'middle', 9);
        if (or.targets) {
          const t = or.targets, yR1 = view.y(t.R1), yR2 = view.y(t.R2), yS1 = view.y(t.S1), yS2 = view.y(t.S2);
          if (s.orTargets) {
            [[yR1, topColor], [yR2, topColor], [yS1, btmColor], [yS2, btmColor]].forEach(([y, c]) => seg(x1, y, x2, y, rgba(c, 0.6), 1));
            rect(x1, yR1, x2, yR2, rgba(topColor, 0.04)); rect(x1, yS1, x2, yS2, rgba(btmColor, 0.04));
          }
          if (s.orTargetBoxes) { const w = view.barSpacing; rect(x1, yR1, x1 + w, yR2, rgba(topColor, 0.5)); rect(x1, yS1, x1 + w, yS2, rgba(btmColor, 0.5)); }
          if (s.orBreakout) or.breakouts.forEach(b => text('×', view.x(b.time) + view.barSpacing / 2, view.y(b.price), b.side === 'up' ? TV.blue : TV.red, 'center', 'middle', 14));
        }
        if (!inst.ended) seg(x1, view.y(inst.close), x1 + 2 * view.barSpacing, view.y(inst.close), inst.color, 3);
      }
    });
    return drawn;
  }

  return { DAY, TIMEZONES, IANA_ZONES, INHERIT, resolveZone, zoneOffset, localToUtc, SESSION_DEFAULTS, WEEKDAYS, FIB_LEVELS, TV, parseOffset, parseSession, settingsDefaults, settingsSchema, compute, openingRange, labelText, draw };
}));
