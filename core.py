"""Shared infrastructure copied into each independent project; no cross-repo imports."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from flask import g, session
from werkzeug.security import generate_password_hash, check_password_hash

class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def connect(path):
    db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=10000')
    return db


@contextmanager
def transaction(path):
    db = connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def secret_file(directory):
    path = Path(directory) / '.session-secret'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    value = path.read_text().strip()
    if len(value) != 64:
        raise RuntimeError('Invalid persistent secret; restore the existing secret before starting.')
    return value


def now():
    return int(time.time())


def text(value, label, limit, minimum=1):
    if not isinstance(value, str):
        raise Problem(f'{label} must be text.')
    value = value.strip()
    if not minimum <= len(value) <= limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise Problem(f'{label} must contain {minimum}–{limit} printable characters.')
    return value


def positive_id(value, label='ID'):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Problem(f'{label} must be a positive integer.')
    return value


def revision(value):
    return positive_id(value, 'Revision')


def require_revision(row, value):
    if row['revision'] != revision(value):
        raise Problem('This record changed. Refresh before trying again.', 409)


def require_role(user, *roles):
    if user['role'] not in roles:
        raise Problem('This action is not permitted for your account.', 403)


def user_for(db):
    identity = session.get('user_id')
    if not isinstance(identity, int):
        raise Problem('Please sign in first.', 401)
    user = db.execute('SELECT id,name,email,role,unit FROM users WHERE id=?', (identity,)).fetchone()
    if user is None:
        raise Problem('Please sign in again.', 401)
    return user


def add_users(db, rows):
    for name, email, role, unit, password in rows:
        db.execute('INSERT INTO users(name,email,role,unit,password_hash) VALUES(?,?,?,?,?)',
                   (name, email, role, unit, generate_password_hash(password)))


def authenticate(db, email, password):
    email = text(email, 'Email', 120).lower()
    if not isinstance(password, str) or not 1 <= len(password) <= 150:
        raise Problem('Password must contain 1–150 characters.')
    row = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    # Unknown identities still perform one password verification.
    dummy = generate_password_hash('invalid-login') if row is None else row['password_hash']
    valid = check_password_hash(dummy, password)
    if row is None or not valid:
        raise Problem('Email or password is incorrect.', 401)
    return {key: row[key] for key in ('id','name','email','role','unit')}


def code_digest(secret, code):
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 role TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '',
 password_hash TEXT NOT NULL
);
"""
