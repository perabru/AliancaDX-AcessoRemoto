#!/usr/bin/env python3
"""Real Tk saved-access lifecycle with temporary vaults and localhost TLS only.

Mesh discovery is replaced by a deterministic localhost route in this fixture;
the production code parser, vault, registry, TLS Peer and authorization callbacks
remain real. No personal code/password enters screenshots or JSON evidence.
"""
from pathlib import Path
import argparse,base64,contextlib,hashlib,io,json,os,sys,tempfile,time,traceback
from unittest.mock import Mock,patch
import tkinter as tk

DESKTOP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(DESKTOP))
from PIL import Image,ImageDraw
import controller,mesh,net
from computer_book import ComputerBook
from mesh_ui import pairing_address

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--display',help='Existing X display, for example127.0.0.1:98')
parser.add_argument('--output-dir',type=Path,required=True)
args=parser.parse_args()
if args.display:os.environ['DISPLAY']=args.display
args.output_dir.mkdir(parents=True,exist_ok=True)
checks=[];errors=[];approvals=[];peers=[];commands=[];owner=manager=server=None
expected_error=[False]
SOURCES=('controller.py','computer_book.py','mesh_ui.py','mesh.py','net.py')

def check(name,value):
    if not value:raise AssertionError(name)
    checks.append(name)

def pump(seconds=.06):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        owner.update();time.sleep(.005)
    if errors:raise AssertionError('Unexpected native dialog/callback: '+errors[-1])

def wait(name,predicate,timeout=8):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        pump(.025)
        if predicate():checks.append(name);return
    raise AssertionError('Timed out: '+name)

def screenshot(app,name):
    import mss
    check('private fields masked before '+name,app.mesh_panel.code_entry['show']=='*' and app.password_entry['show']=='*')
    app.root.lift();pump(.08)
    with mss.MSS(display=os.environ.get('DISPLAY')) as capture:
        shot=capture.grab({'left':app.root.winfo_rootx(),'top':app.root.winfo_rooty(),'width':app.root.winfo_width(),'height':app.root.winfo_height()})
        Image.frombytes('RGB',shot.size,shot.rgb).save(args.output_dir/(name+'.png'))

def route(app):
    manager=app.mesh_panel.manager
    manager.start=Mock()
    manager.wait_ready=Mock(return_value=('127.0.0.1',server.port))
    manager.snapshot=Mock(return_value=mesh.MeshStatus(state='ready',endpoint=('127.0.0.1',server.port)))

def frame():
    image=Image.new('RGB',(960,540),'#215244');ImageDraw.Draw(image).text((30,30),'PC de teste · acesso salvo',fill='white')
    output=io.BytesIO();image.save(output,format='JPEG',quality=50)
    return {'type':'screen','captureId':'saved-qa','monitorId':'all','monitors':[],'width':960,'height':540,'left':0,'top':0,'streamVersion':1,'frameId':1,'image':base64.b64encode(output.getvalue()).decode()}

try:
  with contextlib.ExitStack() as stack:
    folder=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='aliancadx-saved-qa-')))
    os.environ['LOCALAPPDATA']=str(folder/'user')
    owner=tk.Tk();owner.withdraw()
    owner.report_callback_exception=lambda *values:errors.append(''.join(traceback.format_exception(*values)))
    def error_dialog(title,*values,**kwargs):
        if not expected_error[0]:errors.append(str(title))
    stack.enter_context(patch.object(controller.messagebox,'showerror',side_effect=error_dialog))
    stack.enter_context(patch.object(controller.messagebox,'showwarning',side_effect=error_dialog))
    def connected(peer,name,address):
        peers.append(peer);peer.on_message=commands.append;peer.send(frame())
    def approve(name,address):approvals.append(True);return True
    server=net.HostServer('127.0.0.1',0,'temporary-qa-password',approve,connected,lambda *a:None,state_dir=folder/'host')
    server.start();stack.callback(server.stop)
    pairing=mesh.generate_pairing(port=server.port,fingerprint=server.fingerprint)
    settings=controller.Settings(folder/'controller.json')
    manager=controller.ControllerSessions(owner,settings=settings)
    a=manager.new_window();route(a);pump()
    a.mesh_panel.code_var.set(pairing.code);a.mesh_panel.remember_var.set(True);a.mesh_panel.saved_name_var.set('Notebook de teste')
    check('native Continue saves before any authentication',a.mesh_panel.select_code() and not approvals and a.peer is None)
    record=a.book.entries()[0]
    check('saved row exists before first authentication',record['last_seen'] is None and record['name']=='Notebook de teste')
    # Recreate both persistence readers; no secret comes from Controller A's memory.
    manager.settings=controller.Settings(settings.path);manager.book=ComputerBook(a.book.path)
    b=manager.new_window();route(b);pump()
    check('fresh native controller opens saved list without a password',b.selected_page=='computers' and b.password_var.get()=='' and b.book is not a.book)
    check('fresh native controller recovers private code from vault',b._prepare_computer_connection(record) and b.mesh_pairing==pairing and b.password_var.get()=='')
    b._show_computers();pump();screenshot(b,'saved-before-first-auth')
    a.password_var.set('temporary-qa-password');a.connect()
    wait('first saved-code session completes real TLS and authorization',lambda:a.peer is not None and a.current_screen is not None and len(approvals)==1)
    a.disconnect();wait('ending first session preserves saved access and clears password',lambda:a.peer is None and a.password_var.get()=='' and len(a.book.entries())==1)
    b._prepare_computer_connection(record);b.password_var.set('temporary-qa-password');b.connect()
    wait('fresh controller reconnects with another explicit authorization',lambda:b.peer is not None and b.current_screen is not None and len(approvals)==2)
    active=b.peer
    a._update_computer_code(record);a.mesh_panel.code_var.set(mesh.generate_pairing(port=server.port,fingerprint=server.fingerprint).code)
    a._cancel_connection_action();pump()
    check('native Cancel update keeps same row and original private code',a.book.get(record['id'])['address']==record['address'] and a.mesh_panel.load_remembered(record['address'])==pairing)
    rotated=mesh.generate_pairing(port=server.port,fingerprint=server.fingerprint)
    a._update_computer_code(record);a.mesh_panel.code_var.set(rotated.code)
    expected_error[0]=True
    with patch.object(a.book,'_write',side_effect=OSError('Injected fixture write failure')):
        check('native write failure reports failure without selecting replacement',a.mesh_panel.select_code() is False)
    expected_error[0]=False
    check('failed update preserves fresh registry and vault',ComputerBook(a.book.path).get(record['id'])['address']==record['address'] and a.mesh_panel.load_remembered(record['address'])==pairing)
    screenshot(a,'update-code-masked')
    check('retrying update succeeds without deleting saved row',a.mesh_panel.select_code())
    updated=a.book.get(record['id'])
    check('rotated code retains stable saved identity and friendly name',updated['id']==record['id'] and updated['name']==record['name'] and updated['address']==pairing_address(rotated) and len(a.book.entries())==1)
    check('editing another window never closes active remote session',b.peer is active and not active.closed)
    b.disconnect();pump()
    b._prepare_reconnect();pump()
    check('manual reconnect reloads rotated code by stable saved identity',b.mesh_pairing==rotated and b.password_var.get()=='')
    c=manager.new_window();route(c);pump()
    check('stale saved selection resolves newly stored code by stable ID',c._prepare_computer_connection(record) and c.mesh_pairing==rotated and c.password_var.get()=='')
    c.password_var.set('temporary-qa-password');c.connect()
    wait('updated saved code connects with fresh TLS and authorization',lambda:c.peer is not None and c.current_screen is not None and len(approvals)==3)
    check('all native fields keep password and code masked',all(app.password_entry['show']=='*' and app.mesh_panel.code_entry['show']=='*' for app in (a,b,c)))
    check('registry and settings never contain password or private code',all('temporary-qa-password' not in p.read_text() and pairing.code not in p.read_text() and rotated.code not in p.read_text() for p in (a.book.path,settings.path)))
    check('no unexpected native callbacks or dialogs',not errors)
    result={'passed':True,'checks':checks,'checks_count':len(checks),'scope':'Real Tk controllers, fresh persistence readers, production vault/registry/parser and localhost TLS Peer; deterministic mesh route and fixture-only authorization.','limitations':['No installed Windows build or physical device tested.','Mesh internet discovery is replaced only in this fixture; no personal PC is contacted.','Synthetic PC frames; Windows DPAPI is not executed on Linux.'],'source_sha256':{name:hashlib.sha256((DESKTOP/name).read_bytes()).hexdigest() for name in SOURCES}}
except BaseException as error:
    result={'passed':False,'checks':checks,'checks_count':len(checks),'error':str(error),'traceback':traceback.format_exc(),'errors':errors}
finally:
    if manager:
        try:manager.close_all()
        except BaseException:pass
    if server:server.stop()
    (args.output_dir/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not result['passed']:sys.exit(1)
