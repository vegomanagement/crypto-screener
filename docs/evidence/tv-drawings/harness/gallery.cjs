// Gallery: clean screenshots per group, drawn programmatically from real candles (anchors at real highs/lows)
// so the rendering of each group can be inspected without clutter. Input paths are covered by live-draw-all.
'use strict';
const { open, EV } = require('./live-common.cjs');
const GROUPS = {
  lines: ['trend', 'info', 'angle', 'hray', 'vline', 'cross'], channels: ['channel', 'regression', 'pitchfork', 'schiff', 'pitchfan'],
  fib: ['fib', 'fibext', 'fibchannel', 'gannfan', 'fibspiral', 'fibtrendtime'], measure: ['datepricerange', 'long', 'avwap', 'vbp', 'sine', 'barspattern', 'ghostfeed'],
  shapes: ['rect', 'rotrect', 'ellipse', 'triangle', 'arc', 'arrow', 'arrowup', 'brush'], annotation: ['text', 'note', 'pricelabel', 'callout', 'flag', 'signpost', 'table'],
  patterns: ['xabcd', 'headshoulders', 'elliottimpulse', 'trianglepattern'] };
(async () => {
  const c = await open({ interval: '15', reset: true });
  for (const [group, types] of Object.entries(GROUPS)) {
    const res = await c.eval(`(() => {
      const ctl = [...WorkspaceDrawings.instances][0], v = ctl.view(), n = v.candles.length, D = WorkspaceDrawings;
      ctl.actions.clearAll();
      const types = ${JSON.stringify(types)}, lo = Math.max(0, Math.ceil(v.mapper.logicalAt(70))), hi = n - 3, span = Math.floor((hi - lo) / types.length);
      const pt = (i, hi) => ({ time: v.candles[i].time * 1000, price: D.priceString(hi ? v.candles[i].high : v.candles[i].low, 2) });
      types.forEach((type, k) => {
        const def = D.getTool(type), base = lo + k * span, need = def.mode === 'click' ? def.points : 4;
        let pts = Array.from({ length: need }, (_, j) => pt(Math.min(n - 1, base + Math.round(j * (span - 4) / Math.max(1, need - 1))), j % 2 === 0));
        if (type === 'barspattern') pts = [pt(base, true), pt(base + 6, true), pt(Math.min(n - 1, base + span - 7), false)];
        if (type === 'avwap') pts = [pt(base, false)];
        if (def.finalize) pts = def.finalize(pts.slice(0, 2), v);
        ctl.actions.addDrawing(type, pts, def.editText ? { text: type === 'table' ? 'Level | Price\\nEntry | ' + pts[0].price + '\\nStop | —' : def.label } : {});
      });
      ctl.select(null);
      return ctl.state.items.length; })()`);
    await c.sleep(600);
    await c.shot(`${EV}/group_${group}.png`);
    console.log(group, res);
  }
  c.close(); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
