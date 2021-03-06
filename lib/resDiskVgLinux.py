import re
import os
import rcExceptions as ex
import resDisk
from rcGlobalEnv import rcEnv
from rcUtilitiesLinux import major, get_blockdev_sd_slaves, \
                             devs_to_disks, udevadm_settle
from rcUtilities import which, justcall, cache, lazy

class Disk(resDisk.Disk):
    def __init__(self,
                 rid=None,
                 name=None,
                 **kwargs):
        resDisk.Disk.__init__(self,
                              rid=rid,
                              name=name,
                              type='disk.vg',
                              **kwargs)
        self.label = "vg "+name
        self.tag = rcEnv.nodename
        self.refresh_provisioned_on_provision = True
        self.refresh_provisioned_on_unprovision = True

    def _info(self):
        data = [
          ["name", self.name],
        ]
        return data

    def is_child_dev(self, device):
        l = device.split("/")
        if len(l) != 4 or l[1] != "dev":
            return False
        if l[2] == "mapper":
            dmname = l[3]
            if "-" not in dmname:
                return False
            i = 0
            dmname.replace("--", "#")
            _l = dmname.split("-")
            if len(_l) != 2:
                return False
            vgname = _l[0].replace("#", "-")
        else:
            vgname = l[2]
        if vgname == self.name:
            return True
        return False

    def has_it(self):
        data = self.get_tags()
        if self.name in data:
            return True
        return False

    def is_up(self):
        """Returns True if the volume group is present and activated
        """
        if not self.has_it():
            return False
        data = self.get_lvs_attr()
        if self.name not in data:
            # no lv ... happens in provisioning, where lv are not created yet
            self.log.debug("no logical volumes. consider up")
            return self.tag in self.get_tags()[self.name]
        for attr in data[self.name].values():
            if re.search('....a.', attr) is not None:
                # at least one lv is active
                return True
        return False

    @cache("vg.lvs.attr")
    def get_lvs_attr(self):
        cmd = [rcEnv.syspaths.lvs, '-o', 'vg_name,lv_name,lv_attr', '--noheadings', '--separator=;']
        out, err, ret = justcall(cmd)
        data = {}
        for line in out.splitlines():
            l = line.split(";")
            if len(l) != 3:
                continue
            vgname = l[0].strip()
            lvname = l[1].strip()
            attr = l[2].strip()
            if vgname not in data:
                data[vgname] = {}
            data[vgname][lvname] = attr
        return data

    @cache("vg.tags")
    def get_tags(self):
        cmd = [rcEnv.syspaths.vgs, '-o', 'vg_name,tags', '--noheadings', '--separator=;']
        out, err, ret = justcall(cmd)
        data = {}
        for line in out.splitlines():
            l = line.split(";")
            if len(l) == 1:
                data[l[0].strip()] = []
            if len(l) == 2:
                data[l[0].strip()] = l[1].strip().split(",")
        return data

    def test_vgs(self):
        data = self.get_tags()
        if self.name not in data:
            self.clear_cache("vg.tags")
            return False
        return True

    def remove_tag(self, tag):
        cmd = ['vgchange', '--deltag', '@'+tag, self.name]
        (ret, out, err) = self.vcall(cmd)
        self.clear_cache("vg.tags")

    @lazy
    def has_metad(self):
        cmd = ["pgrep", "lvmetad"]
        out, err, ret = justcall(cmd)
        return ret == 0

    def pvscan(self):
        cmd = ["pvscan"]
        if self.has_metad:
            cmd += ["--cache"]
        ret, out, err = self.vcall(cmd, warn_to_info=True)
        self.clear_cache("vg.lvs")
        self.clear_cache("vg.lvs.attr")
        self.clear_cache("vg.tags")

    def list_tags(self, tags=[]):
        if not self.test_vgs():
            self.pvscan()
        data = self.get_tags()
        if self.name not in data:
            raise ex.excError("vg %s not found" % self.name)
        return data[self.name]

    def remove_tags(self, tags=[]):
        for tag in tags:
            tag = tag.lstrip('@')
            if len(tag) == 0:
                continue
            self.remove_tag(tag)

    def add_tags(self):
        cmd = ['vgchange', '--addtag', '@'+self.tag, self.name]
        (ret, out, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError
        self.clear_cache("vg.tags")

    def activate_vg(self):
        cmd = ['vgchange', '-a', 'y', self.name]
        ret, out, err = self.vcall(cmd)
        self.clear_cache("vg.lvs")
        self.clear_cache("vg.lvs.attr")
        self.clear_cache("vg.tags")
        if ret != 0:
            raise ex.excError

    def _deactivate_vg(self):
        cmd = ['vgchange', '-a', 'n', self.name]
        ret, out, err = self.vcall(cmd, err_to_info=True)
        self.clear_cache("vg.lvs")
        self.clear_cache("vg.lvs.attr")
        self.clear_cache("vg.tags")
        if ret == 0:
            return True
        if not self.is_up():
            return True
        return False

    def deactivate_vg(self):
        self.wait_for_fn(self._deactivate_vg, 3, 1, errmsg="deactivation failed to release all logical volumes")

    def do_start(self):
        self.clear_cache("vg.lvs")
        self.clear_cache("vg.lvs.attr")
        self.clear_cache("vg.tags")
        curtags = self.list_tags()
        tags_to_remove = set(curtags) - set([self.tag])
        if len(tags_to_remove) > 0:
            self.remove_tags(tags_to_remove)
        if self.tag not in curtags:
            self.add_tags()
        if self.is_up():
            self.log.info("%s is already up" % self.label)
            return 0
        self.can_rollback = True
        self.activate_vg()

    def remove_dev_holders(self, devpath, tree):
        dev = tree.get_dev_by_devpath(devpath)
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
        import glob
        tree = self.svc.node.devtree
        for lvdev in glob.glob("/dev/mapper/%s-*"%self.name.replace("-", "--")):
             if "_rimage_" in lvdev or "_rmeta_" in lvdev or \
                "_mimage_" in lvdev or " _mlog_" in lvdev or \
                lvdev.endswith("_mlog"):
                 continue
             self.remove_dev_holders(lvdev, tree)

    def do_stop(self):
        if not self.is_up():
            self.log.info("%s is already down" % self.label)
            return
        self.remove_holders()
        self.remove_tags([self.tag])
        udevadm_settle()
        self.deactivate_vg()

    @cache("vg.lvs")
    def vg_lvs(self):
        cmd = [rcEnv.syspaths.vgs, '--noheadings', '-o', 'vg_name,lv_name', '--separator', ';']
        out, err, ret = justcall(cmd)
        data = {}
        for line in out.splitlines():
            try:
                vgname, lvname = line.split(";")
                vgname = vgname.strip()
            except:
                pass
            if vgname not in data:
                data[vgname] = []
            data[vgname].append(lvname.strip())
        return data


    @cache("vg.pvs")
    def vg_pvs(self):
        cmd = [rcEnv.syspaths.vgs, '--noheadings', '-o', 'vg_name,pv_name', '--separator', ';']
        out, err, ret = justcall(cmd)
        data = {}
        for line in out.splitlines():
            try:
                vgname, pvname = line.split(";")
                vgname = vgname.strip()
            except:
                pass
            if vgname not in data:
                data[vgname] = []
            data[vgname].append(os.path.realpath(pvname.strip()))
        return data

    def sub_devs(self):
        if not self.has_it():
            return set()
        devs = set()
        data = self.vg_pvs()
        if self.name in data:
            devs |= set(data[self.name])
        return devs

    def exposed_devs(self):
        if not self.has_it():
            return set()
        devs = set()
        data = self.vg_lvs()
        if self.name in data:
            for lvname in data[self.name]:
                lvp = "/dev/"+self.name+"/"+lvname
                if os.path.exists(lvp):
                    devs.add(lvp)
        return devs

    def boot(self):
        self.do_stop()
