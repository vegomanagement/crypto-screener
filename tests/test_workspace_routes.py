"""Read-only workspace routing, independent of live exchange or model calls."""
from unittest.mock import patch

import screener


def test_workspace_serves_local_assets_and_keeps_legacy_viewer():
    client = screener.app.test_client()
    response = client.get('/ui')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '/static/workspace.js' in html
    assert '/static/workspace.css' in html
    assert '/static/vendor/lightweight-charts.standalone.production.js' in html
    legacy = client.get('/ui/legacy')
    assert legacy.status_code == 200
    assert 'Chart Viewer' in legacy.get_data(as_text=True)


def test_workspace_interval_menu_is_explicit_about_unsupported_seconds():
    response = screener.app.test_client().get('/api/chart/intervals')
    assert response.status_code == 200
    intervals = response.get_json()['intervals']
    available = {i['value'] for i in intervals if i['available']}
    assert {'1', '3', '10', '12', '45', '240', '480', '720', 'D', '2D', '3D', 'W', 'M'} <= available
    assert any(not i['available'] and i['label'] == '30s' for i in intervals)
    labels = {i['value']: i['label'] for i in intervals}
    assert labels['240'] == '4h'
    assert labels['720'] == '12h'
    assert labels['D'] == 'D'


def test_workspace_chart_rejects_unknown_interval_without_network():
    with patch.object(screener, '_klines') as fetch:
        response = screener.app.test_client().get('/api/chart?symbol=BTCUSDT&interval=30s')
    assert response.status_code == 400
    fetch.assert_not_called()
