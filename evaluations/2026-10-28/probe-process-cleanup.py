import ctypes,json,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_release_smoke_process import _is_running
from jobops.release_candidate import _python_smoke_environment
from jobops.release_smoke_process import run_release_python
failed=[]
start=time.monotonic()
for n in range(50):
    with tempfile.TemporaryDirectory(prefix='jobflow-owned-baseline-probe-') as raw:
        root=Path(raw)
        (root/'child.py').write_text("import os,time\nfrom pathlib import Path\nPath('child-pid.txt').write_text(str(os.getpid()))\ntime.sleep(1)\n")
        target=root/'parent.py'
        target.write_text("import subprocess,sys,time\nfrom pathlib import Path\nsubprocess.Popen([sys.executable,'-I','-B','child.py'])\nwhile not Path('child-pid.txt').exists(): time.sleep(0.001)\n")
        result=run_release_python(target,cwd=root,env=_python_smoke_environment(root),timeout=3)
        pid=int((root/'child-pid.txt').read_text())
        if _is_running(pid):
            elapsed=time.monotonic()
            while _is_running(pid) and time.monotonic()-elapsed<2: time.sleep(0.001)
            failed.append({'iteration':n,'post_return_liveness_ms':round((time.monotonic()-elapsed)*1000)})
print(json.dumps({'iterations':50,'failure_count':len(failed),'failures':failed,'elapsed_ms':round((time.monotonic()-start)*1000)}))
