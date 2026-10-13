import sys,tempfile,unittest,json,sqlite3,hashlib,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s

class AccessTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();s.DB=Path(self.tmp.name)/'crm.sqlite3';s.init();s.SETUP_TOKEN='test-setup-token'
  with s.connect() as c:
   s.public_account_action(c,'setup',dict(token=s.SETUP_TOKEN,name='Admin',email='admin@example.test',password='Admin-password-123',confirm_password='Admin-password-123'))
   self.admin=dict(c.execute('SELECT * FROM users').fetchone())
 def tearDown(self):self.tmp.cleanup()
 def test_invite_single_use_and_disable(self):
  with s.connect() as c:
   r=s.account_action(c,'users',dict(name='Recepción',email='reception@example.test',role='recepcion'),self.admin)
   token=r['activation_token'];uid=r['id']
   self.assertNotEqual(c.execute('SELECT token FROM activation_tokens').fetchone()[0],token)
   s.public_account_action(c,'activate',dict(token=token,password='Worker-password-123',confirm_password='Worker-password-123'))
   with self.assertRaises(ValueError):s.public_account_action(c,'activate',dict(token=token,password='Worker-password-123',confirm_password='Worker-password-123'))
   u=dict(s.existing(c,'users',uid));self.assertEqual(u['invited'],0)
   cid=s.mutate(c,'customers',dict(name='Cliente',kind='Persona',phone='999'),self.admin)
   with self.assertRaises(PermissionError):s.mutate(c,'sales',{},u)
   with self.assertRaises(PermissionError):s.account_action(c,'users',dict(name='Hack',email='hack@example.test',role='admin'),u)
   snap=s.snapshot(c,u);self.assertEqual(snap['users'],[]);self.assertEqual(snap['sales'],[]);self.assertEqual(snap['audit'],[])
   s.insert(c,'sessions',dict(token='test-session',csrf='test-csrf',user_id=uid,expires=int(time.time())+1000))
   s.account_action(c,'user-access',dict(id=uid,active=False),self.admin)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(uid,)).fetchone()[0],0)
   with self.assertRaises(ValueError):s.account_action(c,'user-invite',dict(id=uid),self.admin)
   s.account_action(c,'user-access',dict(id=uid,active=True),self.admin)
   r=s.account_action(c,'user-invite',dict(id=uid),self.admin)
   old=r['activation_token'];new=s.account_action(c,'user-invite',dict(id=uid),self.admin)['activation_token']
   with self.assertRaises(ValueError):s.public_account_action(c,'activate',dict(token=old,password='Worker-password-123',confirm_password='Worker-password-123'))
   c.execute('UPDATE activation_tokens SET expires=0')
   with self.assertRaises(ValueError):s.public_account_action(c,'activate',dict(token=new,password='Worker-password-123',confirm_password='Worker-password-123'))
 def test_admin_guard_setup_and_password(self):
  with s.connect() as c:
   with self.assertRaises(PermissionError):s.public_account_action(c,'setup',{})
   with self.assertRaises(ValueError):s.account_action(c,'user-access',dict(id=self.admin['id'],active=False),self.admin)
   with self.assertRaises(ValueError):s.account_action(c,'users',dict(id=self.admin['id'],name='Admin',email='admin@example.test',role='ventas'),self.admin)
   with self.assertRaises(ValueError):s.last_admin_guard(c,self.admin,'ventas',True)
   with self.assertRaises(ValueError):s.account_action(c,'password',dict(current_password='wrong',password='New-password-123',confirm_password='New-password-123'),self.admin)
   s.account_action(c,'password',dict(current_password='Admin-password-123',password='New-password-123',confirm_password='New-password-123'),self.admin)
   self.assertTrue(s.verify('New-password-123',s.existing(c,'users',self.admin['id'])['password']))
 def test_edits_and_audit(self):
  with s.connect() as c:
   e=dict(date=s.local_today().isoformat(),site='Principal',category='Otros',description='Gasto',amount='10')
   eid=s.mutate(c,'expenses',e,self.admin);s.mutate(c,'expenses',{**e,'id':eid,'amount':'15'},self.admin)
   self.assertEqual(s.existing(c,'expenses',eid)['amount'],1500)
   trail=json.loads(c.execute("SELECT detail FROM audit WHERE action='editar' AND entity='expenses'").fetchone()[0]);self.assertEqual(trail['before']['amount'],1000);self.assertEqual(trail['after']['amount'],1500)
   cid=s.mutate(c,'customers',dict(name='Cliente',kind='Persona',phone='999'),self.admin)
   q=dict(customer_id=cid,date=s.local_today().isoformat(),valid_until=s.local_today().isoformat(),site='Principal',items=[dict(description='Prueba',quantity=1,price='10',cost='4')])
   qid=s.mutate(c,'quotes',q,self.admin)
   s.mutate(c,'quotes',{**q,'id':qid,'items':[dict(description='Nuevo',quantity=2,price='15',cost='4')]},self.admin)
   self.assertEqual(s.existing(c,'quotes',qid)['total'],3000)
   s.mutate(c,'quote-status',dict(id=qid,status='Enviada'),self.admin)
   with self.assertRaises(ValueError):s.mutate(c,'quotes',{**q,'id':qid},self.admin)

class MigrationTests(unittest.TestCase):
 def test_v1_preserves_users_and_references(self):
  with tempfile.TemporaryDirectory() as t:
   s.DB=Path(t)/'old.sqlite3'
   with sqlite3.connect(s.DB) as c:
    c.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','comercial'))); INSERT INTO users VALUES(1,'Admin','a@test.test','hash','admin'); INSERT INTO users VALUES(2,'Venta','v@test.test','hash','comercial'); CREATE TABLE sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),csrf TEXT NOT NULL,expires INTEGER NOT NULL); INSERT INTO sessions VALUES('token',2,'csrf',9999999999);")
   s.init();s.init()
   with s.connect() as c:
    self.assertEqual(s.existing(c,'users',2)['role'],'ventas')
    self.assertEqual(c.execute('SELECT user_id FROM sessions').fetchone()[0],2)
    self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
   self.assertTrue(s.DB.with_name('old-antes-v2.sqlite3').exists())

if __name__=='__main__':unittest.main()
