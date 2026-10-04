"""Parcel lifecycle, one-time codes and audit events under SQLite transactions."""
import hmac
import re
import secrets
from core import (Problem, USER_SCHEMA, add_users, code_digest, connect, now,
                  positive_id, require_revision, require_role, text, transaction)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS parcels (
 id INTEGER PRIMARY KEY, resident_id INTEGER NOT NULL REFERENCES users(id),
 sender TEXT NOT NULL, description TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('waiting','collected')) DEFAULT 'waiting',
 revision INTEGER NOT NULL DEFAULT 1,
 arrived_at INTEGER NOT NULL, code_expires_at INTEGER NOT NULL,
 code_hash TEXT NOT NULL, failed_attempts INTEGER NOT NULL DEFAULT 0,
 collected_at INTEGER, collected_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, parcel_id INTEGER NOT NULL REFERENCES parcels(id),
 actor_id INTEGER NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
 detail TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS handovers (
 parcel_id INTEGER PRIMARY KEY REFERENCES parcels(id),
 actor_id INTEGER NOT NULL REFERENCES users(id),
 retry_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
 completed_at INTEGER NOT NULL
);
'''
DEMO_USERS = [
 ('Kavita Shah','reception@aangan.demo','receptionist','Lobby','Lobby@2026'),
 ('Aarav Patel','aarav@aangan.demo','resident','A-302','Home@2026'),
 ('Nisha Desai','nisha@aangan.demo','resident','B-104','Home@2026'),
 ('Rohan Mehta','rohan@aangan.demo','resident','A-205','Home@2026'),
]


def initialize(path, secret, demo):
    db = connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(USER_SCHEMA + SCHEMA)
    db.close()
    if demo:
        with transaction(path) as db:
            if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                add_users(db, DEMO_USERS)
                for resident, sender, description, code, hours in [
                    (2,'Meera Books','Two paperbacks · small box','246810',24),
                    (3,'Sagar Electronics','Keyboard · sealed carton','135790',24),
                    (4,'Ananya Crafts','Handmade desk organiser','112233',-1),
                    (2,'Tara Studio','Printed photographs · envelope','445566',24),
                ]:
                    at = now()
                    cursor = db.execute('INSERT INTO parcels(resident_id,sender,description,arrived_at,code_expires_at,code_hash) VALUES(?,?,?,?,?,?)',
                        (resident,sender,description,at-7200,at+hours*3600,code_digest(secret,code)))
                    event(db,cursor.lastrowid,1,'arrival','Parcel recorded at the lobby.',at-7200)


def event(db, parcel, actor, kind, detail, at=None):
    db.execute('INSERT INTO events(parcel_id,actor_id,kind,detail,created_at) VALUES(?,?,?,?,?)',
               (parcel,actor,kind,detail,now() if at is None else at))


def visible(db, user, parcel_id):
    row = db.execute('SELECT * FROM parcels WHERE id=?',(positive_id(parcel_id),)).fetchone()
    if row is None or (user['role'] != 'receptionist' and row['resident_id'] != user['id']):
        raise Problem('Parcel not found.',404)
    return row


def public(row, at=None):
    data = {key:row[key] for key in ('id','resident_id','sender','description','status','revision','arrived_at','code_expires_at','collected_at','collected_by')}
    data['code_state'] = 'used' if row['status']=='collected' else ('locked' if row['failed_attempts']>=5 else ('expired' if row['code_expires_at']<= (now() if at is None else at) else 'active'))
    if 'resident_name' in row.keys():
        data['resident_name'],data['unit']=row['resident_name'],row['unit']
    return data


def list_parcels(db,user):
    condition, args = ('',()) if user['role']=='receptionist' else ('WHERE p.resident_id=?',(user['id'],))
    rows=db.execute(f'SELECT p.*,u.name AS resident_name,u.unit FROM parcels p JOIN users u ON u.id=p.resident_id {condition} ORDER BY p.status DESC,p.arrived_at DESC,p.id DESC',args).fetchall()
    return [public(row) for row in rows]


def arrive(path,secret,user,data):
    require_role(user,'receptionist')
    resident_id=positive_id(data.get('resident_id'),'Resident')
    sender=text(data.get('sender'),'Sender',80)
    description=text(data.get('description'),'Description',220)
    code=f'{secrets.randbelow(1000000):06d}'
    with transaction(path) as db:
        owner=db.execute("SELECT id FROM users WHERE id=? AND role='resident'",(resident_id,)).fetchone()
        if not owner:raise Problem('Choose a valid resident.')
        at=now()
        cursor=db.execute('INSERT INTO parcels(resident_id,sender,description,arrived_at,code_expires_at,code_hash) VALUES(?,?,?,?,?,?)',
                          (resident_id,sender,description,at,at+86400,code_digest(secret,code)))
        event(db,cursor.lastrowid,user['id'],'arrival','Parcel recorded; pickup code issued.')
        row=db.execute('SELECT * FROM parcels WHERE id=?',(cursor.lastrowid,)).fetchone()
        return {'parcel':public(row),'pickup_code':code}


def reissue(path,secret,user,parcel_id,data):
    require_role(user,'receptionist')
    code=f'{secrets.randbelow(1000000):06d}'
    with transaction(path) as db:
        row=visible(db,user,parcel_id)
        require_revision(row,data.get('revision'))
        if row['status']!='waiting':raise Problem('Collected parcels cannot receive a new code.',409)
        db.execute('UPDATE parcels SET code_hash=?,code_expires_at=?,failed_attempts=0,revision=revision+1 WHERE id=?',
                   (code_digest(secret,code),now()+86400,parcel_id))
        event(db,parcel_id,user['id'],'code_reissued','Pickup code replaced; valid for 24 hours.')
        updated=db.execute('SELECT * FROM parcels WHERE id=?',(parcel_id,)).fetchone()
        return {'parcel':public(updated),'pickup_code':code}


def handover(path,secret,user,parcel_id,data):
    code=data.get('pickup_code')
    if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):raise Problem('Pickup code must contain six digits.')
    retry_key=text(data.get('retry_key'),'Retry key',90,8)
    if not re.fullmatch(r'[A-Za-z0-9_-]+',retry_key):raise Problem('Retry key contains invalid characters.')
    fingerprint=code_digest(secret,f'{parcel_id}:{code}')
    denied=None
    result=None
    with transaction(path) as db:
        row=visible(db,user,parcel_id)
        existing=db.execute('SELECT * FROM handovers WHERE retry_key=?',(retry_key,)).fetchone()
        if existing:
            if existing['parcel_id']!=parcel_id or existing['actor_id']!=user['id'] or not hmac.compare_digest(existing['fingerprint'],fingerprint):
                raise Problem('Retry key was already used for another handover.',409)
            return {'parcel':public(row),'replayed':True}
        require_revision(row,data.get('revision'))
        if row['status']!='waiting':raise Problem('Parcel has already been collected.',409)
        if row['failed_attempts']>=5:raise Problem('Pickup code is locked. Ask reception to reissue it.',423)
        if row['code_expires_at']<=now():raise Problem('Pickup code expired. Ask reception to reissue it.',409)
        if not hmac.compare_digest(row['code_hash'],code_digest(secret,code)):
            # Persist failed attempts without recording the submitted code in logs.
            db.execute('UPDATE parcels SET failed_attempts=failed_attempts+1 WHERE id=?',(parcel_id,))
            denied=Problem('Pickup code is incorrect.',400)
        else:
            at=now()
            db.execute("UPDATE parcels SET status='collected',collected_at=?,collected_by=?,revision=revision+1 WHERE id=?",(at,user['id'],parcel_id))
            db.execute('INSERT INTO handovers(parcel_id,actor_id,retry_key,fingerprint,completed_at) VALUES(?,?,?,?,?)',
                       (parcel_id,user['id'],retry_key,fingerprint,at))
            event(db,parcel_id,user['id'],'collected','Parcel handed over using a verified single-use code.',at)
            result={'parcel':public(db.execute('SELECT * FROM parcels WHERE id=?',(parcel_id,)).fetchone()),'replayed':False}
    if denied:raise denied
    return result
