import sys, tempfile, unittest, sqlite3, threading, json, urllib.request, urllib.error, http.cookiejar
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s

class CRMTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();s.DB=Path(self.tmp.name)/'db.sqlite3';s.init()
  with s.connect() as c:
   self.uid=s.insert(c,'users',dict(name='Admin',email='test@example.test',password=s.password_hash('long-test-password'),role='admin'))
   self.u=dict(id=self.uid,role='admin');self.cid=s.mutate(c,'customers',dict(name='Empresa Demo',kind='Empresa',phone='999',ruc=''),self.u)
  self.sale=dict(customer_id=self.cid,date=date.today().isoformat(),site='Principal',items=[dict(description='Servicio',quantity=2,price='60.25',cost='20.10')])
 def tearDown(self):self.tmp.cleanup()
 def test_quote_conversion_and_payments(self):
  with s.connect() as c:
   q=s.mutate(c,'quotes',{**self.sale,'valid_until':date.today().isoformat()},self.u)
   with self.assertRaises(ValueError):s.mutate(c,'convert-quote',dict(id=q,date=date.today().isoformat()),self.u)
   s.mutate(c,'quote-status',dict(id=q,status='Aceptada'),self.u)
   sale=s.mutate(c,'convert-quote',dict(id=q,date=date.today().isoformat()),self.u)
   self.assertEqual(sale,s.mutate(c,'convert-quote',dict(id=q,date=date.today().isoformat()),self.u))
   self.assertEqual(c.execute('SELECT COUNT(*) FROM sales').fetchone()[0],1)
   row=s.existing(c,'sales',sale);self.assertEqual(row['total'],12050);self.assertEqual(row['cost'],4020)
   payment=dict(sale_id=sale,date=date.today().isoformat(),amount='50',method='Efectivo')
   s.mutate(c,'payments',payment,self.u)
   with self.assertRaises(ValueError):s.mutate(c,'payments',{**payment,'amount':'70.51'},self.u)
   s.mutate(c,'payments',{**payment,'amount':'70.50'},self.u)
   with self.assertRaises(ValueError):s.mutate(c,'payments',payment,self.u)
 def test_validation_permissions_and_conflicts(self):
  with s.connect() as c:
   for v in ['NaN','Infinity','-1']:
    with self.assertRaises(ValueError):s.money(v)
   with self.assertRaises(ValueError):s.mutate(c,'sales',{**self.sale,'customer_id':999},self.u)
   with self.assertRaises(PermissionError):s.mutate(c,'expenses',{},dict(id=self.uid,role='comercial'))
   sale=s.mutate(c,'sales',self.sale,self.u)
   snap=s.snapshot(c,dict(id=self.uid,name='Agent',role='comercial'))
   self.assertNotIn('cost',snap['sales'][0]);self.assertNotIn('cost',snap['sales'][0]['items'][0])
   a=dict(customer_id=self.cid,service='Atención',date=date.today().isoformat(),time='09:00',site='Principal',mode='En sede',owner='Ana',status='Confirmada')
   aid=s.mutate(c,'appointments',a,self.u)
   with self.assertRaises(ValueError):s.mutate(c,'appointments',a,self.u)
   s.mutate(c,'appointments',{**a,'id':aid,'status':'Cancelada'},self.u)
   s.mutate(c,'appointments',a,self.u)
 def test_http_auth_csrf_persistence(self):
  srv=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
  base='http://127.0.0.1:'+str(srv.server_port);jar=http.cookiejar.CookieJar();op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
  def call(path,body=None,csrf=None):
   headers={'X-CRM-Request':'1','Content-Type':'application/json'}
   if csrf:headers['X-CSRF-Token']=csrf
   req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
   with op.open(req) as r:return json.load(r)
  try:
   with self.assertRaises(urllib.error.HTTPError) as err:call('/api/data')
   self.assertEqual(err.exception.code,401)
   login=call('/api/login',dict(email='test@example.test',password='long-test-password'))
   self.assertIn('csrf',login)
   with self.assertRaises(urllib.error.HTTPError) as err:call('/api/sales',self.sale)
   self.assertEqual(err.exception.code,403)
   sale=call('/api/sales',self.sale,login['csrf'])
   self.assertEqual(call('/api/data')['sales'][0]['id'],sale['id'])
   call('/api/logout',{},login['csrf'])
   with self.assertRaises(urllib.error.HTTPError):call('/api/data')
   with s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM sales').fetchone()[0],1)
  finally:srv.shutdown();srv.server_close();thread.join()

if __name__=='__main__':unittest.main()
