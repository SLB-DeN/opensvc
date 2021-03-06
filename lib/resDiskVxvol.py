import re
import os
from collections import namedtuple

import rcExceptions as ex
import resDisk
from rcGlobalEnv import rcEnv
from rcUtilitiesLinux import major, get_blockdev_sd_slaves, \
                             devs_to_disks, udevadm_settle
from rcUtilities import which, justcall, cache

class Disk(resDisk.Disk):
    def __init__(self,
                 rid=None,
                 name=None,
                 vg=None,
                 **kwargs):
        resDisk.Disk.__init__(self,
                              rid=rid,
                              name=name,
                              type='disk.vxvol',
                              **kwargs)
        self.fullname = "%s/%s" % (vg, name)
        self.label = "vxvol %s" % self.fullname
        self.vg = vg
        self.devpath  = "/dev/vx/dsk/%s/%s" % (self.vg, self.name)

    def _info(self):
        data = [
          ["name", self.name],
          ["vg", self.vg],
        ]
        return data

    def vxprint(self):
        cmd = ["vxprint", "-g", self.vg, "-v"]
        out, err, ret = justcall(cmd)
        if ret == 11:
            # no lv
            return {}
        if ret != 0:
            raise ex.excError(err)
        data = {}
        for line in out.splitlines():
            words = line.split()
            if len(words) < 7:
                continue
            if words[0] == "TY":
                headers = list(words)
                continue
            lv = namedtuple("lv", headers)._make(words)
            data[lv.NAME] = lv
        return data

    def has_it(self):
        return self.name in self.vxprint()

    def is_up(self):
        """
        Returns True if the logical volume is present and activated
        """
        data = self.vxprint()
        return self.name in data and data[self.name].STATE == "ACTIVE"

    def activate_lv(self):
        cmd = ['vxvol', '-g', self.vg, 'start', self.name]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def deactivate_lv(self):
        cmd = ['vxvol', '-g', self.vg, 'stop', self.name]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def do_start(self):
        if self.is_up():
            self.log.info("%s is already up" % self.label)
            return 0
        self.activate_lv()
        self.can_rollback = True

    def remove_dev_holders(self, devpath, tree):
        dev = tree.get_dev_by_devpath(devpath)
        if dev is None:
            self.log.error("the device %s is not in the devtree", devpath)
            return
        holders_devpaths = set()
        holder_devs = dev.get_children_bottom_up()
        for holder_dev in holder_devs:
            holders_devpaths |= set(holder_dev.devpath)
        holders_devpaths -= set(dev.devpath)
        holders_handled_by_resources = self.svc.sub_devs() & holders_devpaths
        if len(holders_handled_by_resources) > 0:
            raise ex.excError("resource %s has holders handled by other resources: %s" % (self.rid, ", ".join(holders_handled_by_resources)))
        for holder_dev in holder_devs:
            holder_dev.remove(self)

    def remove_holders(self):
        tree = self.svc.node.devtree
        self.remove_dev_holders(self.devpath, tree)

    def do_stop(self):
        if not self.is_up():
            self.log.info("%s is already down" % self.label)
            return
        self.remove_holders()
        self.deactivate_lv()

    def lv_devices(self):
        """
        Return the set of sub devices.
        """
        devs = set()
        return devs
        
    def sub_devs(self):
        if not self.has_it():
            return set()
        return self.lv_devices()

    def exposed_devs(self):
        if not self.has_it():
            return set()
        devs = set()
        if os.path.exists(self.devpath):
            devs.add(self.devpath)
        return devs

