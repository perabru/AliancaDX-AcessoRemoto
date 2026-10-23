#!/usr/bin/env python3
"""Exercise two real Tk Controller windows and two local production TLS peers.

Run from any directory with Python dependencies from desktop/requirements.txt.
A graphical display is required. On Linux, use an Xvfb display; on Windows run
in an interactive desktop. This is a fixture, not a test against users' PCs:
temporary TLS identities, passwords and auto-approval exist only on localhost.
"""
from pathlib import Path
import argparse,base64,contextlib,hashlib,io,json,os,platform,sys,tempfile,time,traceback
from types import SimpleNamespace
import tkinter as tk
DESKTOP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(DESKTOP))
from PIL import Image,ImageDraw
import controller,net

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--report',type=Path,help='Optional JSON evidence output path')
parser.add_argument('--display',help='Optional X display, for example 127.0.0.1:99')
args=parser.parse_args()
if args.display:
    os.environ['DISPLAY']=args.display
SOURCE_NAMES=('controller.py','net.py','computer_book.py','transfer.py','desktop_ui.py','desktop_geometry.py','mesh.py','mesh_ui.py')
SOURCE_HASHES={name:hashlib.sha256((DESKTOP/name).read_bytes()).hexdigest() for name in SOURCE_NAMES}
checks=[]
errors=[]
servers=[]
records=[]
owner=None
manager=None

def check(name,condition):
    if not condition: raise AssertionError(name)
    checks.append(name)

def pump(seconds=.06):
    stop=time.monotonic()+seconds
    while time.monotonic()<stop:
        owner.update()
        time.sleep(.005)
    if errors: raise AssertionError('Tk callback error: '+errors[-1])

def until(name,fn,timeout=8):
    stop=time.monotonic()+timeout
    while time.monotonic()<stop:
        pump(.025)
        if fn():
            checks.append(name)
            return
    raise AssertionError('Timed out: '+name)

def frame(name,color,index):
    image=Image.new('RGB',(960,540),color)
    ImageDraw.Draw(image).text((30,30),'PC '+name,fill='white')
    output=io.BytesIO();image.save(output,format='JPEG',quality=50)
    return {'type':'screen','captureId':'capture-'+name,'monitorId':'all','monitors':[],
        'width':960,'height':540,'left':0,'top':0,'streamVersion':1,'frameId':index,
        'image':base64.b64encode(output.getvalue()).decode()}

try:
  with contextlib.ExitStack() as stack:
    state=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='aliancadx-gui-fixture-')))
    owner=tk.Tk();owner.withdraw()
    owner.report_callback_exception=lambda *args: errors.append(''.join(traceback.format_exception(*args)))
    settings=controller.Settings(state/'controller.json')
    for i in range(2):
        record={'name':chr(65+i),'consent':[],'messages':[],'peers':[],'disconnected':[]}
        def connected(peer,name,address,record=record):
            record['peers'].append(peer)
            peer.on_message=record['messages'].append
        def consent(name,address,record=record):
            record['consent'].append((name,address));return True
        server=net.HostServer('127.0.0.1',0,'test-password-2026',consent,connected,
            lambda peer,reason,record=record:record['disconnected'].append(str(reason)),
            state_dir=state/('host-'+str(i)),status_callback=lambda i=i:'QA PC '+str(i+1))
        server.start();servers.append(server);records.append(record);stack.callback(server.stop)
        settings.data['pins'][controller.pin_key('127.0.0.1',server.port)]=server.fingerprint.lower()
    manager=controller.ControllerSessions(owner,settings=settings)
    stack.callback(manager.close_all)
    a=manager.new_window();b=manager.new_window()
    pump(.1)
    check('two real Tk Toplevel Controller windows',len(manager.controllers)==2 and a.root.winfo_exists() and b.root.winfo_exists())
    check('shared settings and book, distinct mutable command/image state',a.settings is b.settings and a.book is b.book and a.outgoing is not b.outgoing and a.frame_lock is not b.frame_lock and a.mesh_panel.manager is not b.mesh_panel.manager)
    check('two populated session selectors',len(a.session_windows_combo['values'])==2 and len(b.session_windows_combo['values'])==2)
    check('hidden Tk owner remains hidden',owner.state()=='withdrawn')
    for app,server in zip((a,b),servers):
        app._use_lan();app.address_var.set('127.0.0.1');app.port_var.set(str(server.port));app.password_var.set('test-password-2026')
        app.connect()
    until('two production TLS sessions concurrently authenticated',lambda:a.peer and b.peer and not a.connecting and not b.connecting)
    check('each host received its own authorization request',all(len(r['consent'])==1 for r in records))
    check('each controller peer and file transport independent',a.peer is not b.peer and a.transfers is not b.transfers)
    for record,color in zip(records,('#235044','#334970')):
        record['peers'][0].send(frame(record['name'],color,1))
    until('both native canvases decode and present their own JPEG',lambda:a.current_screen and b.current_screen and a.photo is not None and b.photo is not None)
    check('displayed capture identities isolated',a.current_screen['captureId']=='capture-A' and b.current_screen['captureId']=='capture-B')
    until('both frame presentation acknowledgements returned to correct TLS host',lambda:all(any(m.get('type')=='stream_feedback' and m.get('frameId')==1 for m in r['messages']) for r in records))
    manager.focus_session(a.session_window_id);pump(.1)
    x,y,w,h=a.image_bounds
    a._mouse_down(SimpleNamespace(x=x+w//2,y=y+h//2,num=1))
    a._key_down(SimpleNamespace(keysym='Control_L',char='',keycode=37))
    check('real canvas holds drag and Control before switch',a.buttons_down=={'left'} and a.keys_down.get(37)=='ctrl')
    before_b_inputs=sum(m.get('type')=='input' for m in records[1]['messages'])
    manager.focus_session(b.session_window_id);pump(.1)
    check('switch clears held keys and mouse on previous canvas',not a.buttons_down and not a.keys_down)
    until('switch release delivered only to previous host',lambda:any(m.get('type')=='input' and m.get('action')=='release' for m in records[0]['messages']))
    check('prior host input capture never crossed into next host',all(m.get('captureId')!='capture-A' for m in records[1]['messages']))
    x,y,w,h=b.image_bounds
    b._mouse_down(SimpleNamespace(x=x+w//2,y=y+h//2,num=3));b._mouse_up(SimpleNamespace(x=x+w//2,y=y+h//2,num=3))
    until('second host receives complete right-click',lambda:len([m for m in records[1]['messages'] if m.get('type')=='input' and m.get('action')=='button' and m.get('button')=='right'])==2)
    check('second-host mouse capture never reached first host',all(m.get('captureId')!='capture-B' for m in records[0]['messages']))
    entries=b.book.entries()
    entry_a=next(row for row in entries if row['port']==servers[0].port)
    b._refresh_computer_rows();b.computers_tree.selection_set(entry_a['id']);b._connect_computer();pump(.1)
    check('saved active computer focuses existing session without new window',len(manager.controllers)==2 and owner.focus_get()==a.canvas)
    a.close();pump(.2)
    check('closing A leaves B, its TLS peer and hidden owner alive',len(manager.controllers)==1 and b.root.winfo_exists() and b.peer and not b.peer.closed and owner.winfo_exists())
    check('B selector updates after A closes',len(b.session_windows_combo['values'])==1 and b.session_window_ids==[b.session_window_id])
    records[1]['peers'][0].send(frame('B','#4e437a',2))
    until('B still renders and ACKs fresh frames after A closes',lambda:b.current_screen and b.current_screen.get('frameId')==2 and any(m.get('type')=='stream_feedback' and m.get('frameId')==2 for m in records[1]['messages']))
    b.close()
    check('last session close unregisters all windows',not manager.controllers)
    try: owner.winfo_exists();root_destroyed=False
    except tk.TclError:root_destroyed=True
    check('last session closes the hidden Tk owner',root_destroyed)
    check('no Tk callback exceptions',not errors)
    result={'passed':True,'checks':checks,'checks_count':len(checks),'scope':'Real Tk canvases and production localhost TCP/TLS HostServer + Peer. Test hosts autoapprove only inside this fixture.', 'limitations':['Does not install Windows packages or measure internet relay or packet loss.','Screens are generated fixtures, not captures of a real PC desktop.'],'tk_errors':errors}
except BaseException as error:
    result={'passed':False,'checks':checks,'checks_count':len(checks),'error':str(error),'traceback':traceback.format_exc(),'tk_errors':errors}
    try:
        if manager:manager.close_all()
    except BaseException:pass
finally:
    for server in servers:server.stop()
    result['source_sha256']=SOURCE_HASHES
    result['tk_version']=tk.TkVersion
    result['operating_system']=platform.system()
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if not result['passed']:sys.exit(1)
