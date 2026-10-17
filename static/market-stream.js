/* Public market data only. No quotes are used to manufacture candle OHLCV.
 * Protocol references: bybit-exchange.github.io/docs/v5/websocket/public/kline,
 * developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice,
 * hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceMarketStream = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function createHub(options = {}) {
    const WS = options.WebSocket || globalThis.WebSocket;
    const now = options.now || Date.now;
    const later = options.setTimeout || setTimeout;
    const cancel = options.clearTimeout || clearTimeout;
    const random = options.random || Math.random;
    const entries = new Map();
    function subscribe(config, listener) {
      const key = JSON.stringify([config.venue, config.symbol, config.interval, config.channel || 'candle']);
      let entry = entries.get(key);
      if (!entry) {
        entry = { config: { ...config }, listeners: new Set(), socket: null, retry: null, heartbeat: null,
          generation: 0, attempt: 0, lastTime: -1, lastStamp: -1, receivedAt: 0, connectedAt: null, status: 'connecting', current: null };
        entries.set(key, entry);
      }
      entry.listeners.add(listener);
      listener({ type: 'status', status: entry.status, receivedAt: entry.receivedAt, connectedAt: entry.connectedAt });
      if (!entry.socket && !entry.retry) connect(entry);
      if (entry.current) listener(entry.current);
      let released = false;
      return () => {
        if (released) return; released = true;
        entry.listeners.delete(listener);
        if (!entry.listeners.size) {
          entry.generation += 1; cancel(entry.retry); cancel(entry.heartbeat);
          entry.socket?.close(); entries.delete(key);
        }
      };
    }
    function emit(entry, event) { entry.listeners.forEach(fn => fn(event)); }
    function status(entry, value) {
      entry.status = value;
      emit(entry, { type: 'status', status: value, receivedAt: entry.receivedAt, connectedAt: entry.connectedAt });
    }
    function connect(entry) {
      const c = entry.config;
      const trades = c.channel === 'trades';
      const iv = trades ? (['bybit', 'binance', 'hyperliquid'].includes(c.venue) ? 'trades' : null) : wireInterval(c.venue, c.interval);
      if (!iv || !WS) { status(entry, 'unsupported'); return; }
      const generation = ++entry.generation;
      const active = () => entry.generation === generation && entry.listeners.size > 0;
      status(entry, 'connecting');
      let ws;
      const failed = () => {
        if (!active()) return;
        entry.generation += 1;
        cancel(entry.heartbeat); entry.socket = null; entry.connectedAt = null;
        try { ws?.close(); } catch (_) { /* already closed */ }
        status(entry, 'disconnected');
        const delay = Math.min(30000, 1000 * 2 ** Math.min(entry.attempt++, 5)) * (0.75 + random() * 0.25);
        entry.retry = later(() => { entry.retry = null; if (entry.listeners.size) connect(entry); }, delay);
      };
      try {
        const stream = trades ? 'aggTrade' : `kline_${iv}`;
        const url = c.venue === 'bybit' ? 'wss://stream.bybit.com/v5/public/linear' : c.venue === 'hyperliquid' ? 'wss://api.hyperliquid.xyz/ws' : `wss://fstream.binance.com/market/ws/${encodeURIComponent(c.symbol.toLowerCase())}@${stream}`;
        ws = new WS(url); entry.socket = ws;
        entry.heartbeat = later(failed, 15000); // bound handshake even if no callbacks arrive
        ws.onopen = () => {
          if (!active()) return;
          cancel(entry.heartbeat);
          entry.receivedAt = now(); entry.connectedAt = entry.receivedAt;
          if (c.venue === 'bybit') ws.send(JSON.stringify({ op: 'subscribe', args: trades ? [`publicTrade.${c.symbol}`] : [`kline.${iv}.${c.symbol}`, `publicTrade.${c.symbol}`] }));
          if (c.venue === 'hyperliquid') ws.send(JSON.stringify({ method: 'subscribe', subscription: trades ? { type: 'trades', coin: c.symbol } : { type: 'candle', coin: c.symbol, interval: iv } }));
          status(entry, 'connected');
          const beat = () => {
            if (!active()) return;
            // Binance pushes no app-level pongs; quiet aggTrade streams get a longer silence budget
            // (gaps are still detected exactly through aggregate trade id continuity).
            if (now() - entry.receivedAt > (trades && c.venue === 'binance' ? 300000 : 75000)) { failed(); return; }
            try {
              if (c.venue === 'bybit') ws.send(JSON.stringify({ op: 'ping' }));
              if (c.venue === 'hyperliquid') ws.send(JSON.stringify({ method: 'ping' }));
            } catch (_) { failed(); return; }
            entry.heartbeat = later(beat, 20000);
          };
          entry.heartbeat = later(beat, 20000);
        };
        ws.onmessage = message => {
          if (!active()) return;
          let data;
          try { data = JSON.parse(message.data); } catch (_) { return; }
          entry.receivedAt = now();
          if (data.success === false || data.channel === 'error') { failed(); return; }
          const events = decode(c, iv, data);
          for (const event of events) {
            if (event.type === 'candle') {
              const time = event.candle.time;
              if (time < entry.lastTime || (time === entry.lastTime && event.timestamp < entry.lastStamp)) continue;
              entry.lastTime = time; entry.lastStamp = event.timestamp;
              entry.current = { ...event, receivedAt: now() };
              entry.attempt = 0; status(entry, 'live'); emit(entry, entry.current);
            } else { entry.attempt = 0; status(entry, 'live'); emit(entry, { ...event, receivedAt: now() }); }
          }
        };
        ws.onclose = failed; ws.onerror = failed;
      } catch (_) { failed(); }
    }
    // Raw venue trade rows (exact decimal strings kept) for order-flow consumers.
    function subscribeTrades(config, listener) {
      return subscribe({ venue: config.venue, symbol: config.symbol, interval: null, channel: 'trades' }, listener);
    }
    return { subscribe, subscribeTrades, dispose() { for (const entry of entries.values()) {
      entry.listeners.clear(); entry.generation += 1; cancel(entry.retry); cancel(entry.heartbeat); entry.socket?.close();
    } entries.clear(); }, get size() { return entries.size; } };
  }
  function wireInterval(venue, interval) {
    if (venue === 'bybit') return ['1','3','5','15','30','60','120','240','360','720','D','W','M'].includes(interval) ? interval : null;
    if (!['binance', 'hyperliquid'].includes(venue)) return null;
    return { '1':'1m','3':'3m','5':'5m','15':'15m','30':'30m','60':'1h','120':'2h','240':'4h','480':'8h','720':'12h',D:'1d','3D':'3d',W:'1w',M:'1M' }[interval] || null;
  }
  function candleEvent(c, values, timestamp, closed) {
    const [start, end, open, high, low, close, volume] = values.map(Number);
    if (![start,end,open,high,low,close,volume,Number(timestamp)].every(Number.isFinite) || start <= 0 || end <= start || Math.min(open,high,low,close) <= 0 || volume < 0 || high < Math.max(open,close,low) || low > Math.min(open,close,high)) return null;
    return { type: 'candle', venue: c.venue, symbol: c.symbol, interval: c.interval, timestamp: Number(timestamp), closed: Boolean(closed),
      candle: { time: Math.floor(start / 1000), closeTime: end, open, high, low, close, volume } };
  }
  function decodeTrades(c, message) {
    let rows = [];
    if (c.venue === 'bybit' && message.topic === `publicTrade.${c.symbol}` && Array.isArray(message.data)) rows = message.data.filter(t => t?.s === c.symbol);
    else if (c.venue === 'binance' && message.e === 'aggTrade' && message.s === c.symbol) rows = [message];
    else if (c.venue === 'hyperliquid' && message.channel === 'trades' && Array.isArray(message.data)) rows = message.data.filter(t => t?.coin === c.symbol);
    return rows.length ? [{ type: 'trades', venue: c.venue, symbol: c.symbol, rows }] : [];
  }
  function decode(c, iv, message) {
    if (c.channel === 'trades') return decodeTrades(c, message);
    const events = [];
    if (c.venue === 'bybit') {
      if (message.topic === `kline.${iv}.${c.symbol}` && Array.isArray(message.data)) for (const k of message.data) {
        if (k.interval !== iv) continue;
        events.push(candleEvent(c, [k.start,k.end,k.open,k.high,k.low,k.close,k.volume], k.timestamp ?? message.ts, k.confirm));
      }
      if (message.topic === `publicTrade.${c.symbol}` && Array.isArray(message.data)) for (const t of message.data) {
        if (t.s === c.symbol && Number(t.p) > 0 && Number.isFinite(Number(t.T))) events.push({ type: 'trade', venue: c.venue, symbol: c.symbol, interval: c.interval, price: Number(t.p), timestamp: Number(t.T) });
      }
    } else if (c.venue === 'binance') {
      const k = message.k;
      if (message.e === 'kline' && message.s === c.symbol && k?.s === c.symbol && k.i === iv) events.push(candleEvent(c, [k.t,k.T,k.o,k.h,k.l,k.c,k.v], message.E, k.x));
    } else if (c.venue === 'hyperliquid' && message.channel === 'candle') {
      const list = Array.isArray(message.data) ? message.data : [message.data];
      for (const k of list) if (k?.s === c.symbol && k.i === iv) events.push(candleEvent(c, [k.t,k.T,k.o,k.h,k.l,k.c,k.v], k.T, false));
    }
    return events.filter(Boolean);
  }
  return { createHub, wireInterval };
});
