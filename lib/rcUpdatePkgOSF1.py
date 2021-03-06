from __future__ import print_function
import os
import sys
import tarfile
from rcGlobalEnv import rcEnv

repo_subdir = "tar"

def update(fpath):
    cmd = sys.executable + ' ' + rcEnv.paths.preinstall
    ret = os.system(cmd)
    if ret != 0:
        return

    oldpath = os.getcwd()
    os.chdir("/")
    tar = tarfile.open(fpath)
    try:
        tar.extractall()
        tar.close()
    except:
        try:
            os.unlink(fpath)
        except:
            pass
        print("failed to unpack", file=sys.stderr)
        return 1
    try:
        os.unlink(fpath)
    except:
        pass

    cmd = sys.executable + ' ' + rcEnv.paths.postinstall
    return os.system(cmd)
