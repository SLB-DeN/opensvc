import os
import logging

from rcGlobalEnv import rcEnv
from rcUtilities import which
import rcExceptions as ex
import rcStatus
import time
import datetime
import xml.etree.ElementTree as ET
import subprocess
import resSync

class syncEvasnap(resSync.Sync):
    def wait_for_devs_ready(self):
        pass

    def can_sync(self, target=None):
        ts = None

        """ get oldest snap
        """
        for pair in self.pairs:
            info = self.lun_info(pair['dst'])
            if info is None:
                self.log.debug("snap %s missing"%pair['dst'])
                return True
            _ts = info['creationdatetime']
            if ts is None or _ts < ts:
                ts = _ts
        return not self.skip_sync(ts)

    def recreate(self):
        def snapname(info):
            return info['objectname'].split('\\')[-2]+'_'+self.snap_name

        try:
            self.prereq()
        except ex.excError as e:
            self.log.error(str(e))
            raise ex.excError

        status = self._status(skip_prereq=True)

        if not self.can_sync():
            return

        if not self.svc.options.force and status == rcStatus.UP:
            self.log.info("snapshots are already fresh. use --force to bypass")
            return

        cmd = []
        for pair in self.pairs:
            if pair['dst'] in self._lun_info:
                info = self._lun_info[pair['dst']]
                for mask in pair['mask']:
                    lunid = int(mask.split('\\')[-1])
                    hostpath = '\\'.join(mask.split('\\')[:-1])
                    if hostpath in info['mask'] and info['mask'][hostpath] == lunid:
                        cmd += ['delete lun "%s"'%mask]
                cmd += ['delete vdisk "%s" wait_for_completion'%info['objectname']]
        self.sssu(cmd, verbose=True)

        cmd = []
        for pair in self.pairs:
            info = self.lun_info(pair['src'])
            if 'allocation_policy' in pair:
                policy = str(pair['allocation_policy']).lower()
            else:
                policy = 'demand'
            if policy not in ['demand', 'fully']:
                policy = 'demand'
            if 'vraid' in pair and pair['vraid'] in ['vraid6', 'vraid5', 'vraid0', 'vraid1']:
                force_vraid = "redundancy=%s"%pair['vraid']
            else:
                force_vraid = ""
            cmd += ['add snapshot %s vdisk="%s" allocation_policy=%s world_wide_lun_name=%s %s'%(snapname(info), info['objectname'], policy, self.convert_wwid(pair['dst']), force_vraid)]
        self.sssu(cmd, verbose=True)

        cmd = []
        for pair in self.pairs:
            info = self.lun_info(pair['dst'])
            for mask in pair['mask']:
                lunid = mask.split('\\')[-1]
                hostpath = '\\'.join(mask.split('\\')[:-1])
                cmd += ['add lun %s host="%s" vdisk="%s"'%(lunid, hostpath, snapname(info))]
        self.sssu(cmd, verbose=True)

    def sssu(self, cmd=[], verbose=False, check=True):
        os.chdir(rcEnv.paths.pathtmp)
        cmd = [self.sssubin,
               "select manager %s username=%s password=%s"%(self.manager, self.username, self.password),
               "select system %s"%self.eva_name] + cmd
        if verbose:
            import re
            from copy import copy
            _cmd = copy(cmd)
            _cmd[1] = re.sub(r'password=.*', 'password=xxxxx', _cmd[1])
            self.log.info(subprocess.list2cmdline(_cmd))
            ret, out, err = self.call(cmd)
            if 'Error:' in out > 0:
                self.log.error(out)
            else:
                self.log.info(out)
        else:
            ret, out, err = self.call(cmd)
        if check and "Error" in out:
            raise ex.excError("sssu command execution error")
        return ret, out, err

    def lun_info(self, wwid):
        if wwid in self._lun_info:
            return self._lun_info[wwid]

        if '-' not in wwid:
            _wwid = wwid
            wwid = self.convert_wwid(wwid)

        info = {
            'oxuid': 'unknown',
            'lunid': -1,
            'creationdatetime': datetime.datetime(year=datetime.MINYEAR, month=1, day=1),
            'mask': {}
        }
        try:
            ret, out, err = self.sssu(["find vdisk lunwwid="+wwid+" xml"])
        except:
            return None
        l = out.split('\n')
        for i, line in enumerate(l):
            if line == '<object>':
                l = l[i:]
                break
        e = ET.fromstring('\n'.join(l))

        for p in e.findall("presentations/presentation"):
            host = p.find("hostname").text
            lunid = p.find("lunnumber").text
            info['mask'][host] = int(lunid)

        e_oxuid = e.find("objectparenthexuid")
        if e_oxuid is not None:
            info['oxuid'] = e_oxuid.text.replace('-','')

        e_objectname = e.find("objectname")
        if e_objectname is not None:
            info['objectname'] = e_objectname.text

        e_creationdatetime = e.find("creationdatetime")
        if e_oxuid is not None:
            try:
                creationdatetime = datetime.datetime.strptime(e_creationdatetime.text, "%d-%b-%Y %H:%M:%S")
                info['creationdatetime'] = creationdatetime
            except:
                self.log.error("failed to parse snapshot creation datetime")
                pass

        self._lun_info[_wwid] = info

        return info

    def _status(self, verbose=False, skip_prereq=False):
        err = False
        errlog = []
        try:
            if not skip_prereq:
                self.prereq()
        except ex.excError as e:
            self.status_log(str(e))
            return rcStatus.WARN
        for pair in self.pairs:
            info_s = self.lun_info(pair['src'])
            info_d = self.lun_info(pair['dst'])
            if info_s is None:
                errlog.append("snapshot source %s does not exists"%pair['src'])
                err |= True
                continue
            if info_d is None:
                errlog.append("snapshot %s does not exists"%pair['dst'])
                err |= True
                continue
            if info_s['oxuid'].lower() != info_d['oxuid'].lower():
                errlog.append("snapshot %s exists but incorrect parent object uid: %s"%(pair['dst'], info_d['oxuid'].lower()))
                err |= True
            if info_d['creationdatetime'] < datetime.datetime.now() - datetime.timedelta(seconds=self.sync_max_delay):
                errlog.append("snapshot %s too old"%pair['dst'])
                err |= True
            for mask in pair['mask']:
                hostpath = '\\'.join(mask.split('\\')[:-1])
                hostname = mask.split('\\')[-2]
                dstlunid = mask.split('\\')[-1]
                if hostpath not in info_d['mask']:
                    errlog.append("snapshot %s exists but not presented to host %s"%(pair['dst'], hostname))
                    err |= True
                    continue
                try:
                    dstlunid = int(dstlunid)
                    if info_d['mask'][hostpath] != dstlunid:
                        errlog.append("snapshot %s exists but incorrect lunid for host %s"%(pair['dst'], hostname))
                        err |= True
                except ValueError:
                    pass
        if err:
            self.status_log('. '.join(errlog))
            return rcStatus.WARN
        return rcStatus.UP

    def sync_resync(self):
        self.recreate()

    @resSync.notify
    def sync_update(self):
        self.recreate()

    def refresh_svcstatus(self):
        self.svcstatus = self.svc.group_status(excluded_groups=set(["app", "sync", "task", "disk.scsireserv"]))

    def get_svcstatus(self):
        if len(self.svcstatus) == 0:
            self.refresh_svcstatus()

    def convert_wwid(self, wwid):
        s = ""
        for i, c in enumerate(wwid):
            s += c
            if (i+1) % 4 == 0: s += '-'
        return s.strip('-')

    def prereq(self):
        s = "array#" + self.eva_name
        try:
            self.svc.node.oget(s, "type")
        except ex.RequiredOptNotFound:
            raise ex.excError("no credentials for array %s in node or cluster configuration" % self.eva_name)
        try:
            self.manager = self.svc.node.oget(s, "manager")
        except ex.RequiredOptNotFound:
            raise ex.excError("no manager set for array %s in node or cluster configuration" % self.eva_name)
        try:
            self.username = self.svc.node.oget(s, "username")
        except ex.RequiredOptNotFound:
            raise ex.excError("no username set for array %s in node or cluster configuration" % self.eva_name)
        try:
            self.password = self.svc.node.oget(s, "password")
        except ex.RequiredOptNotFound:
            raise ex.excError("no password set for array %s in node or cluster configuration" % self.eva_name)
        self.sssubin = self.svc.node.oget(s, "bin")
        if not self.sssubin:
            self.sssubin = which(self.sssubin)
        if not self.sssubin:
            raise ex.excError("missing sssu binary")

        for pair in self.pairs:
            if 'src' not in pair or 'dst' not in pair or 'mask' not in pair:
                raise ex.excError("missing parameter in pair %s"%str(pair))
        ret, out, err = self.sssu(check=False)
        if "Error opening https connection" in out:
            raise ex.excError("error login to %s"%self.manager)
        elif "Error" in out:
            raise ex.excError("eva %s is not managed by %s"%(self.eva_name, self.manager))

    def __init__(self,
                 rid=None,
                 pairs=[],
                 eva_name="",
                 snap_name="",
                 **kwargs):
        resSync.Sync.__init__(self,
                              rid=rid, type="sync.evasnap",
                              **kwargs)

        self.label = "EVA snapshot %s"%(rid)
        self.eva_name = eva_name
        self.snap_name = snap_name
        self.pairs = pairs
        self._lun_info = {}
        self.default_schedule = "@0"

    def __str__(self):
        return "%s eva_name=%s pairs=%s" % (resSync.Sync.__str__(self),\
                self.eva_name, str(self.pairs))

