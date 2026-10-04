import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from core import connect

@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING':True,'DATA_DIR':str(tmp_path/'data'),'DEMO':True})

@pytest.fixture
def client(app):return app.test_client()


def login(client,email,password):
    token=client.get('/api/session').json['csrf_token']
    result=client.post('/api/login',json={'email':email,'password':password},headers={'X-CSRF-Token':token})
    assert result.status_code==200,result.json
    return result.json


def post(client,path,data=None,**kwargs):
    token=client.get('/api/session').json['csrf_token']
    return client.post(path,json={} if data is None else data,headers={'X-CSRF-Token':token},**kwargs)


def user(app,email):
    db=connect(app.config['DB_PATH'])
    try:return dict(db.execute('SELECT id,name,email,role,unit FROM users WHERE email=?',(email,)).fetchone())
    finally:db.close()
