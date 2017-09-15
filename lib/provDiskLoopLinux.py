from provisioning import Provisioning
import os
import rcExceptions as ex
from converters import convert_size

class ProvisioningDisk(Provisioning):
    def __init__(self, r):
        Provisioning.__init__(self, r)

    def unprovisioner(self):
        self.r.stop()
        try:
            self.path = self.r.loopFile
        except Exception as e:
            raise ex.excError(str(e))

        if not os.path.exists(self.path):
            self.r.log.info("already unprovisioned")
            return

        self.r.log.info("unlink %s" % self.path)
        self.r.log.info("unprovisioned")
        os.unlink(self.path)

    def provisioner(self):
        try:
            self.path = self.r.loopFile
            self.size = self.r.svc.conf_get(self.r.rid, "size")
        except Exception as e:
            raise ex.excError(str(e))
        if os.path.exists(self.path):
            self.r.log.info("already provisioned")
            self.r.start()
            return
        d = os.path.dirname(self.path)
        try:
            if not os.path.exists(d):
                self.r.log.info("create directory %s"%d)
                os.makedirs(d)
            with open(self.path, 'w') as f:
                self.r.log.info("create file %s, size %s"%(self.path, self.size))
                f.seek(convert_size(self.size, _to='b', _round=512)-1)
                f.write('\0')
        except Exception as e:
            self.r.log.error("Failed to create %s: %s"% (self.path, str(e)))
            raise ex.excError

        self.r.log.info("provisioned")
        self.r.start()
