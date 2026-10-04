"""Flask factory and source-independent command-line launcher."""
import argparse
import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path
from flask import Flask, jsonify, render_template, request, session, send_file
from core import Problem, authenticate, connect, now, secret_file, transaction, user_for
import domain

APP_NAME = 'Aangan — Parcel Handover'
DEFAULT_PORT = 8112


def create_app(config=None):
    app=Flask(__name__)
    app.config.update(DATA_DIR=str(Path(__file__).resolve().parent/'instance'), DEMO=True,
                      MAX_CONTENT_LENGTH=300*1024, SESSION_COOKIE_NAME='aangan_session',
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      PERMANENT_SESSION_LIFETIME=28800)
    if config:app.config.update(config)
    directory=Path(app.config['DATA_DIR']);directory.mkdir(parents=True,exist_ok=True)
    app.secret_key=secret_file(directory)
    db_path=directory/'app.sqlite3'
    app.config['DB_PATH']=str(db_path)
    domain.initialize(db_path,app.secret_key,app.config['DEMO'])
    with transaction(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS login_attempts(identity TEXT PRIMARY KEY,failures INTEGER NOT NULL,first_at INTEGER NOT NULL)')

    @app.errorhandler(Problem)
    def invalid(error):return jsonify(error=error.message),error.status

    @app.errorhandler(413)
    def large(_):return jsonify(error='Request too large. Attachments are limited to 256 KiB.'),413

    @app.errorhandler(404)
    def missing(_):return jsonify(error='Page or API route not found.'),404

    @app.errorhandler(sqlite3.OperationalError)
    def unavailable(_):return jsonify(error='Storage is temporarily unavailable. Retry shortly.'),503

    @app.errorhandler(sqlite3.IntegrityError)
    def constraint(_):return jsonify(error='The operation conflicts with the current record state.'),409

    @app.before_request
    def protect():
        if request.method in ('POST','PUT','PATCH','DELETE'):
            supplied=request.headers.get('X-CSRF-Token','')
            expected=session.get('csrf','')
            if not expected or not hmac.compare_digest(supplied,expected):
                raise Problem('Request token is missing or expired. Refresh and try again.',403)
            origin=request.headers.get('Origin')
            if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
                raise Problem('Cross-origin requests are not allowed.',403)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    def payload():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise Problem('Send a JSON object with application/json content type.')
        return data

    def identity():
        db=connect(db_path)
        try:return dict(user_for(db))
        finally:db.close()

    @app.get('/')
    def home():return render_template('index.html',demo=app.config['DEMO'])

    @app.get('/api/health')
    def health():
        db=connect(db_path)
        try:db.execute('SELECT 1').fetchone()
        finally:db.close()
        return jsonify(status='ok',app=APP_NAME,demo=app.config['DEMO'])

    @app.get('/api/session')
    def session_info():
        if not session.get('csrf'):session['csrf']=secrets.token_urlsafe(32)
        user=None
        if session.get('user_id'):
            try:user=identity()
            except Problem:session.pop('user_id',None)
        return jsonify(user=user,csrf_token=session['csrf'],demo=app.config['DEMO'])

    @app.post('/api/login')
    def login():
        data=payload()
        email=data.get('email');password=data.get('password')
        if not isinstance(email,str) or not isinstance(password,str):raise Problem('Email and password are required.')
        key=hashlib.sha256(email.strip().lower().encode()).hexdigest()
        denied=None;user=None
        with transaction(db_path) as db:
            db.execute('DELETE FROM login_attempts WHERE first_at<?',(now()-900,))
            prior=db.execute('SELECT * FROM login_attempts WHERE identity=?',(key,)).fetchone()
            if prior and prior['failures']>=8:raise Problem('Too many failed sign-ins. Try again in 15 minutes.',429)
            try:user=authenticate(db,email,password)
            except Problem as error:
                db.execute('INSERT INTO login_attempts(identity,failures,first_at) VALUES(?,1,?) ON CONFLICT(identity) DO UPDATE SET failures=failures+1',(key,now()))
                denied=error
            if user:db.execute('DELETE FROM login_attempts WHERE identity=?',(key,))
        if denied:raise denied
        session.clear();session['user_id']=user['id'];session['csrf']=secrets.token_urlsafe(32);session.permanent=True
        return jsonify(user=user,csrf_token=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear();session['csrf']=secrets.token_urlsafe(32)
        return jsonify(csrf_token=session['csrf'])

    @app.get('/api/residents')
    def residents():
        user=identity()
        if user['role']!='receptionist':raise Problem('Reception access required.',403)
        db=connect(db_path)
        try:rows=[dict(r) for r in db.execute("SELECT id,name,unit FROM users WHERE role='resident' ORDER BY unit")]
        finally:db.close()
        return jsonify(residents=rows)

    @app.get('/api/parcels')
    def parcels():
        user=identity();db=connect(db_path)
        try:rows=domain.list_parcels(db,user)
        finally:db.close()
        return jsonify(parcels=rows)

    @app.post('/api/parcels')
    def arrival():return jsonify(domain.arrive(db_path,app.secret_key,identity(),payload())),201

    @app.get('/api/parcels/<int:parcel_id>/events')
    def history(parcel_id):
        user=identity();db=connect(db_path)
        try:
            domain.visible(db,user,parcel_id)
            rows=[dict(r) for r in db.execute('SELECT e.id,e.kind,e.detail,e.created_at,u.name AS actor FROM events e JOIN users u ON u.id=e.actor_id WHERE parcel_id=? ORDER BY e.id',(parcel_id,))]
        finally:db.close()
        return jsonify(events=rows)

    @app.post('/api/parcels/<int:parcel_id>/reissue')
    def reissue(parcel_id):return jsonify(domain.reissue(db_path,app.secret_key,identity(),parcel_id,payload()))

    @app.post('/api/parcels/<int:parcel_id>/handover')
    def pickup(parcel_id):return jsonify(domain.handover(db_path,app.secret_key,identity(),parcel_id,payload()))

    return app


def main():
    parser=argparse.ArgumentParser(description=APP_NAME+' local demo')
    parser.add_argument('--port',type=int,default=DEFAULT_PORT)
    parser.add_argument('--data-dir',default=str(Path(__file__).resolve().parent/'instance'))
    parser.add_argument('--no-demo',action='store_true',help='Create an empty database; do not seed fictional accounts.')
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Port must be from 1 to 65535.')
    create_app({'DATA_DIR':args.data_dir,'DEMO':not args.no_demo}).run(host='127.0.0.1',port=args.port,debug=False)


if __name__=='__main__':main()
