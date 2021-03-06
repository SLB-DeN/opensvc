import checks
import os
from rcUtilities import justcall, which
from rcGlobalEnv import rcEnv

class check(checks.check):
    prefixes = [os.path.join(os.sep, "usr", "sbin")]
    fmadm = "fmadm"
    chk_type = "fm"
    chk_name = "Solaris fmadm"

    def find_fmadm(self):
        if which(self.fmadm):
            return self.fmadm
        for prefix in self.prefixes:
            fmadm = os.path.join(prefix, self.fmadm)
            if os.path.exists(fmadm):
                return fmadm
        return

    def do_check(self):
        r = self.do_check_ldpdinfo()
        return r

    def do_check_ldpdinfo(self):
        fmadm = self.find_fmadm()
        if fmadm is None:
            return self.undef
        os.chdir(rcEnv.paths.pathtmp)
        cmd = [fmadm, 'faulty']
        out, err, ret = justcall(cmd)
        if ret != 0:
            return self.undef
        r = []
        r.append({
              "instance": 'faults ',
              "value": str(len(out)),
              "path": '',
            })
        return r
