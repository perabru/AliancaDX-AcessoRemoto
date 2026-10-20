from pathlib import Path
import argparse,hashlib,json,subprocess,re,os
parser=argparse.ArgumentParser(description="Check rebuilt JNI libraries without overwriting historical provenance")
parser.add_argument("--output",type=Path,default=Path(__file__).with_name("NATIVE_REBUILD_VERIFICATION.json"))
parser.add_argument("--readelf",default=os.environ.get("READELF","readelf"))
args=parser.parse_args()
root=Path(__file__).resolve().parent.parent
items=[]
for abi in ('arm64-v8a','x86_64'):
 for name in ('libeasytier_ffi.so','libeasytier_android_jni.so'):
  p=root/'app/src/main/jniLibs'/abi/name
  if not p.is_file():raise SystemExit("Missing native library: "+str(p)+"; see native-sources/README.md")
  elfh=subprocess.check_output([args.readelf,'-h',p],text=True)
  segments=subprocess.check_output([args.readelf,'-lW',p],text=True)
  aligns=[int(x.split()[-1],16) for x in segments.splitlines() if x.lstrip().startswith('LOAD ')]
  dynamic=subprocess.check_output([args.readelf,'-dW',p],text=True)
  needed=re.findall(r'\(NEEDED\).*?\[(.*?)\]',dynamic)
  symbols=subprocess.check_output([args.readelf,'-Ws',p],text=True)
  exported={x.split()[-1] for x in symbols.splitlines() if ' GLOBAL ' in x and ' DEFAULT ' in x and ' UND ' not in x}
  expected=('parse_config','run_network_instance','retain_network_instance','collect_network_infos','free_string') if 'ffi' in name else tuple('Java_com_easytier_jni_EasyTierJNI_'+x for x in ('parseConfig','runNetworkInstance','retainNetworkInstance','collectNetworkInfos','getLastError'))
  assert aligns and min(aligns)>=0x4000,(p,aligns)
  assert not any('RWE' in x for x in segments.splitlines() if 'GNU_STACK' in x),p
  assert '(TEXTREL)' not in dynamic,p
  assert all(x in exported for x in expected),(p,set(expected)-exported)
  if 'jni' in name:assert 'libeasytier_ffi.so' in needed,(p,needed)
  items.append({'abi':abi,'file':name,'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'minimum_load_alignment':min(aligns),'load_alignments':aligns,'needed':needed,'required_exports_present':True,'executable_stack':any('RWE' in x for x in segments.splitlines() if 'GNU_STACK' in x),'text_relocations': '(TEXTREL)' in dynamic})
source_dir=root/'native-sources'
archives={name:hashlib.sha256((source_dir/name).read_bytes()).hexdigest() for name in ['easytier-2.6.4-source.tar.gz','easytier-2.6.4-dependency-sources.tar.xz','easytier_jni.c'] if (source_dir/name).is_file()}
report={'schema':1,'easytier_version':'2.6.4','native_mode':'app-scoped userspace no-TUN loopback TCP forward','upstream':'https://github.com/EasyTier/EasyTier/releases/tag/v2.6.4','rust':'1.95.0 (59807616e 2026-04-14)','ndk':'27.2.12479018','android_native_api':26,'protoc':'30.2','features':['smoltcp','socks5','websocket','aes-gcm','tun','magic-dns'],'tun_runtime_enabled':False,'system_vpn_required':False,'core_rust_behavior_edits':False,'source_preparation':'prepare_native_source.py','source_hashes':archives,'artifacts':items,'abis_built':sorted({x['abi'] for x in items}),'both_required_abis_built':len(items)==4,'validation_scope':'Compiled and ELF/C/JNI ABI validated; consult main Android runtime report for actual device/network tests.','jni_mock_boundary_checks':88,'jni_mock_concurrent_calls':800}
for key in ('rust','ndk','protoc','core_rust_behavior_edits','jni_mock_boundary_checks','jni_mock_concurrent_calls'):
 report.pop(key,None)
report['historical_reference']='NATIVE_PROVENANCE.json'
report['compiler_versions']='Not inferred from ELF; retain the native build log separately.'
report['validation_scope']='ELF/load alignment/export inspection only; no runtime or mock JNI tests are executed by this command.'
args.output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(items,indent=2))
