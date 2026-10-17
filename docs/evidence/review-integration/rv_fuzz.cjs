const G = require('/Users/maxbelov/Projects/wt/review-fixes/static/indicators/groups.js');
const C = require('/Users/maxbelov/Projects/wt/review-fixes/static/drawings/core.js');
const vals = [null, 1, 'x', [], {}, [1], {a:1}, true, {__proto__:null}, {7:'x',14:[],28:{}}, {high:'yes'}, {period: 1e309}, {'ema7': null}, {'ema7': []}];
let errs = 0;
for (const v1 of vals) for (const v2 of vals) for (const v3 of vals) {
  const saved = { version: 4, layout: '4grid', ema: v1, ma20hl: v2, indicatorSettings: v3, indicatorEnabled: v2 };
  try { G.migrateLegacyState(saved); } catch (e) { errs++; if (errs < 5) console.log('groups err', JSON.stringify(saved), e.message); }
}
console.log('groups errors', errs);
errs = 0;
const pts = [null, 1, 'x', [], [{}], [{time: 1, price: '1'}], [{time: '1', price: 1}], [{time: 1, price: '1'}, {time: 2, price: '2'}]];
for (const items of [null, 1, 'x', {}, [null], [1], [{}], ...pts.map(p => [{ id: 'a', type: 'line', points: p }])]) {
  for (const entry of [null, 1, { items }, { items, hidden: 'x' }]) {
    try { C.migrateV1(JSON.stringify({ version: 1, symbols: { 'binance:BTCUSDT': entry, '__proto__': entry } })); C.parseState(JSON.stringify({ version: 2, symbols: { 'binance:BTCUSDT': entry } })); }
    catch (e) { errs++; if (errs < 5) console.log('drawings err', JSON.stringify(entry), e.message); }
  }
}
console.log('drawings errors', errs);
