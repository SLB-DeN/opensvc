import rcStatus
import rcExceptions as ex
from rcGlobalEnv import rcEnv
Res = __import__("resFsLinux")

class Mount(Res.Mount):
    def _status(self, verbose=False):
        try:
            s = self.svc.get_res_val(self.vcs_name, 'State')
        except ex.excError as e:
            self.status_log(str(e))
            return rcStatus.WARN

        if s == "ONLINE":
            return rcStatus.UP
        elif s == "OFFLINE":
            return rcStatus.DOWN
        else:
            self.status_log(s)
            return rcStatus.WARN

