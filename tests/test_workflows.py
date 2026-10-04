import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
import domain
from core import Problem, code_digest, connect, transaction
from conftest import login,post,user

RECEPTION=('reception@aangan.demo','Lobby@2026')
AARAV=('aarav@aangan.demo','Home@2026')
NISHA=('nisha@aangan.demo','Home@2026')


def test_boot_health_and_security_headers(client):
    health=client.get('/api/health');assert health.status_code==200 and health.json['status']=='ok'
    page=client.get('/');assert page.status_code==200 and b'Aangan' in page.data
    assert "script-src 'self'" in page.headers['Content-Security-Policy']
    assert page.headers['X-Frame-Options']=='DENY'
    assert health.headers['Cache-Control']=='no-store'


def test_unique_cookie_and_rotation(client,app):
    first=client.get('/api/session');assert 'aangan_session=' in first.headers['Set-Cookie']
    old=first.json['csrf_token'];signed=login(client,*RECEPTION)
    assert signed['csrf_token']!=old
    assert client.post('/api/logout',json={},headers={'X-CSRF-Token':old}).status_code==403
    assert post(client,'/api/logout').status_code==200
    assert client.get('/api/parcels').status_code==401


@pytest.mark.parametrize('path',['/api/parcels','/api/residents','/api/parcels/1/events'])
def test_login_required(client,path):assert client.get(path).status_code==401


@pytest.mark.parametrize('path',['/api/login','/api/logout','/api/parcels','/api/parcels/1/reissue','/api/parcels/1/handover'])
def test_csrf_required(client,path):assert client.post(path,json={}).status_code==403


def test_cross_origin_rejected(client):
    token=client.get('/api/session').json['csrf_token']
    r=client.post('/api/login',json={'email':RECEPTION[0],'password':RECEPTION[1]},headers={'X-CSRF-Token':token,'Origin':'https://attacker.example'})
    assert r.status_code==403


@pytest.mark.parametrize('password',['wrong','',None,100])
def test_invalid_login(client,password):
    response=post(client,'/api/login',{'email':RECEPTION[0],'password':password})
    assert response.status_code in (400,401)
    assert client.get('/api/parcels').status_code==401


def test_login_throttle(client):
    for _ in range(8):assert post(client,'/api/login',{'email':RECEPTION[0],'password':'wrong'}).status_code==401
    assert post(client,'/api/login',{'email':RECEPTION[0],'password':RECEPTION[1]}).status_code==429


def test_no_demo_clean_start(tmp_path):
    from app import create_app
    app=create_app({'TESTING':True,'DATA_DIR':str(tmp_path/'empty'),'DEMO':False})
    client=app.test_client();assert client.get('/api/health').json['demo'] is False
    db=connect(app.config['DB_PATH']);assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0;db.close()


def test_restart_preserves_secret_and_rows(app):
    from app import create_app
    other=create_app({'TESTING':True,'DATA_DIR':app.config['DATA_DIR'],'DEMO':True})
    assert other.secret_key==app.secret_key
    db=connect(other.config['DB_PATH']);assert db.execute('SELECT COUNT(*) FROM parcels').fetchone()[0]==4;db.close()


def test_passwords_and_codes_are_hashed(app):
    db=connect(app.config['DB_PATH'])
    try:
        assert db.execute('SELECT password_hash FROM users WHERE id=1').fetchone()[0].startswith('scrypt:')
        code=db.execute('SELECT code_hash FROM parcels WHERE id=1').fetchone()[0]
        assert code==code_digest(app.secret_key,'246810') and '246810' not in code
    finally:db.close()


def test_resident_scope_and_no_code_leaks(client):
    login(client,*AARAV);rows=client.get('/api/parcels').json['parcels']
    assert {r['id'] for r in rows}=={1,4}
    dumped=json.dumps(rows);assert 'code_hash' not in dumped and '246810' not in dumped
    assert client.get('/api/parcels/2/events').status_code==404
    assert client.get('/api/residents').status_code==403
    assert '246810' not in json.dumps(client.get('/api/parcels/1/events').json)


def test_reception_lists_all_residents(client):
    login(client,*RECEPTION)
    assert len(client.get('/api/parcels').json['parcels'])==4
    assert len(client.get('/api/residents').json['residents'])==3


def test_arrival_and_fresh_single_use_code(client,app):
    login(client,*RECEPTION)
    response=post(client,'/api/parcels',{'resident_id':2,'sender':'Tara Books','description':'One hardback'})
    assert response.status_code==201
    data=response.json;assert len(data['pickup_code'])==6 and data['pickup_code'].isdigit()
    assert data['parcel']['revision']==1
    db=connect(app.config['DB_PATH']);stored=db.execute('SELECT * FROM parcels WHERE id=?',(data['parcel']['id'],)).fetchone();db.close()
    assert stored['code_hash']==code_digest(app.secret_key,data['pickup_code'])
    assert stored['code_expires_at']-stored['arrived_at']==86400


@pytest.mark.parametrize('data',[
 {},{'resident_id':True,'sender':'Store','description':'Box'},
 {'resident_id':1,'sender':'Store','description':'Box'},
 {'resident_id':999,'sender':'Store','description':'Box'},
 {'resident_id':2,'sender':'','description':'Box'},
 {'resident_id':2,'sender':'A'*81,'description':'Box'},
 {'resident_id':2,'sender':'Store','description':'x'*221},
 {'resident_id':2,'sender':'Store','description':'\x00box'},
 {'resident_id':2,'sender':123,'description':'Box'},
])
def test_arrival_validation_leaves_no_partial_rows(client,app,data):
    login(client,*RECEPTION)
    assert post(client,'/api/parcels',data).status_code==400
    db=connect(app.config['DB_PATH']);assert db.execute('SELECT COUNT(*) FROM parcels').fetchone()[0]==4;db.close()


def test_resident_cannot_record_or_reissue(client):
    login(client,*AARAV)
    assert post(client,'/api/parcels',{'resident_id':2,'sender':'Store','description':'Box'}).status_code==403
    assert post(client,'/api/parcels/1/reissue',{'revision':1}).status_code==403


@pytest.mark.parametrize('code',['12345','1234567','abcdef',123456,None,'١٢٣٤٥٦'])
def test_invalid_code_format(client,code):
    login(client,*AARAV)
    assert post(client,'/api/parcels/1/handover',{'revision':1,'pickup_code':code,'retry_key':'retry_test_123'}).status_code==400


def test_handover_single_event_and_safe_retry(client,app):
    login(client,*AARAV)
    payload={'revision':1,'pickup_code':'246810','retry_key':'handover_retry_1'}
    first=post(client,'/api/parcels/1/handover',payload);assert first.status_code==200 and first.json['replayed'] is False
    assert first.json['parcel']['status']=='collected' and first.json['parcel']['revision']==2
    retry=post(client,'/api/parcels/1/handover',payload);assert retry.status_code==200 and retry.json['replayed'] is True
    payload['retry_key']='handover_retry_2';assert post(client,'/api/parcels/1/handover',payload).status_code==409
    db=connect(app.config['DB_PATH'])
    assert db.execute('SELECT COUNT(*) FROM handovers WHERE parcel_id=1').fetchone()[0]==1
    assert db.execute("SELECT COUNT(*) FROM events WHERE parcel_id=1 AND kind='collected'").fetchone()[0]==1
    db.close()


def test_retry_key_cannot_change_payload_or_bypass_ownership(client,app):
    login(client,*AARAV)
    payload={'revision':1,'pickup_code':'246810','retry_key':'handover_retry_1'}
    assert post(client,'/api/parcels/1/handover',payload).status_code==200
    payload['pickup_code']='000000';assert post(client,'/api/parcels/1/handover',payload).status_code==409
    login(client,*NISHA);payload['pickup_code']='246810'
    assert post(client,'/api/parcels/1/handover',payload).status_code==404
    payload['pickup_code']='135790'
    assert post(client,'/api/parcels/2/handover',payload).status_code==409
    login(client,*RECEPTION);payload['pickup_code']='246810'
    assert post(client,'/api/parcels/1/handover',payload).status_code==409


def test_wrong_code_persists_and_locks_then_reissue_resets(client,app):
    login(client,*RECEPTION)
    for i in range(5):
        assert post(client,'/api/parcels/1/handover',{'revision':1,'pickup_code':'000000','retry_key':f'wrong_code_{i}'}).status_code==400
    assert post(client,'/api/parcels/1/handover',{'revision':1,'pickup_code':'246810','retry_key':'correct_code_key'}).status_code==423
    data=post(client,'/api/parcels/1/reissue',{'revision':1}).json
    assert data['parcel']['revision']==2 and data['parcel']['code_state']=='active'
    assert post(client,'/api/parcels/1/handover',{'revision':2,'pickup_code':data['pickup_code'],'retry_key':'fresh_code_key'}).status_code==200


def test_expired_code_cannot_collect_and_reissue_invalidates_old(client):
    login(client,*RECEPTION)
    assert post(client,'/api/parcels/3/handover',{'revision':1,'pickup_code':'112233','retry_key':'expired_code_key'}).status_code==409
    fresh=post(client,'/api/parcels/1/reissue',{'revision':1}).json
    assert post(client,'/api/parcels/1/handover',{'revision':2,'pickup_code':'246810','retry_key':'invalidated_code_key'}).status_code==400
    assert post(client,'/api/parcels/1/handover',{'revision':2,'pickup_code':fresh['pickup_code'],'retry_key':'fresh_code_key'}).status_code==200
    assert post(client,'/api/parcels/1/reissue',{'revision':2}).status_code==409


@pytest.mark.parametrize('revision',[None,True,0,-1,'1',2])
def test_revision_guard(client,revision):
    login(client,*RECEPTION)
    expected=409 if revision==2 and not isinstance(revision,bool) else 400
    assert post(client,'/api/parcels/1/reissue',{'revision':revision}).status_code==expected


@pytest.mark.parametrize('key',['short','space in key','bad/key','x'*91,None])
def test_retry_key_limits(client,key):
    login(client,*AARAV)
    assert post(client,'/api/parcels/1/handover',{'revision':1,'pickup_code':'246810','retry_key':key}).status_code==400


def test_racing_handover_has_one_winner(app):
    actor=user(app,AARAV[0]);barrier=Barrier(2)
    def worker(key):
        barrier.wait()
        try:return domain.handover(app.config['DB_PATH'],app.secret_key,actor,1,{'revision':1,'pickup_code':'246810','retry_key':key})
        except Problem as e:return e.status
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,['race_key_one','race_key_two']))
    assert sum(isinstance(r,dict) for r in results)==1 and 409 in results
    db=connect(app.config['DB_PATH']);assert db.execute('SELECT COUNT(*) FROM handovers').fetchone()[0]==1;db.close()


def test_simultaneous_identical_retry_returns_same_handover(app):
    actor=user(app,AARAV[0]);barrier=Barrier(2)
    def worker(_):
        barrier.wait();return domain.handover(app.config['DB_PATH'],app.secret_key,actor,1,{'revision':1,'pickup_code':'246810','retry_key':'same_retry_key'})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,[1,2]))
    assert sorted(r['replayed'] for r in results)==[False,True]


def test_handover_and_reissue_race_is_serialized(app):
    actor=user(app,RECEPTION[0]);barrier=Barrier(2)
    def worker(kind):
        barrier.wait()
        try:
            if kind=='collect':return domain.handover(app.config['DB_PATH'],app.secret_key,actor,1,{'revision':1,'pickup_code':'246810','retry_key':'race_collection'})
            return domain.reissue(app.config['DB_PATH'],app.secret_key,actor,1,{'revision':1})
        except Problem as error:return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,['collect','reissue']))
    assert sum(isinstance(r,dict) for r in results)==1 and 409 in results


def test_audit_failure_rolls_back_handover(app,monkeypatch):
    actor=user(app,AARAV[0])
    def fail(*_):raise RuntimeError('Simulated audit failure')
    monkeypatch.setattr(domain,'event',fail)
    with pytest.raises(RuntimeError):domain.handover(app.config['DB_PATH'],app.secret_key,actor,1,{'revision':1,'pickup_code':'246810','retry_key':'rollback_key'})
    db=connect(app.config['DB_PATH']);assert db.execute('SELECT status FROM parcels WHERE id=1').fetchone()[0]=='waiting';assert db.execute('SELECT COUNT(*) FROM handovers').fetchone()[0]==0;db.close()


def test_unknown_record_and_bad_json(client):
    login(client,*RECEPTION)
    assert client.get('/api/parcels/999/events').status_code==404
    token=client.get('/api/session').json['csrf_token']
    assert client.post('/api/parcels',data='not json',content_type='application/json',headers={'X-CSRF-Token':token}).status_code==400
    assert client.get('/missing').status_code==404


def test_audit_and_handover_rows_cannot_be_rewritten(client,app):
    login(client,*AARAV);assert post(client,'/api/parcels/1/handover',{'revision':1,'pickup_code':'246810','retry_key':'immutable_record'}).status_code==200
    db=connect(app.config['DB_PATH'])
    for statement in ['UPDATE events SET detail=\'Tampered\' WHERE parcel_id=1','DELETE FROM events WHERE parcel_id=1','UPDATE handovers SET actor_id=1 WHERE parcel_id=1','DELETE FROM handovers WHERE parcel_id=1']:
        with pytest.raises(sqlite3.IntegrityError):db.execute(statement)
    db.close()


def test_deleted_or_invalid_session_identity_cannot_authorize(client):
    with client.session_transaction() as session:session['user_id']=999
    assert client.get('/api/parcels').status_code==401
    assert client.get('/api/session').json['user'] is None


def test_corrupt_secret_is_rejected_without_overwriting(tmp_path):
    from app import create_app
    folder=tmp_path/'corrupt';folder.mkdir();(folder/'.session-secret').write_text('wrong')
    with pytest.raises(RuntimeError):create_app({'TESTING':True,'DATA_DIR':str(folder),'DEMO':False})
    assert (folder/'.session-secret').read_text()=='wrong'


@pytest.mark.parametrize('password',[' Lobby@2026','Lobby@2026 '])
def test_password_whitespace_is_not_silently_normalized(client,password):
    assert post(client,'/api/login',{'email':RECEPTION[0],'password':password}).status_code==401
