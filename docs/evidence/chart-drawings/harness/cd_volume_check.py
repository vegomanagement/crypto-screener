"""Σ volume of backfilled 5s bars per 1m vs Binance USDT-M 1m kline volume (real network)."""
import json
import sys
import urllib.request
from decimal import Decimal

BASE = 'http://127.0.0.1:5089'
out_path = sys.argv[1]
payload = json.load(urllib.request.urlopen(f'{BASE}/api/seconds?exchange=binance&symbol=BTCUSDT&minutes=10&seconds=5', timeout=60))
bars = payload['bars']
groups = {}
for b in bars:
    m = b['t'] // 60000 * 60000
    g = groups.setdefault(m, {'v': Decimal(0), 'n': 0, 'bars': 0})
    g['v'] += Decimal(b['v']); g['n'] += b['n']; g['bars'] += 1
first_full = -(-payload['complete_from_ms'] // 60000) * 60000
last_full_end = payload['as_of'] // 60000 * 60000
minutes = [m for m in sorted(groups) if m >= first_full and m + 60000 <= last_full_end]
url = (f'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&startTime={minutes[0]}'
       f'&endTime={minutes[-1]}&limit=100')
klines = {int(k[0]): k for k in json.load(urllib.request.urlopen(url, timeout=30))}
rows = []
for m in minutes:
    k = klines.get(m)
    rows.append({'minute_ms': m, 'sum_5s_volume': str(groups[m]['v']), 'kline_volume': k[5] if k else None,
                 'equal': bool(k) and groups[m]['v'] == Decimal(k[5]), 'bars_5s': groups[m]['bars'],
                 'agg_trades': groups[m]['n'], 'kline_trades': int(k[8]) if k else None})
report = {'as_of': payload['as_of'], 'complete_from_ms': payload['complete_from_ms'], 'requests': payload['requests'],
          'truncated': payload['truncated'], 'trades': payload['trades'], 'bars': len(bars),
          'full_minutes_compared': len(rows), 'exact_equal': sum(r['equal'] for r in rows), 'rows': rows,
          'note': 'kline_trades counts raw fills; agg_trades counts aggregate trades (one aggTrade may hold several fills).'}
json.dump(report, open(out_path, 'w'), indent=1)
print(json.dumps({k: v for k, v in report.items() if k != 'rows'}))
for r in rows:
    print(r['minute_ms'], r['sum_5s_volume'], r['kline_volume'], r['equal'], r['bars_5s'])

# Mismatch analysis: adjacent-pair sums and multi-fill aggTrades just before the boundary.
for i, r in enumerate(rows):
    if r['equal']:
        continue
    end = r['minute_ms'] + 60000
    agg = json.load(urllib.request.urlopen(
        f'https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&startTime={end - 2000}&endTime={end - 1}&limit=1000', timeout=30))
    multi = [{'a': t['a'], 'T': t['T'], 'q': t['q'], 'fills': t['l'] - t['f'] + 1} for t in agg if t['l'] > t['f']]
    r['diff'] = str(Decimal(r['sum_5s_volume']) - Decimal(r['kline_volume']))
    r['multi_fill_aggtrades_last_2s'] = multi[-5:]
    if i + 1 < len(rows):
        nxt = rows[i + 1]
        r['pair_sum_equal'] = (Decimal(r['sum_5s_volume']) + Decimal(nxt['sum_5s_volume'])
                               == Decimal(r['kline_volume']) + Decimal(nxt['kline_volume']))
    print('mismatch', r['minute_ms'], r['diff'], r.get('pair_sum_equal'), multi[-5:])
report['rows'] = rows
json.dump(report, open(out_path, 'w'), indent=1)
