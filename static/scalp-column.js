/* Per-column trade bookkeeping for /scalp: prints feed + cluster bars from one public trade stream.
 * Pure (no DOM, no network): the page feeds it hub events, tests feed it fixtures. Every print and every
 * cluster cell comes from a received venue trade; bars that could be missing trades are flagged `partial`
 * (opened before this page subscribed, or a stream gap / reconnect happened inside them).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./orderbook.js'), require('./footprint.js'));
  else root.ScalpColumnData = factory(root.ScalpOrderBook, root.WorkspaceFootprint);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (ob, fp) {
  'use strict';
  const tickString = tick => tick.toFixed(ob.decimalsOf(tick));

  class ColumnData {
    constructor({ venue, symbol, tick, feed = {}, clusters = {}, now = Date.now, maxRaw = 20000 }) {
      this.venue = venue; this.symbol = symbol; this.tick = tick; this.tickStr = tickString(tick); this.now = now;
      this.feedCfg = { aggregateMs: feed.aggregateMs || 0, aggregateTicks: feed.aggregateTicks || 0 };
      this.intervalMs = clusters.intervalMs || 60000; this.keepBars = Math.max(1, (clusters.count ?? 3) + 1);
      this.maxRaw = maxRaw; this.raw = []; this.bars = []; this.startedAt = now();
      this.gapOpen = true; // nothing observed yet: the first bar is partial unless it opens after we subscribed
      this.lastAggId = null; this.streamStatus = 'connecting';
      this.stats = { received: 0, rejected: 0, clusterRejected: 0, idGaps: 0, disconnects: 0, firstAt: null, lastAt: null };
      this.feed = this.newFeed();
    }
    newFeed() { return new ob.PrintFeed({ tick: this.tick, aggregateMs: this.feedCfg.aggregateMs, aggregateTicks: this.feedCfg.aggregateTicks }); }
    /** Hub event: { type: 'trades', rows } or { type: 'status', status }. Returns true when something changed. */
    onEvent(event) {
      if (!event) return false;
      if (event.type === 'status') return this.onStatus(event.status);
      if (event.type !== 'trades' || !Array.isArray(event.rows)) return false;
      let changed = false;
      for (const row of event.rows) changed = this.onRow(row) || changed;
      return changed;
    }
    onStatus(status) {
      const was = this.streamStatus; this.streamStatus = status;
      if (status === 'disconnected' && was !== 'disconnected') {
        this.stats.disconnects += 1; this.gapOpen = true;
        const last = this.bars.at(-1); if (last) last.partial = true;
      }
      return was !== status;
    }
    onRow(row) {
      const print = ob.normalizePrint(this.venue, row);
      if (!print) { this.stats.rejected += 1; return false; }
      this.stats.received += 1; this.stats.firstAt ??= this.now(); this.stats.lastAt = this.now();
      if (this.venue === 'binance' && Number.isSafeInteger(row.a)) {
        if (this.lastAggId !== null && row.a > this.lastAggId + 1) { this.stats.idGaps += 1; this.gapOpen = true; }
        if (this.lastAggId === null || row.a > this.lastAggId) this.lastAggId = row.a;
      }
      this.raw.push(print);
      if (this.raw.length > this.maxRaw) this.raw.splice(0, this.raw.length - this.maxRaw);
      this.feed.add(print);
      this.addCluster(row, print.time);
      return true;
    }
    addCluster(row, time) {
      let trade;
      try { trade = fp.normalizeTrade(this.venue, row); } catch (_) { this.stats.clusterRejected += 1; return; }
      const openMs = Math.floor(time / this.intervalMs) * this.intervalMs;
      let entry = this.bars.find(b => b.openMs === openMs);
      if (!entry) {
        if (this.bars.length && openMs < this.bars.at(-1).openMs) { this.stats.clusterRejected += 1; return; } // late trade for a dropped bar
        entry = { openMs, bar: fp.createBar(openMs, this.intervalMs, this.tickStr), partial: this.gapOpen || openMs < this.startedAt, cache: null };
        this.bars.push(entry);
        if (this.bars.length > this.keepBars) this.bars.splice(0, this.bars.length - this.keepBars);
      } else if (this.gapOpen) entry.partial = true;
      this.gapOpen = false;
      try { fp.addTrade(entry.bar, trade); } catch (_) { this.stats.clusterRejected += 1; }
    }
    /** Re-aggregate the feed from the raw prints kept in memory (aggregation settings changed). */
    setFeedAggregation({ aggregateMs = 0, aggregateTicks = 0 }) {
      this.feedCfg = { aggregateMs, aggregateTicks };
      const feed = this.newFeed();
      for (const p of this.raw) feed.add(p);
      this.feed = feed;
    }
    /** Changing the interval drops the bars: they are rebuilt from live trades only (marked partial). */
    setClusters({ intervalMs, count }) {
      if (count !== undefined) this.keepBars = Math.max(1, count + 1);
      if (intervalMs && intervalMs !== this.intervalMs) { this.intervalMs = intervalMs; this.bars = []; this.gapOpen = true; }
    }
    /** Window accounting used by the live acceptance check: trades received vs trades held in the feed. */
    windowCounts(from, to) {
      const raw = this.raw.filter(p => p.time >= from && p.time <= to);
      const feed = this.feed.prints.filter(p => p.firstTime >= from && p.lastTime <= to);
      return { received: raw.length, receivedUsd: raw.reduce((a, p) => a + p.price * p.qty, 0),
        feedTrades: feed.reduce((a, p) => a + p.count, 0), feedUsd: feed.reduce((a, p) => a + p.usd, 0), feedPrints: feed.length };
    }
  }
  return { ColumnData, tickString };
});
