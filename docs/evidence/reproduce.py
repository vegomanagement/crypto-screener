"""Offline audit reproductions; synthetic fixtures, no market performance claims."""
import os
import sys
import json
import inspect
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, '/Users/maxbelov/Projects/crypto-screener-audit')
os.environ.update(TELEGRAM_TOKEN='AUDIT_DISABLED', TELEGRAM_CHAT_ID='0', ANTHROPIC_API_KEY='AUDIT_DISABLED')
import screener
from backtest import _simulate_outcome
from bt_market import _slice_klines_up_to

results = {}
client = screener.app.test_client()
fixture = [{'ts': 0, 'o': 100, 'h': 200, 'l': 90, 'c': 150, 'v': 1}]
assert _slice_klines_up_to(fixture, 300000) == fixture
results['htf_open_time_slice'] = '4H candle opened at 0 is admitted at 5m despite closing later'
assert 'entry' not in inspect.signature(_simulate_outcome).parameters
outcome = _simulate_outcome([{'h': 101, 'l': 99}, {'h': 104, 'l': 99}], 0, 'LONG', 79, 103, None, None, 23, None, None, 10)
assert outcome[1] == 'tp1_hit' and outcome[3] == 23
results['no_entry_fill_check'] = {'intended_entry': 80, 'observed_range': [99, 104], 'result': outcome}
outcome = _simulate_outcome([{'h': 101, 'l': 99}, {'h': 110, 'l': 99}], 0, 'LONG', 98, 103, 105, 108, 1.5, 2.5, 4, 10)
assert outcome[2] == 'TP3'
results['furthest_tp_selected'] = outcome
with patch.object(screener, '_klines', return_value=[{'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 1}]):
    response = client.get('/api/klines?symbol=BTCUSDT&interval=240&limit=1')
    ts = response.json['klines'][0]['ts']
    assert ts % (4 * 60 * 60 * 1000) != 0
    results['fabricated_candle_time'] = {'ts': ts, 'misalignment_ms': ts % 14400000}
with patch.object(screener, 'fetch_market', return_value={'price': 100, 'ema_biases': {'1H': 'bull', '4H': 'bear', '1D': 'bull'}, 'bybit': {'oi': 42}, 'vwap': {'daily': {'value': 100}}}):
    response = client.get('/api/market?symbol=BTCUSDT')
    assert response.status_code == 200
    payload = response.json
    assert all(v is None for v in payload['ema_bias'].values())
    assert payload['open_int'] is None and payload['vwap'] is None
    results['api_schema_loss'] = {k: payload[k] for k in ['ema_bias', 'open_int', 'vwap']}
with patch.object(screener._aggregator, 'submit') as submit:
    response = client.post('/webhook', json={'symbol': 'BTCUSDT', 'signal': 'TEST'})
    assert response.status_code == 200 and submit.call_count == 1
    results['unauthenticated_webhook_accepted'] = response.json
with patch.object(screener, 'cmd_status') as status:
    screener.handle_update({'message': {'chat': {'id': 987654321}, 'text': '/status'}})
    status.assert_called_once_with(987654321)
    results['unapproved_telegram_chat_accepted'] = True
out = Path('/Users/maxbelov/.hermes/cache/scratch/crypto-screener-reproductions.json')
out.write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
print(f'CONFIRMED {len(results)} audit observations with offline fixtures; no signals sent.')
