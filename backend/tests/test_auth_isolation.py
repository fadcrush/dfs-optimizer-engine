from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import file_service


def test_slates_require_auth(isolated_upload_dirs, monkeypatch):
    monkeypatch.setenv("DFS_DISABLE_AUTH", "0")
    from routers.slates import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get('/api/slates')
    assert response.status_code == 401


def test_slates_are_isolated_per_user(make_authed_client, isolated_upload_dirs):
    from routers.slates import router

    user1 = make_authed_client(router, user_id='user-1')
    user2 = make_authed_client(router, user_id='user-2')

    csv_content = 'Name,Team\nAlpha,AAA\nBeta,BBB\n'
    upload = user1.post(
        '/api/slates/upload?platform=draftkings&sport=nba',
        files={'file': ('slate.csv', csv_content, 'text/csv')},
    )
    assert upload.status_code == 200
    slate = upload.json()['slate']

    user1_list = user1.get('/api/slates')
    user2_list = user2.get('/api/slates')
    assert user1_list.status_code == 200
    assert user2_list.status_code == 200
    assert len(user1_list.json()['slates']) == 1
    assert user2_list.json()['slates'] == []

    denied_download = user2.get(f"/api/slates/{slate['id']}/download")
    assert denied_download.status_code == 404

    allowed_download = user1.get(f"/api/slates/{slate['id']}/download")
    assert allowed_download.status_code == 200


def test_optimizer_download_is_isolated_per_user(make_authed_client, isolated_upload_dirs):
    from routers.optimizer import router

    user1_dir = file_service.get_user_storage_dir('lineup', 'user-1')
    lineup_file = user1_dir / 'lineups_user1.csv'
    lineup_file.write_text('Entry ID,PG\n1,Player One\n', encoding='utf-8')

    user1 = make_authed_client(router, user_id='user-1')
    user2 = make_authed_client(router, user_id='user-2')

    allowed = user1.get(f'/api/optimizer/download/{lineup_file.name}')
    denied = user2.get(f'/api/optimizer/download/{lineup_file.name}')

    assert allowed.status_code == 200
    assert denied.status_code == 404