"""Best Service CRM: servidor local, Python 3.10+, biblioteca estándar."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit, parse_qs
import argparse, getpass, hashlib, hmac, json, mimetypes, os, re, secrets, sqlite3, time

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('CRM_DB', ROOT / 'data' / 'crm.sqlite3'))
SETUP_TOKEN = ''

def local_today():
    return datetime.now(timezone(timedelta(hours=-5))).date()
SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','comercial','ventas','recepcion')),active INTEGER NOT NULL DEFAULT 1,invited INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),csrf TEXT NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,phone TEXT NOT NULL,email TEXT NOT NULL DEFAULT '',ruc TEXT NOT NULL DEFAULT '',contact TEXT NOT NULL DEFAULT '',owner TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL REFERENCES customers(id),date TEXT NOT NULL,valid_until TEXT NOT NULL,site TEXT NOT NULL,status TEXT NOT NULL,items TEXT NOT NULL,total INTEGER NOT NULL,terms TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL REFERENCES customers(id),quote_id INTEGER UNIQUE REFERENCES quotes(id),date TEXT NOT NULL,site TEXT NOT NULL,items TEXT NOT NULL,total INTEGER NOT NULL,cost INTEGER NOT NULL,reference TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,sale_id INTEGER NOT NULL REFERENCES sales(id),date TEXT NOT NULL,amount INTEGER NOT NULL,method TEXT NOT NULL,reference TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY,date TEXT NOT NULL,site TEXT NOT NULL,category TEXT NOT NULL,description TEXT NOT NULL,amount INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS appointments(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL REFERENCES customers(id),service TEXT NOT NULL,date TEXT NOT NULL,time TEXT NOT NULL,site TEXT NOT NULL,mode TEXT NOT NULL,address TEXT NOT NULL DEFAULT '',owner TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL REFERENCES customers(id),action TEXT NOT NULL,date TEXT NOT NULL,owner TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,user_id INTEGER REFERENCES users(id),action TEXT NOT NULL,entity TEXT NOT NULL,entity_id INTEGER);
CREATE TABLE IF NOT EXISTS activation_tokens(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,expires INTEGER NOT NULL);
'''

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c=sqlite3.connect(DB, timeout=15); c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); return c

def init():
    with connect() as c:
        c.executescript(SCHEMA)
        schema=c.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
        if 'recepcion' not in schema:
            # Version 1 -> 2: keep user IDs and all referencing rows.
            backup=DB.with_name(DB.stem+'-antes-v2.sqlite3')
            if not backup.exists():
                with sqlite3.connect(backup) as b:c.backup(b)
            c.execute('PRAGMA foreign_keys=OFF')
            c.execute('BEGIN IMMEDIATE')
            try:
                c.execute("CREATE TABLE users_v2(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','comercial','ventas','recepcion')),active INTEGER NOT NULL DEFAULT 1,invited INTEGER NOT NULL DEFAULT 0)")
                c.execute("INSERT INTO users_v2(id,name,email,password,role) SELECT id,name,email,password,CASE WHEN role='comercial' THEN 'ventas' ELSE role END FROM users")
                c.execute('DROP TABLE users')
                c.execute('ALTER TABLE users_v2 RENAME TO users')
                if c.execute('PRAGMA foreign_key_check').fetchall(): raise RuntimeError('Error en la migración')
                c.commit()
            except Exception:
                c.rollback();raise
            finally:c.execute('PRAGMA foreign_keys=ON')
        if 'detail' not in [r[1] for r in c.execute('PRAGMA table_info(audit)')]:
            c.execute("ALTER TABLE audit ADD COLUMN detail TEXT NOT NULL DEFAULT ''")

def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()

def verify(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)

def text_value(d,k,required=True,maximum=500):
    v=d.get(k,'')
    if not isinstance(v,str): raise ValueError('Campo inválido: '+k)
    v=v.strip()
    if (required and not v) or len(v)>maximum: raise ValueError('Revisa el campo '+k)
    return v

def choice(d,k,values):
    v=text_value(d,k)
    if v not in values: raise ValueError('Opción inválida: '+k)
    return v

def day(d,k='date'):
    v=text_value(d,k)
    try: date.fromisoformat(v)
    except ValueError: raise ValueError('Fecha inválida: '+k)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v): raise ValueError('Fecha inválida')
    return v

def money(v):
    try:
        n=Decimal(str(v))
        if not n.is_finite() or n<0 or n>Decimal('100000000'): raise ValueError('Importe fuera de rango')
        return int((n*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation,TypeError): raise ValueError('Importe inválido')

def items(d):
    rows=d.get('items')
    if not isinstance(rows,list) or not 1<=len(rows)<=100: raise ValueError('Agrega al menos un servicio')
    out=[]
    for x in rows:
        if not isinstance(x,dict): raise ValueError('Detalle inválido')
        desc=text_value(x,'description',maximum=200)
        q=x.get('quantity',0)
        if isinstance(q,bool) or not str(q).isdigit() or not 1<=int(q)<=100000: raise ValueError('Cantidad inválida')
        p=money(x.get('price')); cost=money(x.get('cost',0))
        out.append(dict(description=desc,quantity=int(q),price=p,cost=cost))
    total=sum(x['quantity']*x['price'] for x in out)
    if total<=0 or total>10000000000: raise ValueError('Total inválido')
    return json.dumps(out,ensure_ascii=False),total,sum(x['quantity']*x['cost'] for x in out)

def existing(c,table,ident):
    if not isinstance(ident,int) or isinstance(ident,bool): raise ValueError('Selecciona un registro válido')
    r=c.execute('SELECT * FROM '+table+' WHERE id=?',(ident,)).fetchone()
    if not r: raise ValueError('Registro no encontrado')
    return r

def customer(c,d):
    existing(c,'customers',d.get('customer_id')); return d['customer_id']

def insert(c,table,d):
    return c.execute('INSERT INTO '+table+'('+','.join(d)+') VALUES('+','.join('?' for _ in d)+')',list(d.values())).lastrowid

def audit(c,u,action,entity,ident,detail=''):
    insert(c,'audit',dict(at=datetime.now(timezone.utc).isoformat(timespec='seconds'),user_id=u,action=action,entity=entity,entity_id=ident,detail=detail))

def valid_password(d):
    pw=text_value(d,'password',maximum=200)
    if len(pw)<12: raise ValueError('La contraseña debe tener al menos 12 caracteres')
    if pw!=d.get('confirm_password'): raise ValueError('Las contraseñas no coinciden')
    return pw

def valid_email(d):
    email=text_value(d,'email').lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise ValueError('Correo inválido')
    return email

def issue_invite(c,ident):
    token=secrets.token_urlsafe(32)
    c.execute('DELETE FROM activation_tokens WHERE user_id=?',(ident,))
    c.execute('DELETE FROM sessions WHERE user_id=?',(ident,))
    c.execute('UPDATE users SET invited=1 WHERE id=?',(ident,))
    insert(c,'activation_tokens',dict(token=hashlib.sha256(token.encode()).hexdigest(),user_id=ident,expires=int(time.time())+86400))
    return token

def last_admin_guard(c,target,new_role,new_active):
    if target['role']=='admin' and target['active'] and not target['invited'] and (new_role!='admin' or not new_active):
        n=c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND invited=0").fetchone()[0]
        if n<=1:raise ValueError('Debe quedar al menos un administrador activo con acceso')

def public_account_action(c,entity,d):
    if entity=='setup':
        if c.execute('SELECT 1 FROM users LIMIT 1').fetchone():raise PermissionError('El administrador inicial ya fue creado')
        token=text_value(d,'token')
        if not SETUP_TOKEN or not hmac.compare_digest(token,SETUP_TOKEN):raise PermissionError('Usa el enlace de configuración mostrado en la terminal del servidor')
        name=text_value(d,'name');email=valid_email(d);pw=valid_password(d)
        uid=insert(c,'users',dict(name=name,email=email,password=password_hash(pw),role='admin'))
        audit(c,uid,'configurar','users',uid)
        return dict(ok=True)
    token=text_value(d,'token');hashed=hashlib.sha256(token.encode()).hexdigest()
    row=c.execute('SELECT t.*,u.active,u.invited FROM activation_tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(hashed,)).fetchone()
    if not row or row['expires']<int(time.time()) or not row['active'] or not row['invited']:raise ValueError('El enlace venció, fue utilizado o la cuenta está desactivada. Solicita uno nuevo al administrador.')
    pw=valid_password(d)
    c.execute('UPDATE users SET password=?,invited=0 WHERE id=?',(password_hash(pw),row['user_id']))
    c.execute('DELETE FROM activation_tokens WHERE user_id=?',(row['user_id'],))
    c.execute('DELETE FROM sessions WHERE user_id=?',(row['user_id'],))
    audit(c,row['user_id'],'activar contraseña','users',row['user_id'])
    return dict(ok=True)

def account_action(c,entity,d,u):
    if entity=='password':
        pw=valid_password(d)
        if not verify(text_value(d,'current_password'),u['password']):raise ValueError('La contraseña actual no es correcta')
        c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(pw),u['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
        c.execute('DELETE FROM activation_tokens WHERE user_id=?',(u['id'],))
        audit(c,u['id'],'cambiar contraseña','users',u['id']);return dict(ok=True,reauth=True)
    if u['role']!='admin':raise PermissionError('Solo el administrador gestiona los trabajadores')
    if entity=='users':
        ident=d.get('id');name=text_value(d,'name');email=valid_email(d)
        role=choice(d,'role',['admin','ventas','recepcion'])
        if ident:
            target=existing(c,'users',ident)
            if ident==u['id'] and role!=target['role']:raise ValueError('No puedes cambiar tu propio rol')
            last_admin_guard(c,target,role,target['active'])
            c.execute('UPDATE users SET name=?,email=?,role=? WHERE id=?',(name,email,role,ident))
            # Permissions and identity changes invalidate earlier sessions and invitation links.
            if role!=target['role'] or email!=target['email']:
                c.execute('DELETE FROM sessions WHERE user_id=?',(ident,))
                c.execute('DELETE FROM activation_tokens WHERE user_id=?',(ident,))
            audit(c,u['id'],'editar trabajador','users',ident,json.dumps(dict(before={k:target[k] for k in ('name','email','role')},after=dict(name=name,email=email,role=role)),ensure_ascii=False))
            return dict(ok=True,id=ident,reauth=ident==u['id'] and email!=target['email'])
        ident=insert(c,'users',dict(name=name,email=email,role=role,password=password_hash(secrets.token_urlsafe(40)),invited=1))
        token=issue_invite(c,ident);audit(c,u['id'],'crear trabajador','users',ident)
        return dict(ok=True,id=ident,activation_token=token)
    target=existing(c,'users',d.get('id'))
    if target['id']==u['id']:raise ValueError('Usa Mi cuenta para cambiar tu contraseña; no puedes desactivar o reinvitar tu propia cuenta')
    if entity=='user-access':
        active=d.get('active')
        if not isinstance(active,bool):raise ValueError('Estado de acceso inválido')
        last_admin_guard(c,target,target['role'],active)
        c.execute('UPDATE users SET active=? WHERE id=?',(int(active),target['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],))
        c.execute('DELETE FROM activation_tokens WHERE user_id=?',(target['id'],))
        audit(c,u['id'],'activar acceso' if active else 'desactivar acceso','users',target['id'])
        return dict(ok=True)
    if not target['active']:raise ValueError('Primero activa la cuenta')
    # Reinviting a working admin must not leave the installation without an administrator.
    last_admin_guard(c,target,target['role'],False)
    token=issue_invite(c,target['id']);audit(c,u['id'],'generar enlace','users',target['id'])
    return dict(ok=True,id=target['id'],activation_token=token)


def mutate(c,entity,d,user):
    role=user['role']; ident=d.get('id'); result_id=None
    if role=='recepcion' and entity not in ('customers','appointments','tasks'): raise PermissionError('Tu rol no permite esta operación')
    before=None
    if ident and entity in ('customers','quotes','expenses','appointments','tasks'):
        before=dict(existing(c,entity,ident))
    if entity=='customers':
        data={k:text_value(d,k,k in ('name','phone')) for k in ('name','phone','email','ruc','contact','owner','notes')}
        data['kind']=choice(d,'kind',['Persona','Empresa'])
        if data['ruc'] and not re.fullmatch(r'\d{11}',data['ruc']): raise ValueError('El RUC debe tener 11 dígitos')
        if data['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',data['email']): raise ValueError('Correo inválido')
        if ident:
            existing(c,entity,ident)
            c.execute('UPDATE customers SET '+','.join(k+'=?' for k in data)+' WHERE id=?',list(data.values())+[ident]);result_id=ident
        else:
            data['created']=local_today().isoformat(); result_id=insert(c,entity,data)
    elif entity in ('quotes','sales'):
        if ident and entity=='sales': raise ValueError('Las ventas registradas no se sobrescriben')
        if ident and (before['status']!='Borrador' or c.execute('SELECT 1 FROM sales WHERE quote_id=?',(ident,)).fetchone()): raise ValueError('Solo se editan cotizaciones en borrador sin venta asociada')
        its,total,cost=items(d)
        data=dict(customer_id=customer(c,d),date=day(d),site=text_value(d,'site'),items=its,total=total)
        if entity=='quotes':
            data.update(valid_until=day(d,'valid_until'),status='Borrador',terms=text_value(d,'terms',False,1500))
            if data['valid_until']<data['date']: raise ValueError('La vigencia debe ser posterior a la fecha')
        else:
            if data['date']>local_today().isoformat(): raise ValueError('Una venta no puede tener fecha futura')
            data.update(cost=cost,reference=text_value(d,'reference',False))
        if ident:
            if role!='admin':
                raise PermissionError('Solo administración edita cotizaciones; los costos internos están protegidos')
            c.execute('UPDATE quotes SET '+','.join(k+'=?' for k in data)+' WHERE id=?',list(data.values())+[ident]);result_id=ident
        else: result_id=insert(c,entity,data)
    elif entity=='quote-status':
        q=existing(c,'quotes',d.get('id'))
        if c.execute('SELECT id FROM sales WHERE quote_id=?',(q['id'],)).fetchone(): raise ValueError('Esta cotización ya tiene una venta')
        status=choice(d,'status',['Borrador','Enviada','Aceptada','Rechazada'])
        if status=='Aceptada' and q['valid_until']<local_today().isoformat(): raise ValueError('La cotización venció: prepara una nueva propuesta')
        c.execute('UPDATE quotes SET status=? WHERE id=?',(status,q['id']));result_id=q['id']
    elif entity=='convert-quote':
        q=existing(c,'quotes',d.get('id'))
        found=c.execute('SELECT id FROM sales WHERE quote_id=?',(q['id'],)).fetchone()
        if found: return found['id']
        if q['status']!='Aceptada': raise ValueError('Primero marca la cotización como aceptada')
        cost=sum(x['quantity']*x['cost'] for x in json.loads(q['items']))
        conversion_date=day(d)
        if conversion_date<q['date'] or conversion_date>local_today().isoformat(): raise ValueError('Fecha de conversión inválida')
        result_id=insert(c,'sales',dict(customer_id=q['customer_id'],quote_id=q['id'],date=day(d),site=q['site'],items=q['items'],total=q['total'],cost=cost,reference='Cotización '+str(q['id'])))
    elif entity=='payments':
        sale=existing(c,'sales',d.get('sale_id'));amount=money(d.get('amount'))
        paid=c.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE sale_id=?',(sale['id'],)).fetchone()[0]
        if amount<=0 or amount>sale['total']-paid: raise ValueError('El pago debe ser positivo y no superar el saldo')
        dt=day(d)
        if dt<sale['date'] or dt>local_today().isoformat(): raise ValueError('La fecha de cobro debe estar entre la venta y hoy')
        result_id=insert(c,entity,dict(sale_id=sale['id'],amount=amount,date=dt,method=choice(d,'method',['Transferencia','Efectivo','Tarjeta','Yape / Plin']),reference=text_value(d,'reference',False)))
    elif entity=='expenses':
        if role!='admin': raise PermissionError('Solo administración registra gastos')
        amount=money(d.get('amount'))
        if amount<=0: raise ValueError('El gasto debe ser mayor a cero')
        data=dict(date=day(d),site=text_value(d,'site'),category=text_value(d,'category'),description=text_value(d,'description'),amount=amount)
        if ident:
            c.execute('UPDATE expenses SET '+','.join(k+'=?' for k in data)+' WHERE id=?',list(data.values())+[ident]);result_id=ident
        else:result_id=insert(c,entity,data)
    elif entity in ('appointments','tasks'):
        data=dict(customer_id=customer(c,d),date=day(d),owner=text_value(d,'owner'))
        if entity=='appointments':
            data.update(service=text_value(d,'service'),time=text_value(d,'time'),site=text_value(d,'site'),mode=choice(d,'mode',['En sede','A domicilio']),address=text_value(d,'address',False),status=choice(d,'status',['Pendiente','Confirmada','Atendida','Cancelada','No asistió']))
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',data['time']): raise ValueError('Hora inválida')
            if data['mode']=='A domicilio' and not data['address']: raise ValueError('Falta la dirección')
            if data['mode']=='En sede':data['address']=''
            if data['status'] not in ('Cancelada','No asistió') and c.execute("SELECT id FROM appointments WHERE date=? AND time=? AND owner=? AND status NOT IN ('Cancelada','No asistió') AND id<>?",(data['date'],data['time'],data['owner'],ident or 0)).fetchone(): raise ValueError('El responsable ya tiene una cita a esa hora')
        else:data.update(action=text_value(d,'action'),status=choice(d,'status',['Pendiente','En curso','Completado']),result=text_value(d,'result',False))
        if ident:
            existing(c,entity,ident);c.execute('UPDATE '+entity+' SET '+','.join(k+'=?' for k in data)+' WHERE id=?',list(data.values())+[ident]);result_id=ident
        else: result_id=insert(c,entity,data)
    else: raise ValueError('Operación desconocida')
    detail=''
    if before is not None:
        after=dict(existing(c,entity,result_id))
        detail=json.dumps(dict(before=before,after=after),ensure_ascii=False)
    audit(c,user['id'],'editar' if ident else 'crear',entity,result_id,detail);return result_id

def snapshot(c,user):
    role=user['role']
    allowed=('customers','appointments','tasks') if role=='recepcion' else ('customers','quotes','sales','payments','appointments','tasks')
    out={k:[dict(r) for r in c.execute('SELECT * FROM '+k+' ORDER BY id DESC')] if k in allowed else [] for k in ('customers','quotes','sales','payments','appointments','tasks')}
    for k in ('quotes','sales'):
        for r in out[k]:
            r['items']=json.loads(r['items'])
            if role!='admin':
                r.pop('cost',None)
                for i in r['items']:i.pop('cost',None)
    out['expenses']=[dict(r) for r in c.execute('SELECT * FROM expenses ORDER BY id DESC')] if role=='admin' else []
    out['users']=[dict(r) for r in c.execute('SELECT id,name,email,role,active,invited FROM users ORDER BY id')] if role=='admin' else []
    out['team']=[dict(r) for r in c.execute('SELECT id,name FROM users WHERE active=1 AND invited=0 ORDER BY name')]
    out['audit']=[dict(r) for r in c.execute('SELECT a.id,a.at,a.action,a.entity,a.entity_id,u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 100')] if role=='admin' else []
    out['user']={k:user[k] for k in ('id','name','role')}
    return out

class Handler(BaseHTTPRequestHandler):
    server_version='BestServiceCRM'
    def log_message(self,fmt,*args): pass
    def send(self,obj,status=200,cookie=None):
        b=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(b)
    def auth(self,c):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return None
        token=cookie['crm_session'].value if 'crm_session' in cookie else ''
        return c.execute('SELECT u.*,s.csrf,s.token FROM sessions s JOIN users u ON u.id=s.user_id WHERE token=? AND expires>? AND u.active=1 AND u.invited=0',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
    def do_GET(self):
        path=urlsplit(self.path).path
        if path.startswith('/api/'):
            with connect() as c:
                if path=='/api/status':return self.send(dict(setup_required=not bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())))
                u=self.auth(c)
                if not u:return self.send({'error':'Inicia sesión'},401)
                if path=='/api/data': return self.send(snapshot(c,u))
                if path=='/api/me':return self.send(dict(id=u['id'],name=u['name'],role=u['role'],csrf=u['csrf']))
                return self.send({'error':'Ruta no encontrada'},404)
        allowed={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/styles.css':'styles.css','/model.js':'model.js','/assets/logo.png':'assets/logo.png','/assets/manrope-400.ttf':'assets/manrope-400.ttf','/assets/manrope-500.ttf':'assets/manrope-500.ttf','/assets/manrope-600.ttf':'assets/manrope-600.ttf','/assets/manrope-700.ttf':'assets/manrope-700.ttf'}
        if path not in allowed:return self.send({'error':'No encontrado'},404)
        f=ROOT/'web'/allowed[path];b=f.read_bytes();self.send_response(200)
        self.send_header('Content-Type',mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(len(b)));self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers();self.wfile.write(b)
    def do_POST(self):
        global SETUP_TOKEN
        if self.headers.get('X-CRM-Request')!='1':return self.send({'error':'Solicitud no válida'},403)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=250000:raise ValueError('Solicitud demasiado grande o vacía')
            d=json.loads(self.rfile.read(size))
            if not isinstance(d,dict):raise ValueError('Solicitud inválida')
            entity=urlsplit(self.path).path.removeprefix('/api/')
            with connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if entity in ('setup','activate'):
                    result=public_account_action(c,entity,d)
                    c.commit()
                    if entity=='setup':SETUP_TOKEN=''
                    return self.send(result)
                if entity=='login':
                    key=self.client_address[0];now=int(time.time())
                    attempt=c.execute('SELECT * FROM login_attempts WHERE key=?',(key,)).fetchone()
                    if attempt and attempt['expires']>now and attempt['count']>=8:return self.send({'error':'Demasiados intentos. Espera 15 minutos.'},429)
                    email=text_value(d,'email').lower();pw=text_value(d,'password',maximum=200)
                    u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                    if not u or not u['active'] or u['invited'] or not verify(pw,u['password']):
                        count=attempt['count']+1 if attempt and attempt['expires']>now else 1
                        c.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)',(key,count,now+900));c.commit()
                        return self.send({'error':'Correo o contraseña incorrectos'},401)
                    c.execute('DELETE FROM login_attempts WHERE key=?',(key,));c.execute('DELETE FROM sessions WHERE expires<?',(now,))
                    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
                    insert(c,'sessions',dict(token=hashlib.sha256(token.encode()).hexdigest(),user_id=u['id'],csrf=csrf,expires=now+28800));c.commit()
                    secure='; Secure' if os.environ.get('CRM_SECURE_COOKIE')=='1' else ''
                    return self.send(dict(id=u['id'],csrf=csrf,name=u['name'],role=u['role']),cookie='crm_session='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'+secure)
                u=self.auth(c)
                if not u:return self.send({'error':'Inicia sesión'},401)
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),u['csrf']):raise PermissionError('Sesión inválida. Vuelve a iniciar sesión.')
                if entity=='logout':
                    c.execute('DELETE FROM sessions WHERE token=?',(u['token'],));c.commit()
                    return self.send({'ok':True},cookie='crm_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                if entity in ('users','user-access','user-invite','password'):
                    result=account_action(c,entity,d,u);c.commit();return self.send(result)
                ident=mutate(c,entity,d,u);c.commit();return self.send({'ok':True,'id':ident})
        except PermissionError as e:self.send({'error':str(e)},403)
        except sqlite3.IntegrityError:self.send({'error':'El registro ya existe o tiene datos relacionados. Revisa el correo y los campos.'},400)
        except (ValueError,TypeError,KeyError) as e:self.send({'error':str(e)},400)
        except Exception:self.send({'error':'Error interno. Revisa el servidor.'},500)

def seed():
    with connect() as c:
        if c.execute('SELECT COUNT(*) FROM customers').fetchone()[0]:raise ValueError('La base ya tiene clientes; no se cargó la demo')
        today=local_today(); month=today.strftime('%Y-%m');dt=month+'-01'
        for name,kind in [('Ana Torres','Persona'),('Empresa Los Olivos','Empresa'),('Luis Pérez','Persona'),('Grupo Horizonte','Empresa')]:
            insert(c,'customers',dict(name=name,kind=kind,phone='9XX XXX XXX',owner='Equipo comercial',created=dt))
        for cid,total,cost,site in [(1,150000,60000,'Principal'),(2,2400000,960000,'Principal'),(3,450000,180000,'Principal'),(4,2000000,800000,'Norte')]:
            sale_id=insert(c,'sales',dict(customer_id=cid,date=dt,site=site,items=json.dumps([dict(description='Servicio de ejemplo',quantity=1,price=total,cost=cost)]),total=total,cost=cost,reference='DEMO'))
            insert(c,'payments',dict(sale_id=sale_id,date=dt,amount=int(total*0.84),method='Transferencia',reference='DEMO'))
        insert(c,'expenses',dict(date=dt,site='Principal',category='Operación',description='Gastos operativos de ejemplo',amount=1200000))
        insert(c,'quotes',dict(customer_id=2,date=today.isoformat(),valid_until=(today+timedelta(days=15)).isoformat(),site='Principal',status='Enviada',items=json.dumps([dict(description='Servicio corporativo',quantity=20,price=6000,cost=2400)]),total=120000,terms='Propuesta de ejemplo. Precio final.'))
        for i,cid in enumerate([1,2,3]):
            insert(c,'appointments',dict(customer_id=cid,service='Vacunación' if i!=1 else 'Coordinación de campaña',date=today.isoformat(),time=f'{9+i*2:02}:00',site='Principal',mode='En sede',owner='Recepción',status='Confirmada'))
            insert(c,'tasks',dict(customer_id=cid,action=['Confirmar cita','Consultar aceptación de cotización','Responder consulta'][i],date=today.isoformat(),owner='Equipo comercial',status='Pendiente'))
    print('Datos ficticios cargados.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--init',action='store_true');p.add_argument('--user',action='store_true');p.add_argument('--demo',action='store_true');p.add_argument('--backup',action='store_true');p.add_argument('--open-browser',action='store_true');p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8000);a=p.parse_args();init()
    if a.backup:
        dest=ROOT/'backups'/('crm-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3');dest.parent.mkdir(exist_ok=True)
        with connect() as c,sqlite3.connect(dest) as b:c.backup(b)
        print('Copia creada:',dest)
    elif a.init or a.user:
        with connect() as c:
            if a.init and c.execute('SELECT 1 FROM users LIMIT 1').fetchone():raise SystemExit('Ya existe un usuario. Usa --user para agregar otro.')
            name=input('Nombre: ').strip();email=input('Correo: ').strip().lower();role='admin' if a.init else input('Rol (admin/ventas/recepcion): ').strip();pw=getpass.getpass('Contraseña (mínimo 12 caracteres): ')
            if len(pw)<12 or not name or '@' not in email or role not in ('admin','ventas','recepcion'):raise SystemExit('Datos inválidos')
            if pw!=getpass.getpass('Repite la contraseña: '):raise SystemExit('Las contraseñas no coinciden')
            insert(c,'users',dict(name=name,email=email,password=password_hash(pw),role=role));print('Usuario creado.')
    elif a.demo:seed()
    else:
        with connect() as c:
            if not c.execute('SELECT 1 FROM users').fetchone():
                SETUP_TOKEN=secrets.token_urlsafe(32)
                print(f'PRIMER INICIO: abre http://127.0.0.1:{a.port}/#setup={SETUP_TOKEN}',flush=True)
        url=f'http://127.0.0.1:{a.port}/'+('#setup='+SETUP_TOKEN if SETUP_TOKEN else '')
        httpd=ThreadingHTTPServer((a.host,a.port),Handler)
        print(f'Best Service CRM: http://{a.host}:{a.port}',flush=True)
        if a.open_browser:
            import threading, webbrowser
            threading.Timer(0.6,lambda:webbrowser.open(url)).start()
        httpd.serve_forever()
