import provisioning
from rcUtilities import justcall, which
from converters import convert_size
from rcGlobalEnv import rcEnv
import os
import rcExceptions as ex
import time

class Prov(provisioning.Prov):
    def __init__(self, r):
        provisioning.Prov.__init__(self, r)

    def provisioner(self):
        if not which('vgdisplay'):
            self.r.log.error("vgdisplay command not found")
            raise ex.excError

        if not which('lvcreate'):
            self.r.log.error("lvcreate command not found")
            raise ex.excError

        try:
            self.size = self.r.svc.conf_get(self.r.rid, "size")
            self.size = convert_size(self.size, _to="m")
            self.vg = self.r.svc.conf_get(self.r.rid, "vg")
        except Exception as e:
            self.r.log.info("skip lv provisioning: %s" % str(e))
            return

        cmd = ['vgdisplay', self.vg]
        out, err, ret = justcall(cmd)
        if ret != 0:
            self.r.log.error("volume group %s does not exist"%self.vg)
            raise ex.excError

        dev = os.path.basename(self.r.device)

        # create the logical volume
        cmd = ['lvcreate', '-n', dev, '-L', str(self.size)+'M', self.vg]
        ret, out, err = self.r.vcall(cmd)
        if ret != 0:
            raise ex.excError



