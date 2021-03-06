import provFs
import tempfile
import os
import time
import rcExceptions as ex
from rcUtilities import which, justcall, lazy
from rcBtrfs import Btrfs

class Prov(provFs.Prov):
    info = ['btrfs', 'device', 'ready']

    @lazy
    def mkfs(self):
        return ['mkfs.btrfs', '-f', '-L', self.label]

    @lazy
    def raw_label(self):
        return '{name}.' + self.r.rid.replace("#", ".")

    @lazy
    def label(self):
        return self.r.svc.name + '.' + self.r.rid.replace("#", ".")

    def is_provisioned(self):
        ret = provFs.Prov.is_provisioned(self)
        if not ret:
            return ret
        if self.subvol is None:
            return ret
        mnt = tempfile.mkdtemp()
        self.mount(mnt)
        try:
            btrfs = Btrfs(path=mnt)
            return btrfs.has_subvol(self.subvol)
        finally:
            self.cleanup(mnt)

    def current_label(self, mnt):
        cmd = ["btrfs", "filesystem", "label", mnt]
        ret, out, err = self.r.call(cmd, errlog=False)
        if ret == 0 and len(out.strip()) > 0:
            return out.strip()

    @lazy
    def subvol(self):
        l = self.r.mount_options.split(",")
        for e in l:
            if not e.startswith("subvol="):
                continue
            subvol = e.replace("subvol=", "")
            return subvol

    def cleanup(self, mnt):
        cmd = ["umount", mnt]
        self.r.vcall(cmd)
        os.removedirs(mnt)

    def write_label(self, mnt):
        current_label = self.current_label(mnt)
        if current_label is not None:
            label = current_label
            raw_label = current_label.replace(self.r.svc.name, "{name}")
        else:
            label = self.label
            raw_label = self.raw_label
        self.r.svc.set_multi(["%s.dev=%s" % (self.r.rid, "LABEL="+raw_label)])
        self.r.unset_lazy("device")
        self.wait_label(label)

    def wait_label(self, label):
        if which("findfs") is None:
            self.r.log.info("findfs program not found, wait arbitrary 20 seconds for label to be usable")
            time.sleep(20)
        cmd = ["findfs", "LABEL="+label]
        for i in range(20):
            out, err, ret = justcall(cmd)
            self.r.log.debug("%s\n%s\n%s" % (" ".join(cmd), out, err))
            if ret == 0:
                return
            self.r.log.info("label is not usable yet (%s)" % err.strip())
            time.sleep(2)
        raise ex.excError("timeout waiting for label to become usable")

    def mount(self, mnt):
        self.r.set_loopdevice()
        if self.r.loopdevice is None:
            device = self.r.device
        else:
            device = self.r.loopdevice
        cmd = ["mount", "-t", "btrfs", "-o", "subvolid=0", device, mnt]
        ret, out, err = self.r.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def create_subvol(self):
        if self.subvol is None:
            return
        mnt = tempfile.mkdtemp()
        self.mount(mnt)
        try:
            self.write_label(mnt)
            self._create_subvol(mnt)
            self.r.log.info("subvolume %s provisioned" % self.subvol)
            self.r.start()
        finally:
            self.cleanup(mnt)

    def _create_subvol(self, mnt):
        path = os.path.join(mnt, self.subvol)
        if os.path.exists(path):
            return
        cmd = ["btrfs", "subvol", "create", path]
        ret, out, err = self.r.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def provisioner(self):
        if self.r.device.startswith("LABEL=") or self.r.device.startswith("UUID="):
            self.r.log.info("skip formatting because dev is specified by LABEL or UUID")
        else:
            provFs.Prov.provisioner_fs(self)
        self.create_subvol()



