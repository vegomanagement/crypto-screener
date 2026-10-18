"""Cross-check breaker / IDM / EQH-EQL labels of /api/agent/mtf against Binance USDT-M klines."""
import json
import sys
import urllib.request
from decimal import Decimal

D = '/Users/maxbelov/.hermes/cache/scratch/swing-htf/live/'
IV = {'240': '4h', 'D': '1d', 'W': '1w', '60': '1h'}
out, bad, n = [], 0, 0


def kl(sym, tf):
    u = f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={IV[tf]}&limit=1500'
    rows = json.loads(urllib.request.urlopen(u, timeout=30).read())
    return {int(r[0]): {'h': Decimal(r[2]), 'l': Decimal(r[3]), 'c': Decimal(r[4]), 'close': int(r[6]) + 1} for r in rows}


def chk(cond, msg):
    global bad, n
    n += 1
    if not cond:
        bad += 1
    out.append(('OK  ' if cond else 'BAD ') + msg)


for f, sym in (('btc4h', 'BTCUSDT'), ('btc1d', 'BTCUSDT'), ('ethd', 'ETHUSDT')):
    b = json.load(open(D + f + '.json'))['mtf']
    for tf, a in b['tfs'].items():
        if f == 'btc1d' and tf in ('240', 'W'):
            continue                                    # identical reads already checked via btc4h
        K = kl(sym, tf)
        ts_sorted = sorted(K)
        s = a['smc']
        D_ = lambda x: Decimal(str(x))  # noqa: E731
        for z in s['breakers']:
            c = K.get(z['ts'])
            chk(c is not None and D_(z['top']) == c['h'] and D_(z['bottom']) == c['l'],
                f"{sym} {IV[tf]} breaker {z['dir']} {z['bottom']}–{z['top']} = venue H/L of bar {z['ts']}")
            later = [K[t] for t in ts_sorted if t > z['ts'] and K[t]['close'] <= z['confirm_ts']]
            through = any((x['c'] < c['l']) if z['dir'] == 'bear' else (x['c'] > c['h']) for x in later) if c else False
            chk(through, f"{sym} {IV[tf]} breaker: a venue close beyond the far edge before its confirmation")
        x = s['inducement']
        if x:
            c = K.get(x['ts'])
            key = 'l' if x['side'] == 'low' else 'h'
            chk(c is not None and D_(x['price']) == c[key], f"{sym} {IV[tf]} IDM {x['side']} {x['price']} = venue {key} of bar {x['ts']}")
            after = [(t, K[t]) for t in ts_sorted if K[t]['close'] > x['confirm_ts'] and K[t]['close'] <= b['ts'] + 1]
            hit = next((t for t, k in after if ((k['l'] < D_(x['price'])) if key == 'l' else (k['h'] > D_(x['price'])))), None)
            chk((x['state'] == 'swept') == (hit is not None) and (hit is None or K[hit]['close'] == x['swept_ts']),
                f"{sym} {IV[tf]} IDM state {x['state']} (first venue bar through it: {hit})")
        for q in s['equal_levels']:
            c1, c2 = K.get(q['first_ts']), K.get(q['ts'])
            key = 'h' if q['side'] == 'high' else 'l'
            ok = c1 is not None and c2 is not None and D_(q['price']) in (c1[key], c2[key])
            ok = ok and abs(c1[key] - c2[key]) <= Decimal('0.1') * Decimal(str(a['atr14'])) * 3
            chk(ok, f"{sym} {IV[tf]} {q['kind']} {q['price']} = venue {key} of {q['first_ts']} / {q['ts']} "
                    f"(gap {None if not (c1 and c2) else c1[key] - c2[key]})")
            mid = [K[t] for t in ts_sorted if t > q['ts'] and K[t]['close'] <= b['ts'] + 1]
            chk(not any((k['h'] > D_(q['price'])) if key == 'h' else (k['l'] < D_(q['price'])) for k in mid[3:]),
                f"{sym} {IV[tf]} {q['kind']} still resting on venue bars")
print('\n'.join(out))
print(f'checks {n}, mismatches {bad}')
sys.exit(1 if bad else 0)
