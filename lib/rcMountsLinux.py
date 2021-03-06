import os

from rcGlobalEnv import rcEnv
import rcMounts
from rcLoopLinux import file_to_loop
from rcUtilities import justcall

class Mounts(rcMounts.Mounts):
    df_one_cmd = [rcEnv.syspaths.df, '-l']

    def match_mount(self, i, dev, mnt):
        """Given a line of 'mount' output, returns True if (dev, mnt) matches
        this line. Returns False otherwize. Also care about weirdos like loops
        and binds, ...
        """
        if i.mnt != mnt:
            return False
        if i.dev == dev:
            return True
        if i.dev in file_to_loop(dev):
            return True
        if dev.startswith(os.sep) and os.path.isdir(dev):
            # zfs datasets <pool>/<ds> might match the isdir test because the
            # daemon cwd is /, but we don't want them to be considered a match
            src_dir_dev = self.get_src_dir_dev(dev)
            if i.dev == src_dir_dev:
                return True
        return False

    def parse_mounts(self):
        out, err, ret = justcall([rcEnv.syspaths.mount])
        out = out.replace(" (deleted)", "")
        mounts = []
        for l in out.split('\n'):
            if len(l.split()) != 6:
                break
            dev, null, mnt, null, type, mnt_opt = l.split()
            m = rcMounts.Mount(dev, mnt, type, mnt_opt.strip('()'))
            mounts.append(m)
        return mounts

if __name__ == "__main__" :
    for m in Mounts():
        print(m)
