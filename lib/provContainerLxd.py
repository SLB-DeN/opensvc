import os
import provisioning
import rcExceptions as ex
from subprocess import PIPE

class Prov(provisioning.Prov):
    def is_provisioned(self):
        return self.r.has_it()

    def provisioner(self):
        if self.r.has_it():
            return
        image = self.r.oget("launch_image")
        options = self.r.oget("launch_options")
        cmd = ["/usr/bin/lxc", "launch", image] + options + [self.r.name]
        ret, out, err = self.r.vcall(cmd, stdin=PIPE)
        if ret != 0:
            raise ex.excError
        self.r.wait_for_fn(self.r.is_up, self.r.start_timeout, 2)
        self.r.can_rollback = True

    def unprovisioner(self):
        cmd = ["/usr/bin/lxc", "delete", self.r.name]
        ret, out, err = self.r.vcall(cmd)
        if ret != 0:
            raise ex.excError

