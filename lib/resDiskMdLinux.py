import re
import json
import os
import glob
import time
import rcExceptions as ex
import resDisk
import rcStatus
from rcGlobalEnv import rcEnv
from rcUtilitiesLinux import major, get_blockdev_sd_slaves, \
                             dev_to_paths
from rcUtilities import which, justcall, lazy, fcache

class Disk(resDisk.Disk):
    startup_timeout = 10

    def __init__(self,
                 rid=None,
                 uuid=None,
                 **kwargs):
        self.uuid = uuid
        self.mdadm = "/sbin/mdadm"
        resDisk.Disk.__init__(self,
                          rid=rid,
                          name=uuid,
                          type='disk.md',
                          **kwargs)
        if uuid:
            self.label = "md " + uuid
        else:
            self.label = "md"

    @lazy
    def mdadm_cf(self):
        if os.path.exists("/etc/mdadm"):
            return "/etc/mdadm/mdadm.conf"
        else:
            return "/etc/mdadm.conf"

    @lazy
    def is_shared(self):
        if self.shared is not None:
            return self.shared
        if len(self.svc.nodes|self.svc.drpnodes) < 2:
            self.log.debug("shared param defaults to 'false' due to single "
                           "node configuration")
            return False
        l = [option for option in self.svc.cd[self.rid] if \
             option.startswith("uuid@")]
        if len(l) > 0:
            self.log.debug("shared param defaults to 'false' due to scoped "
                           "configuration")
            return False
        else:
            self.log.debug("shared param defaults to 'true' due to unscoped "
                           "configuration")
            return True

    def _info(self):
        data = [
          ["uuid", self.uuid],
        ]
        return data

    def md_config_file_name(self):
        return os.path.join(self.var_d, 'disks')

    def md_config_import(self):
        p = self.md_config_file_name()
        if not os.path.exists(p):
            return set()
        with open(p, "r") as ofile:
            return json.load(ofile)

    def md_config_export(self):
        devs = self.sub_devs()
        disk_ids = set()
        for dev in devs:
            treedev = self.svc.node.devtree.get_dev_by_devpath(dev)
            if not treedev:
                continue
            disk_ids.add(treedev.alias)
        with open(self.md_config_file_name(), "w") as ofile:
             json.dump(list(disk_ids), ofile)

    def postsync(self):
        self.auto_assemble_disable()

    def down_state_alerts(self):
        if not self.is_shared:
            return
        devnames = self.md_config_import()
        devnames = set([d for d in devnames if not d.startswith("md")])
        if len(devnames) == 0:
            return

        dt = self.svc.node.devtree
        aliases = set([d.alias for d in dt.dev.values()])
        not_found = devnames - aliases
        if len(not_found) > 0:
            self.status_log("md member missing: %s" % ", ".join(sorted(list(not_found))))

    def presync(self):
        if self.uuid is None:
            return
        if not self.is_shared:
            return
        s = self.svc.group_status(excluded_groups=set(["app", "sync", "task", "disk.scsireserv"]))
        if self.svc.options.force or s['avail'].status == rcStatus.UP:
            self.md_config_export()

    def files_to_sync(self):
        if self.uuid is None:
            return
        if not self.is_shared:
            return []
        return [self.md_config_file_name()]

    def md_devpath(self):
        devpath = self.devpath()
        if os.path.exists(devpath):
            return devpath
        out, err, ret = self.mdadm_scan()
        for line in out.splitlines():
            if self.uuid in line:
                devname = line.split()[1]
                if os.path.exists(devname):
                    return devname
        raise ex.excError("unable to find a devpath for md")

    def devname(self):
        if self.svc.namespace:
            return "/dev/md/"+self.svc.namespace.lower()+"."+self.svc.name.split(".")[0]+"."+self.rid.replace("#", ".")
        else:
            return "/dev/md/"+self.svc.name.split(".")[0]+"."+self.rid.replace("#", ".")

    def devpath(self):
        return "/dev/disk/by-id/md-uuid-"+self.uuid

    def exposed_devs(self):
        if self.uuid == "" or self.uuid is None:
            return set()
        try:
            return set([os.path.realpath(self.md_devpath())])
        except:
            return set()

    def assemble(self):
        cmd = [self.mdadm, "--assemble", self.devname(), "-u", self.uuid]
        ret, out, err = self.vcall(cmd, warn_to_info=True)
        if ret == 2:
            self.log.info("no changes were made to the array")
        elif ret != 0:
            raise ex.excError
        else:
            self.wait_for_fn(self.has_it, self.startup_timeout, 1, errmsg="waited too long for devpath creation")

    def manage_stop(self):
        cmd = [self.mdadm, "--stop", self.md_devpath()]
        ret, out, err = self.vcall(cmd, warn_to_info=True)
        if ret != 0:
            raise ex.excError

    def detail(self):
        try:
            devpath = self.md_devpath()
        except ex.excError as e:
            return "State : " + str(e)
        if not os.path.exists(devpath):
            return "State : devpath does not exist"
        cmd = [self.mdadm, "--detail", devpath]
        out, err, ret = justcall(cmd)
        if "cannot open /dev" in err:
            return "State : devpath does not exist"
        if ret != 0:
            if "does not appear to be active" in err:
                return "State : md does not appear to be active"
            raise ex.excError(err)
        return out

    def detail_status(self):
        buff = self.detail()
        for line in buff.split("\n"):
            line = line.strip()
            if line.startswith("State :"):
                return line.split(" : ")[-1]
        return "unknown"

    def has_it(self):
        if self.uuid == "" or self.uuid is None:
            return False
        return self.uuid in self.mdadm_scan_v()[0]

    def is_up(self):
        if not self.has_it():
            return False
        buff = self.detail_status()
        states = buff.split(", ")
        if len(states) > 1:
            self.status_log(buff, "warn")
        if "Not Started" in states or len(states) == 0:
            return False
        if "devpath does not exist" in states or \
           "unable to find a devpath for md" in states:
            return False
        return True

    def auto_assemble_disabled(self):
        if self.uuid == "" or self.uuid is None:
            return True
        if not os.path.exists(self.mdadm_cf):
            self.status_log("auto-assemble is not disabled")
            return False
        with open(self.mdadm_cf, "r") as ofile:
            for line in ofile.readlines():
                words = line.strip().split()
                if len(words) < 2:
                    continue
                if words[0] == "AUTO" and "-all" in words:
                    return True
                if words[0] == "ARRAY" and words[1] == "<ignore>" and \
                   "UUID="+self.uuid in words:
                    return True
        self.status_log("auto-assemble is not disabled")
        return False

    def auto_assemble_disable(self):
        if self.uuid == "" or self.uuid is None:
            return
        if self.auto_assemble_disabled():
            return
        self.log.info("disable auto-assemble in %s" % self.mdadm_cf)
        with open(self.mdadm_cf, "a+") as ofile:
            ofile.write("ARRAY <ignore> UUID=%s\n" % self.uuid)

    def _status(self, verbose=False):
        if self.uuid is None:
            return rcStatus.NA
        self.auto_assemble_disabled()
        s = resDisk.Disk._status(self, verbose=verbose)
        if s == rcStatus.DOWN:
             self.down_state_alerts()
        return s

    def do_start(self):
        if self.uuid is None:
            raise ex.excError("uuid is not set")
        self.auto_assemble_disable()
        if self.is_up():
            self.log.info("md %s is already assembled" % self.uuid)
            return 0
        self.can_rollback = True
        self.assemble()
        self._create_static_name()

    def do_stop(self):
        if self.uuid is None:
            self.log.warning("uuid is not set: skip")
            return
        self.auto_assemble_disable()
        if not self.is_up():
            self.log.info("md %s is already down" % self.uuid)
            return
        self.manage_stop()

    def _create_static_name(self):
        self.create_static_name(self.md_devpath())

    @fcache
    def sub_devs(self):
        if self.uuid == "" or self.uuid is None:
            # try to get the info from the config so pr co-resource can reserv
            # during provision
            try:
                devs = self.conf_get("devs")
                if devs is None:
                    return set()
                return set([os.path.realpath(dev) for dev in devs.split()])
            except ex.OptNotFound:
                return set()
        try:
            devpath = self.md_devpath()
        except ex.excError as e:
            return self.sub_devs_inactive()
        if os.path.exists(devpath):
            return self.sub_devs_active()
        else:
            return self.sub_devs_inactive()

    def mdadm_scan(self):
        cmd = [self.mdadm, "--detail", "--scan"]
        return justcall(cmd)

    def mdadm_scan_v(self):
        cmd = [self.mdadm, "-E", "--scan", "-v"]
        return justcall(cmd)

    def sub_devs_inactive(self):
        devs = set()
        out, err, ret = self.mdadm_scan_v()
        if ret != 0:
            return devs
        lines = out.split("\n")

        if len(lines) < 2:
            return set()
        inblock = False
        paths = set()
        for line in lines:
            if "UUID="+self.uuid in line:
                inblock = True
                continue
            if inblock and "devices=" in line:
                l = line.split("devices=")[-1].split(",")
                l = map(lambda x: os.path.realpath(x), l)
                for dev in l:
                    _paths = set(dev_to_paths(dev))
                    if set([dev]) != _paths:
                        paths |= _paths
                    devs.add(dev)
                break
        # discard paths from the list (mdadm shows both mpaths and paths)
        devs -= paths

        self.log.debug("found devs %s held by md %s" % (devs, self.uuid))
        return devs

    def sub_devs_active(self):
        devs = set()

        try:
            lines = self.detail().split("\n")
        except ex.excError as e:
            return set()

        if len(lines) < 2:
            return set()
        for line in lines[1:]:
            if "/dev/" not in line:
                continue
            devpath = line.split()[-1]
            devpath = os.path.realpath(devpath)
            devs.add(devpath)

        self.log.debug("found devs %s held by md %s" % (devs, self.uuid))
        return devs

    def sync_resync(self):
        faultydev = None
        buff = self.detail()
        added = 0
        removed = buff.count("removed")
        if removed == 0:
            self.log.info("skip: no removed device")
            return
        if "Raid Level : raid1" not in buff:
            self.log.info("skip: non-raid1 md")
            return
        if not self.is_up():
            self.log.info("skip: non-up md")
            return
        devpath = self.devpath()
        for line in buff.split("\n"):
            line = line.strip()
            if "faulty" in line:
                faultydev = line.split()[-1]
                cmd = [self.mdadm, "--re-add", devpath, faultydev]
                ret, out, err = self.vcall(cmd, warn_to_info=True)
                if ret != 0:
                    raise ex.excError("failed to re-add %s to %s"%(faultydev, devpath))
                added += 1
        if removed > added:
            self.log.error("no faulty device found to re-add to %s remaining "
                           "%d removed legs"% (devpath, removed - added))

