import os
import glob
import json
import time

import resDisk
import rcExceptions as ex
import rcStatus
from lock import cmlock
from rcGlobalEnv import rcEnv
from rcUtilities import justcall, qcall, which, lazy, cache, clear_cache
from rcZfs import zpool_devs, zpool_getprop, zpool_setprop
from converters import convert_duration


class Disk(resDisk.Disk):
    """
    Zfs pool resource driver.
    """
    def __init__(self,
                 rid=None,
                 name=None,
                 multihost=None,
                 **kwargs):
        resDisk.Disk.__init__(self,
                              rid=rid,
                              name=name,
                              type='disk.zpool',
                              **kwargs)
        self.multihost = multihost
        self.label = 'zpool ' + name if name else "<undefined>"

    def _info(self):
        data = [
          ["name", self.name],
        ]
        return data

    @lazy
    def sub_devs_name(self):
        return os.path.join(self.var_d, 'sub_devs')

    def files_to_sync(self):
        return [self.sub_devs_name]

    def presync(self):
        """ this one is exported as a service command line arg
        """
        self.update_sub_devs_cache()

    def has_it(self):
        """Returns True if the pool is present
        """
        if not which("zpool"):
            raise ex.excError("zpool command not found")
        _, _, ret = justcall(['zpool', 'list', self.name])
        if ret == 0 :
            return True
        return False

    def zpool_health(self, status):
        for line in status.splitlines():
            line = line.strip()
            if not line.strip().startswith("state:"):
                continue
            return line.split(":", 1)[-1].strip()
        return "unknown"

    def zpool_errors(self, status):
        errors = []
        for line in status.splitlines():
            if not line.strip().startswith("errors:"):
                continue
            entry = line.split(":", 1)[-1].strip()
            if entry == "No known data errors":
                continue
            errors.append(entry)
        return errors

    @cache("zpool.status.{args[1]}")
    def zpool_status(self, name):
        cmd = ["zpool", "status", "-v", name]
        out, err, ret = justcall(cmd)
        return out+err

    def _status(self, verbose=False):
        status = self.zpool_status(self.name)
        errors = self.zpool_errors(status)
        if errors:
            for error in errors:
                if "pool I/O is currently suspended" in error:
                    self.status_log("pool I/O is currently suspended")
                    return rcStatus.WARN
                self.status_log(error)
        if self.is_up():
            return rcStatus.UP
        else:
            return rcStatus.DOWN

    def is_up(self):
        """Returns True if the pool is present and activated
        """
        status = self.zpool_status(self.name)
        state = self.zpool_health(status)
        if not self.has_it():
            return False
        if state == "ONLINE":
            return True
        elif state == "SUSPENDED":
            self.status_log(state.lower())
            return True
        elif state == "DEGRADED":
            self.status_log(state.lower())
            return True
        return False

    @lazy
    def zpool_cache(self):
        return os.path.join(rcEnv.paths.pathvar, 'zpool.cache')

    def import_pool_no_cachefile(self):
        cmd = ['zpool', 'import', '-f', '-o', 'cachefile='+self.zpool_cache,
               self.name]
        out, err, ret = justcall(cmd)
        return cmd, ret, out, err

    def import_pool_cachefile(self):
        devzp = os.path.join(self.var_d, 'dev', 'dsk')
        if not os.path.isdir(devzp):
            return [], 1, "", ""
        cmd = ['zpool', 'import', '-f', '-o', 'cachefile='+self.zpool_cache,
               '-d', devzp, self.name]
        out, err, ret = justcall(cmd)
        return cmd, ret, out, err

    def import_pool(self, verbose=True, retries=10):
        """
        Import the pool.
        1/ try using a dev list cache, which is fastest
        2/ fallback without dev list cache

        Parallel import can fail on Solaris 11.4, with a "no such
        pool available" error. Retry in this case, if we confirm the
        pool exists.
        """
        for i in range(retries):
            cmd, ret, out, err = self._import_pool()
            if "no such pool available" in err:
                self.log.info("retry import %d/%d, pool '%s' reported unavailable", i+1, retries, self.name)
                time.sleep(2)
                continue
            break
        if verbose:
            self.log.info("%s", " ".join(cmd))
            if out:
                self.log.info("%s", out)
            if err:
                if ret:
                    self.log.error("%s [retcode %d]", err, ret)
                else:
                    self.log.warning("%s [retcode %d]", err, ret)
        if ret == 0:
            self.can_rollback = True
        return ret

    def _import_pool(self, verbose=True):
        cmd, ret, out, err = self.import_pool_cachefile()
        if ret == 0:
            return cmd, ret, out, err
        if verbose:
            self.log.info("import fallback without dev cache")
        cmd, ret, out, err = self.import_pool_no_cachefile()
        return cmd, ret, out, err

    def do_start(self):
        if self.is_up():
            self.log.info("%s is already up" % self.name)
            return 0
        self.zgenhostid()
        ret = self.import_pool()
        if ret != 0:
            raise ex.excError("failed to import pool")
        self.can_rollback = True
        self.set_multihost()
        clear_cache("zpool.status." + self.name)

    def do_stop(self):
        if not self.is_up():
            self.log.info("%s is already down" % self.name)
            return 0
        cmd = ["zpool", "export", self.name]
        ret, out, err = self.vcall(cmd, err_to_warn=True)
        if ret != 0:
            cmd = ["zpool", "export", "-f", self.name]
            ret, out, err = self.vcall(cmd)
        clear_cache("zpool.status." + self.name)
        if ret != 0:
            raise ex.excError

    def sub_devs(self):
        if self.is_up():
            self.log.debug("resource up ... update sub devs cache")
            dl = self.update_sub_devs_cache()
            return dl
        if not os.path.exists(self.sub_devs_name):
            self.log.debug("no sub devs cache file and service not up ... unable to evaluate sub devs")
            return set()
        dl = self.load_sub_devs_cache()
        dl = self.remap_cached_sub_devs_controller(dl)
        return dl

    def update_sub_devs_cache(self):
        if not self.has_it():
            return set()
        dl = self._sub_devs()
        self.write_sub_devs_cache(dl)
        return dl

    def write_sub_devs_cache(self, dl):
        with cmlock(timeout=5, delay=1, lockfile=self.sub_devs_name+".lock", intent="write"):
            with open(self.sub_devs_name, 'w') as f:
                f.write(json.dumps(list(dl)))

    def load_sub_devs_cache(self):
        with cmlock(timeout=5, delay=1, lockfile=self.sub_devs_name+".lock", intent="load"):
            with open(self.sub_devs_name, 'r') as f:
                try:
                    return set(json.load(f))
                except:
                    raise ex.excError("corrupted sub devs cache file %s"%self.sub_devs_name)

    def remap_cached_sub_devs_controller(self, dl):
        if rcEnv.sysname != "SunOS":
            return dl
        mapping = self.get_wwn_map()
        vdl = []
        for d in dl:
            if os.path.exists(d):
                vdl.append(d)
                continue
            if len(d) < 36 or not d.endswith("s2"):
                self.log.debug("no remapping possible for disk %s. keep as is." % d)
                vdl.append(d)
                continue
            wwid = d[-36:-2]
            if len(wwid) in (18, 26, 34) and wwid.endswith("d0"):
                wwid = wwid[:-2]
                d0 = "d0"
            else:
                d0 = ""
            l = glob.glob("/dev/rdsk/*"+wwid+d0+"s2")
            if len(l) != 1:
                # may be the disk is a R2 mirror member, with a different wwn than R1
                if wwid in mapping:
                    wwid = mapping[wwid]
                    l = glob.glob("/dev/rdsk/*"+wwid+d0+"s2")
            if len(l) != 1:
                self.log.warning("discard disk %s from sub devs cache: "
                                 "not found", wwid)
                continue
            self.log.debug("remapped device %s to %s" % (d, l[0]))
            vdl.append(l[0])
        return set(vdl)

    def get_wwn_map(self):
        mapping = {}
        wwn_maps = glob.glob(os.path.join(self.svc.var_d, "*", "wwn_map"))
        for fpath in wwn_maps:
            try:
                with open(fpath, "r") as filep:
                    _mapping = json.load(filep)
            except ValueError:
                pass
            else:
                for (r1, r2) in _mapping:
                    mapping[r1] = r2
                    mapping[r2] = r1
        return mapping

    def _sub_devs(self):
        """
        Search zpool vdevs from the output of "zpool status poolname" if
        imported.
        """
        return set(zpool_devs(self.name, self.svc.node))

    def zgenhostid(self):
        if self.multihost and not os.path.exists("/etc/hostid"):
            try:
                justcall(["zgenhostid"])
            except Exception:
                self.log.warning("/etc/hostid does not exist and zgenhostid is not installed")

    def set_multihost(self):
        if self.multihost is None:
            # don't care
            return
        current = zpool_getprop(self.name, "multihost")
        if current == "":
            # not multihost capable (pre-0.7) or not imported
            return
        ret = 0
        if self.multihost is True:
            if current == "off":
                ret = zpool_setprop(self.name, "multihost", "on", log=self.log)
            else:
                self.log.info("multihost is already on")
        elif self.multihost is False:
            if current == "on":
                ret = zpool_setprop(self.name, "multihost", "off", log=self.log)
            else:
                self.log.info("multihost is already off")
        if ret != 0:
            raise ex.excError

