import os

from rcGlobalEnv import rcEnv
import rcExceptions as ex
import rcStatus
import time
import datetime
import resSync
import rcBtrfs
from rcUtilities import justcall

class syncBtrfsSnap(resSync.Sync):
    def __init__(self,
                 rid=None,
                 name=None,
                 subvol=[],
                 keep=1,
                 **kwargs):
        resSync.Sync.__init__(self,
                              rid=rid,
                              type="sync.btrfssnap",
                              **kwargs)

        if name:
            self.label = "btrfs '%s' snapshot %s" % (name, ", ".join(subvol))
        else:
            self.label = "btrfs snapshot %s" % ", ".join(subvol)
        self.subvol = subvol
        self.keep = keep
        self.name = name
        self.btrfs = {}

    def on_add(self):
        pass

    def test_btrfs(self, label):
        cmd = [rcEnv.syspaths.blkid, "-L", label]
        out, err, ret = justcall(cmd)
        if ret != 0:
            return ret
        dev = out.strip()
        dev = os.path.realpath(dev)
        import glob
        holders = glob.glob("/sys/block/%s/holders/*" % os.path.basename(dev))
        if len(holders) == 1:
            hdev = "/dev/%s" % os.path.basename(holders[0])
        else:
            hdev = None
        if hdev and os.path.exists(hdev):
            dev = hdev
        cmd = ["btrfs", "fi", "show", dev]
        out, err, ret = justcall(cmd)
        return ret

    def stop(self):
        for s in self.subvol:
            try:
                label, subvol = s.split(":")
            except:
                self.log.error("misformatted subvol entry %s (expected <label>:<subvol>)" % s)
                continue
            btrfs = self.get_btrfs(label)
            cmd = ["umount", btrfs.rootdir]
            self.vcall(cmd)

    def get_btrfs(self, label):
        if label in self.btrfs:
            return self.btrfs[label]
        try:
            self.btrfs[label] = rcBtrfs.Btrfs(label=label, resource=self)
        except rcBtrfs.ExecError as e:
            raise ex.excError(str(e))
        return self.btrfs[label]

    def create_snap(self, label, subvol):
        btrfs = self.get_btrfs(label)
        orig = os.path.join(btrfs.rootdir, subvol)
        snap = os.path.join(btrfs.rootdir, subvol)
        if self.name:
            suffix = "."+self.name
        else:
            suffix = ""
        suffix += ".snap.%Y-%m-%d.%H:%M:%S"
        snap += datetime.datetime.now().strftime(suffix)
        try:
            btrfs.snapshot(orig, snap, readonly=True, recursive=False)
        except rcBtrfs.ExistError:
            raise ex.excError('%s should not exist'%snap)
        except rcBtrfs.ExecError:
            raise ex.excError

    def remove_snap(self, label, subvol):
        btrfs = self.get_btrfs(label)
        btrfs.get_subvols(refresh=True)
        snaps = {}
        for sv in btrfs.subvols.values():
            if not sv["path"].startswith(subvol):
                continue
            s = sv["path"].replace(subvol, "")
            l = s.split('.')
            if len(l) < 2:
                continue
            if l[1] not in ("snap", self.name):
                continue
            try:
                ds = sv["path"].split(".snap.")[-1]
                d = datetime.datetime.strptime(ds, "%Y-%m-%d.%H:%M:%S")
                snaps[ds] = sv["path"]
            except Exception as e:
                pass
        if len(snaps) <= self.keep:
            return
        sorted_snaps = []
        for ds in sorted(snaps.keys(), reverse=True):
            sorted_snaps.append(snaps[ds])
        for path in sorted_snaps[self.keep:]:
            try:
                btrfs.subvol_delete(os.path.join(btrfs.rootdir, path), recursive=False)
            except rcBtrfs.ExecError:
                raise ex.excError

    def _status_one(self, label, subvol):
        if self.test_btrfs(label) != 0:
            self.status_log("snap of %s suspended: not writable"%label, "info")
            return
        try:
            btrfs = self.get_btrfs(label)
        except Exception as e:
            self.status_log("%s:%s %s" % (label, subvol, str(e)))
            return
        try:
            btrfs.get_subvols()
        except:
            return
        snaps = []
        for sv in btrfs.subvols.values():
            if not sv["path"].startswith(subvol):
                continue
            s = sv["path"].replace(subvol, "")
            l = s.split('.')
            if len(l) < 2:
                continue
            if l[1] not in ("snap", self.name):
                continue
            try:
                ds = sv["path"].split(".snap.")[-1]
                d = datetime.datetime.strptime(ds, "%Y-%m-%d.%H:%M:%S")
                snaps.append(d)
            except Exception as e:
                pass
        if len(snaps) == 0:
            self.status_log("%s:%s has no snap" % (label, subvol))
            return
        if len(snaps) > self.keep:
            self.status_log("%s:%s has %d too many snaps" % (label, subvol, len(snaps)-self.keep))
        last = sorted(snaps, reverse=True)[0]
        limit = datetime.datetime.now() - datetime.timedelta(seconds=self.sync_max_delay)
        if last < limit:
            self.status_log("%s:%s last snap is too old (%s)" % (label, subvol, last.strftime("%Y-%m-%d %H:%M:%S")))

    def _status(self, verbose=False):
        for s in self.subvol:
            try:
                label, subvol = s.split(":")
            except:
                self.status_log("misformatted subvol entry %s (expected <label>:<subvol>)" % s)
                continue
            self._status_one(label, subvol)
        messages = set(self.status_logs_get(["warn"])) - set([''])
        not_writable = set([r for r in messages if "not writable" in r])
        issues = messages - not_writable

        if len(not_writable) > 0 and len(not_writable) == len(messages):
            return rcStatus.NA
        if len(issues) == 0:
            return rcStatus.UP
        return rcStatus.WARN

    def _sync_update(self, s):
        try:
            label, subvol = s.split(":")
        except:
            self.log.error("misformatted subvol entry %s (expected <label>:<subvol>)" % s)
            return
        if self.test_btrfs(label) != 0:
            self.log.info("skip snap of %s while the btrfs is no writable"%label)
            return
        self.create_snap(label, subvol)
        self.remove_snap(label, subvol)

    @resSync.notify
    def sync_update(self):
        for subvol in self.subvol:
            self._sync_update(subvol)

    def __str__(self):
        return "%s subvol=%s keep=%s" % (resSync.Sync.__str__(self), str(self.subvol), str(self.keep))

