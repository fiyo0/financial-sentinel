"""
Unit and integration tests for Deep Dive Archive & Repository.
"""
import pytest
from fastapi.testclient import TestClient
from web.app import app, orchestrator
from config import config

@pytest.fixture
def client():
    return TestClient(app)

def test_statestore_deepdive_crud():
    store = orchestrator.state_store
    user_id = 'usr_test_dd'
    with store._get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (user_id, "test_dd", "test_dd@example.com", "hash", "user")
        )
        conn.commit()

    # 1. Save deep dive
    dd_id = store.save_deepdive(
        user_id=user_id,
        ticker='NVDA',
        company_name='NVIDIA Corp',
        current_price=125.50,
        verdict='BULLISH',
        conviction_score=90.0,
        technicals={'rsi_14': 45.0, 'macd_status': 'BULLISH'},
        sentiment={'retail_bull_pct': 75.0},
        analysis_text='Institutional deep dive report content for NVDA.'
    )
    assert dd_id.startswith('dd_')

    # 2. Get user deepdives list
    dds = store.get_user_deepdives(user_id=user_id)
    assert len(dds) >= 1
    nvda_item = next(d for d in dds if d['id'] == dd_id)
    assert nvda_item['ticker'] == 'NVDA'
    assert nvda_item['verdict'] == 'BULLISH'
    assert nvda_item['current_price'] == 125.50

    # 3. Get full detail by id
    detail = store.get_deepdive_by_id(dd_id, user_id=user_id)
    assert detail is not None
    assert detail['ticker'] == 'NVDA'
    assert detail['payload'] is not None
    assert detail['payload']['ticker'] == 'NVDA'
    assert detail['technicals']['rsi_14'] == 45.0

    # 4. Delete deep dive
    del_ok = store.delete_deepdive(dd_id, user_id=user_id)
    assert del_ok is True
    assert store.get_deepdive_by_id(dd_id, user_id=user_id) is None

def test_api_deepdives_endpoints(client, monkeypatch):
    correct_pwd = config.dashboard_password or 'sentinel_admin'
    headers = {'X-Sentinel-Auth': correct_pwd}
    cookies = {'sentinel_auth': correct_pwd}

    # 1. Mock analyze single ticker
    def mock_analyze_single_ticker(*args, **kwargs):
        return """<b>VERDICT: BULLISH</b>
• Conviction Score: 88%
• Upside Target: $150.00"""

    monkeypatch.setattr(orchestrator.analysis_agent, 'analyze_single_ticker', mock_analyze_single_ticker)

    # 2. Call /api/analyze/NVDA
    resp = client.post('/api/analyze/NVDA', headers=headers, cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert data['status'] == 'success'
    assert 'deepdive_id' in data
    dd_id = data['deepdive_id']
    assert dd_id.startswith('dd_')

    # 3. GET /api/deepdives
    resp_list = client.get('/api/deepdives', headers=headers, cookies=cookies)
    assert resp_list.status_code == 200
    list_data = resp_list.json()
    assert list_data['status'] == 'success'
    assert any(d['id'] == dd_id for d in list_data['deepdives'])

    # 4. GET /api/deepdives/{id}
    resp_detail = client.get(f'/api/deepdives/{dd_id}', headers=headers, cookies=cookies)
    assert resp_detail.status_code == 200
    det_data = resp_detail.json()
    assert det_data['status'] == 'success'
    assert det_data['deepdive']['ticker'] == 'NVDA'

    # 5. DELETE /api/deepdives/{id}
    resp_del = client.delete(f'/api/deepdives/{dd_id}', headers=headers, cookies=cookies)
    assert resp_del.status_code == 200
    assert resp_del.json()['status'] == 'success'

    # Verify 404 after deletion
    resp_gone = client.get(f'/api/deepdives/{dd_id}', headers=headers, cookies=cookies)
    assert resp_gone.status_code == 404
