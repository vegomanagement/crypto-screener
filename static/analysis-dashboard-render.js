/* Analysis dashboard: HTML renderers (pure string builders, escaped). No DOM access, so
 * node --test can assert on the markup. UMD: window.AnalysisDashboardRender / module.exports.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./analysis-dashboard-model.js'), require('./analysis-metrics-flow.js'));
  else root.AnalysisDashboardRender = factory(root.AnalysisDashboardModel, root.AnalysisFlowModel);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (M, FL) {
  'use strict';
  const { A } = M;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const dots = n => `<span class="ad-dots" aria-label="strength ${n} of 3">${[1, 2, 3].map(i => `<i class="${i <= n ? 'on' : ''}"></i>`).join('')}</span>`;
  const empty = text => `<p class="ad-empty">${esc(text)}</p>`;
  const section = (id, title, sub, body) => `<section class="ad-section" id="ad-${id}" aria-labelledby="ad-${id}-h"><header class="ad-section-head"><h2 id="ad-${id}-h">${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ''}</header>${body}</section>`;

  function verdictHtml(view, now) {
    const v = M.verdictModel(view.result, view.stats), st = M.staleState(view);
    const controls = `<div class="ad-controls"><button type="button" class="ad-run" data-act="run" ${view.busy ? 'disabled' : ''}>${view.busy ? 'Analyzing…' : st.stale ? 'Re-analyze this chart' : 'Analyze'}</button>
      <label title="Re-run at most every 2 minutes while this tab is visible"><input type="checkbox" data-act="auto" ${view.auto ? 'checked' : ''}> Auto-refresh</label>
      <label title="Dashed price lines for the levels on the active chart"><input type="checkbox" data-act="lines" ${view.showLines ? 'checked' : ''}> Levels on chart</label></div>`;
    const err = view.error ? `<p class="ad-error" role="alert">${esc(view.error)}</p>` : '';
    if (!v) return `<div class="ad-verdict is-empty">${controls}${err}${empty(view.busy ? 'Building the snapshot and asking Hermes — this takes up to about a minute.' : 'No analysis yet for this chart. Press Analyze: Hermes reads the chart\u2019s real candles, funding, positioning and order flow, and every claim cites the field it came from.')}</div>`;
    return `<div class="ad-verdict${st.stale ? ' is-stale' : ''}">
      ${st.stale ? `<p class="ad-stale-note">Previous analysis ${esc(st.label)}</p>` : ''}
      <div class="ad-verdict-main">
        <div class="ad-word bias-${esc(v.bias)}"><span>${v.bias === 'long' ? '▲' : v.bias === 'short' ? '▼' : '◆'} ${esc(v.word)}</span><small>${esc(v.confidence)} confidence${v.horizon ? ` · ${esc(v.horizon)}` : ''}</small></div>
        <div class="ad-split" role="img" aria-label="Long ${v.long} percent, short ${v.short} percent">
          <div class="ad-split-labels"><span class="up">▲ Long ${v.long}%</span><span class="down">Short ${v.short}% ▼</span></div>
          <div class="ad-split-bar"><span class="long" style="width:${v.long}%"></span><span class="short" style="width:${v.short}%"></span><i class="mid" aria-hidden="true"></i></div>
        </div>
        ${controls}
      </div>
      <p class="ad-how">${esc(v.sentence)}</p>
      <dl class="ad-meta">
        <div><dt>Calibration</dt><dd class="${v.calibrated ? '' : 'warn'}">${esc(v.calibrationNote)}${v.calibration ? ` · ${esc(v.calibration)}` : ' · no scored outcomes yet'}</dd></div>
        <div><dt>Source</dt><dd>${esc(v.status)}</dd></div>
        <div><dt>Generated</dt><dd>${esc(v.timestamp)}</dd></div>
        <div><dt>Latency</dt><dd>${esc([v.latency, v.snapshotTime, v.cached].filter(Boolean).join(' · '))}</dd></div>
      </dl>
      ${v.disagreement ? `<p class="ad-disagree" role="note">⚠ ${esc(v.disagreement)}</p>` : ''}
      <details class="ad-maths"><summary>Exact formula</summary><code>${esc(v.maths)}</code></details>${err}
    </div>`;
  }

  function summaryHtml(view) {
    const r = view.result;
    if (!r) return section('summary', 'Summary & scenarios', '', empty('Appears after the first analysis.'));
    const cards = M.scenarioCards(r);
    const body = `<div class="ad-summary"><p class="ad-prose">${esc(r.analysis.summary_ru || r.analysis.summary || 'No summary in this response.')}</p>
      ${cards.length ? `<div class="ad-scenarios">${cards.map(s => `<article class="ad-scenario kind-${esc(s.kind)}">
        <h3><span class="ad-kind">${esc(s.arrow)} ${esc(s.kindLabel)}</span>${esc(s.name)}</h3>
        <dl><div><dt>Trigger</dt><dd>${esc(s.trigger || '—')}</dd></div><div><dt>Target</dt><dd>${esc(s.target || '—')}</dd></div>
        <div><dt>Invalidation</dt><dd class="inval">${esc(s.invalidation || '—')}</dd></div></dl></article>`).join('')}</div>` : empty('Hermes gave no scenarios (baseline fallback has none).')}</div>`;
    return section('summary', 'Summary & scenarios', 'Hermes\u2019 own words; scenario direction is read from the target vs the analysed price.', body);
  }

  function levelsHtml(view, precision) {
    const r = view.result, rows = r ? M.levelTable(r, view.now, precision) : [];
    if (!rows.length) return section('levels', 'Key levels', '', empty(r ? 'No levels in this analysis.' : 'Appears after the first analysis.'));
    const canAct = !view.stale;
    const ref = r.price && r.price.value;
    const body = `<div class="ad-table-wrap"><table class="ad-table ad-levels"><caption class="sr-only">Key levels sorted by distance from the analysed price</caption>
      <thead><tr><th scope="col">Price</th><th scope="col">Type</th><th scope="col" class="num">Distance</th><th scope="col" class="num">In ATR</th><th scope="col">Source</th><th scope="col">Reason</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${rows.map(row => `<tr class="${row.unanchored ? 'is-unanchored' : ''}">
        <td class="num price"><i class="ad-swatch" style="background:${esc(row.color)}"></i>${esc(row.priceText)}</td>
        <td><span class="ad-type type-${esc(row.type)}">${esc(row.label)}</span></td>
        <td class="num ${row.side === 'above' ? 'up' : row.side === 'below' ? 'down' : ''}">${esc(row.arrow)} ${esc(row.distPctText)}</td>
        <td class="num">${esc(row.distAtrText)}</td>
        <td>${row.tags.map(t => `<span class="ad-tag${t.missing ? ' is-missing' : ''}" title="${esc(t.hint)}">${esc(t.label)} <b>${esc(t.value)}</b></span>`).join(' ') || '<span class="ad-muted">—</span>'}${!row.unanchored ? '' : ' <span class="ad-warn" title="Price does not match the cited field">unanchored</span>'}</td>
        <td class="reason">${esc(row.reason)}</td>
        <td class="actions"><button type="button" data-act="center" data-price="${row.price}" ${canAct ? '' : 'disabled'} title="Centre the active chart on ${esc(row.priceText)}">Centre</button>
          <button type="button" data-act="save" data-index="${row.index}" ${canAct && !view.added[row.index] ? '' : 'disabled'} title="Save to the shared levels store">${view.added[row.index] ? 'Saved ✓' : 'Save'}</button></td></tr>`).join('')}</tbody></table></div>`;
    return section('levels', 'Key levels', `Nearest first · distance from the analysed close ${esc(A.fmtPrice(ref, precision))}; ATR = 14-bar ATR of this chart.`, body);
  }

  function factorList(rows, side) {
    if (!rows.length) return empty(`No ${side} evidence cited.`);
    return `<ol class="ad-factors">${rows.map(f => `<li class="ad-factor is-${side}">
      <div class="ad-factor-top">${dots(f.strength)}<span class="ad-group">${esc(f.group)}</span></div>
      <p>${esc(f.text)}</p>
      <div class="ad-tags">${f.tags.map(t => `<span class="ad-tag${t.missing ? ' is-missing' : ''}" title="${esc(t.hint)}">${esc(t.label)} <b>${esc(t.value)}</b></span>`).join('')}</div></li>`).join('')}</ol>`;
  }
  function factorsHtml(view) {
    const r = view.result;
    if (!r) return section('factors', 'Evidence', '', empty('Appears after the first analysis.'));
    const c = M.factorColumns(r, view.now);
    const body = `<div class="ad-columns"><div class="ad-col"><h3 class="up">▲ Long evidence <small>${c.long.length} factors · ${c.longPoints} pts</small></h3>${factorList(c.long, 'long')}</div>
      <div class="ad-col"><h3 class="down">▼ Short evidence <small>${c.short.length} factors · ${c.shortPoints} pts</small></h3>${factorList(c.short, 'short')}</div></div>`;
    return section('factors', 'Evidence', 'Sorted by strength (●●● strongest). Tags show the cited value at analysis time; hover for its source and age.', body);
  }

  function rowsHtml(rows) {
    return `<dl class="ad-rows">${rows.map(r => `<div class="ad-row${r.missing ? ' is-missing' : ''}" title="${esc(r.hint)}"><dt>${esc(r.label)}</dt>
      <dd><b class="${esc(r.tone)}"${r.countdown ? ` data-countdown="${r.countdown}"` : ''}>${r.arrow ? `${esc(r.arrow)} ` : ''}${esc(r.value)}</b>${r.missing ? `<small>${esc(r.reason)}</small>` : r.sub ? `<small>${esc(r.sub)}</small>` : ''}</dd></div>`).join('')}</dl>`;
  }
  const shareBar = (share, label) => `<div class="ad-mini-bar" role="img" aria-label="${esc(label)}"><span class="long" style="width:${(share * 100).toFixed(1)}%"></span><span class="short" style="width:${((1 - share) * 100).toFixed(1)}%"></span></div>`;

  function cardExtra(card) {
    if (card.id === 'funding') {
      const cross = card.cross.length ? `<table class="ad-table compact"><thead><tr><th>Venue</th><th class="num">Predicted</th><th class="num">Interval</th><th class="num">Annualised</th></tr></thead><tbody>${card.cross.map(r => `<tr class="${r.self ? 'is-self' : ''}${r.stale ? ' is-stale' : ''}"><td>${esc(r.venue)}${r.self ? ' (this)' : ''}</td><td class="num">${esc(r.rate)}</td><td class="num">${esc(r.interval)}</td><td class="num">${esc(r.annual)}</td></tr>`).join('')}</tbody></table>${card.spread ? `<p class="ad-note">Cross-venue annualised spread ${esc(card.spread)}</p>` : ''}` : `<p class="ad-note">Cross-venue: — ${esc(card.crossNote || 'not available')}</p>`;
      const sp = card.spark ? `<figure class="ad-spark"><svg viewBox="0 0 300 40" preserveAspectRatio="none" aria-hidden="true">${card.spark.zeroY !== null ? `<line x1="0" x2="300" y1="${card.spark.zeroY}" y2="${card.spark.zeroY}"/>` : ''}<polyline points="${esc(card.spark.points)}"/></svg><figcaption>Last ${card.spark.n} settlements · ${esc(M.F.pct(card.spark.min))} … ${esc(M.F.pct(card.spark.max))}</figcaption></figure>` : '';
      return cross + sp;
    }
    if (card.id === 'positioning') {
      return `<div class="ad-bars">${card.bars.map(b => `<div class="ad-bar-row" title="${esc(b.hint)}"><span>${esc(b.label)}</span>${b.share === null ? `<em>— ${esc(b.reason)}</em>` : `${shareBar(b.share, `${b.label} ${(b.share * 100).toFixed(1)} percent long`)}<b class="num"><span class="up">${(b.share * 100).toFixed(1)}%</span> / <span class="down">${((1 - b.share) * 100).toFixed(1)}%</span></b>`}</div>`).join('')}</div>`;
    }
    if (card.id === 'liquidity' && card.pools) {
      return card.pools.length ? `<table class="ad-table compact"><thead><tr><th>Pool</th><th class="num">Price</th><th class="num">Distance</th><th class="num">Strength</th></tr></thead><tbody>${card.pools.map(p => `<tr><td>${esc(p.kind)}</td><td class="num">${esc(p.price)}</td><td class="num">${esc(p.arrow)} ${esc(p.dist)}</td><td class="num">${esc(p.strength)}</td></tr>`).join('')}</tbody></table>` : `<p class="ad-note">Liquidity pools: — ${esc(card.poolsReason)}</p>`;
    }
    if (card.id === 'sentiment' && card.gauge !== null && card.gauge !== undefined) {
      return `<div class="ad-fg" role="img" aria-label="Fear and greed ${card.gauge} of 100"><span style="left:${card.gauge}%"></span></div><div class="ad-fg-scale"><span>Fear 0</span><span>50</span><span>Greed 100</span></div>`;
    }
    return '';
  }

  function liquidationsBody(c) {
    if (!c.available) return `${c.label ? `<p class="ad-warn-box">${esc(c.label)}</p>` : ''}${empty(`— ${c.reason}`)}`;
    return `<p class="ad-warn-box">${esc(c.label)}</p><table class="ad-table compact"><thead><tr><th>Window</th><th class="num">Longs liquidated</th><th class="num">Shorts liquidated</th><th>Coverage</th></tr></thead><tbody>${c.rows.map(r => `<tr><td>${esc(r.window)}</td><td class="num down">${esc(r.long)} <small>(${r.longCount})</small></td><td class="num up">${esc(r.short)} <small>(${r.shortCount})</small></td><td class="${r.complete ? '' : 'warn'}">${esc(r.coverage)}</td></tr>`).join('')}</tbody></table>
      <h4>Largest prints</h4>${c.largest.length ? `<ul class="ad-list">${c.largest.map(e => `<li><span class="${e.side === 'long' ? 'down' : 'up'}">${e.side === 'long' ? '▼ long' : '▲ short'}</span> ${esc(e.usd)} @ ${esc(e.price)} <small>${esc(e.age)}</small></li>`).join('')}</ul>` : empty('None recorded in the covered window.')}<p class="ad-note" title="${esc(c.hint)}">${esc(c.status)}</p>`;
  }
  function whalesBody(c) {
    if (!c.available) return empty(`— ${c.reason}`);
    if (c.empty) return `<p class="ad-note">${esc(c.label)}</p>${empty(`No tracked account holds ${c.coin || 'this coin'}.`)}`;
    return `${shareBar(c.share, `Whale long share ${(c.share * 100).toFixed(1)} percent`)}<div class="ad-whale-sum"><span class="up">▲ Long ${esc(c.long)} · ${c.longCount}</span><span class="down">▼ Short ${esc(c.short)} · ${c.shortCount}</span><span>net ${esc(c.net)}</span><span>avg entry L ${esc(c.avgLong)} / S ${esc(c.avgShort)}</span></div>
      <div class="ad-table-wrap"><table class="ad-table compact"><thead><tr><th>Account</th><th>Side</th><th class="num">Notional</th><th class="num">Entry</th><th class="num">Liq. price</th></tr></thead><tbody>${c.positions.map(p => `<tr><td class="mono">${esc(p.who)}</td><td class="${p.side === 'long' ? 'up' : 'down'}">${p.side === 'long' ? '▲' : '▼'} ${esc(p.side)} ${esc(p.lev)}×</td><td class="num">${esc(p.usd)}</td><td class="num">${esc(p.entry)}</td><td class="num">${esc(p.liq)}</td></tr>`).join('')}</tbody></table></div>
      <h4>Recent changes</h4>${c.changes.length ? `<ul class="ad-list">${c.changes.map(x => `<li><span class="mono">${esc(x.who)}</span> ${esc(x.kind)} ${esc(x.side)} ${esc(x.usd)} <small>${esc(x.age)} · ${esc(x.detail)}</small></li>`).join('')}</ul>` : empty('None detected yet.')}<p class="ad-note">${esc(c.hint)}</p>`;
  }

  function metricsHtml(input) {
    const cards = FL.metricCards(input);
    const body = `<div class="ad-grid">${cards.map(c => `<article class="ad-card card-${esc(c.id)}"><h3>${esc(c.title)}</h3>
      ${c.id === 'liquidations' ? liquidationsBody(c) : c.id === 'whales' ? whalesBody(c) : `${c.id === 'positioning' ? cardExtra(c) : ''}${rowsHtml(c.rows || [])}${c.id === 'positioning' ? '' : cardExtra(c)}`}
      ${c.signals && c.signals.length ? `<div class="ad-chips">${c.signals.map(s => `<span class="ad-chip" title="${esc(s.hint)}">${esc(s.label)}: <b>${esc(s.state)}</b></span>`).join('')}</div>` : ''}
      ${c.note ? `<p class="ad-note">${esc(c.note)}</p>` : ''}</article>`).join('')}</div>`;
    return section('metrics', 'Market metrics', 'Hover any value for its source, venue and age. — means the value is not available, with the reason.', body);
  }

  function risksHtml(view) {
    const r = view.result, risks = (r && r.analysis.risks) || [], warns = (r && r.warnings) || [];
    return section('risks', 'Risks & caveats', '', `${risks.length ? `<ul class="ad-risks">${risks.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : empty(r ? 'No risks listed.' : 'Appears after the first analysis.')}
      ${warns.length ? `<details class="ad-warnings"><summary>Validator warnings (${warns.length})</summary><ul>${warns.map(w => `<li>${esc(w)}</li>`).join('')}</ul></details>` : ''}
      <p class="ad-disclaimer">Not financial advice. Research output from public market data; the long/short split is a scored opinion, not a probability unless marked calibrated.</p>`);
  }

  function headerHtml(view, now) {
    const h = M.headerModel(view, now);
    return `<span class="ad-title">Analysis</span><span class="ad-target">${esc(h.target)}</span><span class="ad-fresh${h.stale ? ' is-stale' : ''}">${esc(h.fresh)}</span><a href="#workspace" class="ad-top" data-act="top">↑ Charts</a>`;
  }

  return { esc, verdictHtml, summaryHtml, levelsHtml, factorsHtml, metricsHtml, risksHtml, headerHtml, rowsHtml };
});
