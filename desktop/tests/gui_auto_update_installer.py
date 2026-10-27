#!/usr/bin/env python3
"""Exercise the real installer Tk auto-update lifecycle, with a fake worker.

No installation or Windows execution occurs. Checks automatic start, visible
progress, automatic success exit and a persistent, retryable error state.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import ImageGrab
import installer_python as installer

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--report',type=Path,required=True)
parser.add_argument('--screenshots',type=Path,required=True)
args=parser.parse_args()
checks=[]; errors=[]

def check(name,value):
    if not value: raise AssertionError(name)
    checks.append(name)

with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'LOCALAPPDATA':directory}):
    started=threading.Event(); finish=threading.Event()
    def work(self,role):
        started.set()
        self.write('Verificando o ambiente existente e preservando as configurações...')
        finish.wait(3)
        self.events.put(('committing',''))
        self.events.put(('done','Aplicativo atualizado.'))
    with patch.object(installer.Installer,'worker',work):
        app=installer.Installer('controller',True,False,True)
        app.root.report_callback_exception=lambda *values:errors.append(str(values))
        def snapshot_success():
            check('auto-update starts without pressing Install',started.is_set())
            check('installer visible during operation',app.root.winfo_viewable())
            check('operation runs while primary button is disabled','disabled' in app.primary.state())
            args.screenshots.mkdir(parents=True,exist_ok=True)
            with ImageGrab.grab(xdisplay=os.environ['DISPLAY']) as image:
                image.save(args.screenshots/'installer-auto-progress.png')
            finish.set()
        app.root.after(350,snapshot_success)
        app.root.after(6000,app.root.destroy)
        check('auto-update exits successfully without pressing Continue',app.run()==0)

    started.clear()
    def failure(self,role):
        started.set(); self.events.put(('error','Falha de teste: instalação anterior preservada.'))
    notices=[]
    with patch.object(installer.Installer,'worker',failure),patch.object(installer.messagebox,'showerror',side_effect=lambda *a,**k:notices.append(a)):
        app=installer.Installer('host',True,False,True)
        app.root.report_callback_exception=lambda *values:errors.append(str(values))
        def snapshot_failure():
            check('failed auto-update remains visible',app.root.winfo_viewable())
            check('failure status is explicit',app.status.get()=='A operacao nao foi concluida.')
            check('failure offers retry',app.primary.cget('text')=='Tentar novamente' and 'disabled' not in app.primary.state())
            check('failure shows detail dialog',bool(notices))
            with ImageGrab.grab(xdisplay=os.environ['DISPLAY']) as image:
                image.save(args.screenshots/'installer-auto-error.png')
            app.close()
        app.root.after(600,snapshot_failure)
        app.root.after(6000,app.root.destroy)
        check('failed auto-update returns error for helper',app.run()==1)
    check('no Tk callback exceptions',not errors)
report={'passed':True,'checks_count':len(checks),'checks':checks,'tk_errors':errors,
        'scope':'Real Tk auto-update installer with injected success/failure worker; no Windows execution or real install.'}
args.report.parent.mkdir(parents=True,exist_ok=True)
args.report.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
