import sys,tempfile,unittest,threading,json,http.cookiejar,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s

class HTTPAccessTests(unittest.TestCase):
 def test_setup_invite_role_edit_and_revocation(self):
  with tempfile.TemporaryDirectory() as t:
   s.DB=Path(t)/'crm.sqlite3';s.init();s.SETUP_TOKEN='setup-test-only'
   srv=s.ThreadingHTTPServer(('127.0.0.1',0),s.Handler);th=threading.Thread(target=srv.serve_forever,daemon=True);th.start();base='http://127.0.0.1:'+str(srv.server_port)
   admin=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));worker=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
   def req(client,path,body=None,csrf=''):
    headers={'Content-Type':'application/json','X-CRM-Request':'1','X-CSRF-Token':csrf}
    r=urllib.request.Request(base+'/api/'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    with client.open(r) as response:return json.load(response)
   try:
    self.assertTrue(req(admin,'status')['setup_required'])
    req(admin,'setup',dict(token=s.SETUP_TOKEN,name='Administrador',email='admin@example.test',password='admin-password-123',confirm_password='admin-password-123'))
    self.assertFalse(req(admin,'status')['setup_required'])
    a=req(admin,'login',dict(email='admin@example.test',password='admin-password-123'))
    invite=req(admin,'users',dict(name='Recepción',email='worker@example.test',role='recepcion'),a['csrf'])
    req(worker,'activate',dict(token=invite['activation_token'],password='worker-password-123',confirm_password='worker-password-123'))
    w=req(worker,'login',dict(email='worker@example.test',password='worker-password-123'))
    with self.assertRaises(urllib.error.HTTPError) as e:req(worker,'expenses',dict(amount='10'),w['csrf'])
    self.assertEqual(e.exception.code,403)
    self.assertEqual(req(worker,'data')['users'],[])
    req(admin,'users',dict(id=invite['id'],name='Ventas',email='worker@example.test',role='ventas'),a['csrf'])
    with self.assertRaises(urllib.error.HTTPError) as e:req(worker,'data')
    self.assertEqual(e.exception.code,401)
    w=req(worker,'login',dict(email='worker@example.test',password='worker-password-123'));self.assertEqual(w['role'],'ventas')
    req(admin,'user-access',dict(id=invite['id'],active=False),a['csrf'])
    with self.assertRaises(urllib.error.HTTPError):req(worker,'data')
    with self.assertRaises(urllib.error.HTTPError):req(worker,'login',dict(email='worker@example.test',password='worker-password-123'))
    with urllib.request.urlopen(base+'/assets/manrope-400.ttf') as r:self.assertGreater(len(r.read()),1000)
   finally:srv.shutdown();srv.server_close();th.join()
if __name__=='__main__':unittest.main()
