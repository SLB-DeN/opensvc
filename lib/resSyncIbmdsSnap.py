import os
import logging

from rcGlobalEnv import rcEnv
from rcUtilities import which
from converters import print_duration
import rcExceptions as ex
import rcStatus
import time
import datetime
import resSync
import rcIbmDs

class syncIbmdsSnap(resSync.Sync):
    def resyncflash(self):
        if self.array is None:
            self.array = rcIbmDs.IbmDss(node=self.svc.node).get(self.arrayname)
        data = self.lsflash()
        ese_pairs = []
        other_pairs = []

        for d in data:
            if d['isTgtSE'] == 'ESE':
                ese_pairs.append(d['ID'])
            else:
                other_pairs.append(d['ID'])

        present_pairs = set(map(lambda x: x['ID'], data))
        missing_pairs = list(set(self.pairs) - present_pairs)
        if len(missing_pairs) > 0:
            missing_pairs.sort()
            raise ex.excError("refuse to resync as %s pairs are not currently configured"%', '.join(missing_pairs))

        self._resyncflash(ese_pairs, '-tgtse')
        self._resyncflash(other_pairs)

    def _resyncflash(self, pairs, options=None):
        if len(pairs) == 0:
            return
        if self.recording:
            self._resyncflash_recording(pairs, options=options)
        else:
            self._resyncflash_norecording(pairs, options=options)

    def _resyncflash_norecording(self, pairs, options=None):
        s = 'rmflash -dev %s -quiet' % self.arrayname
        l = [s]
        l.append(' '.join(pairs))
        cmd = ' '.join(l)
        out, err = self.array.dscli(cmd, log=self.log)
        if len(err) > 0:
            raise ex.excError(err)
        s = 'mkflash -dev %s -persist' % self.arrayname
        if self.bgcopy:
            s += ' -cp'
        else:
            s += ' -nocp'
        l = [s]
        if options is not None:
            l.append(options)
        l.append(' '.join(pairs))
        cmd = ' '.join(l)
        out, err = self.array.dscli(cmd, log=self.log)
        if len(err) > 0:
            raise ex.excError(err)

    def _resyncflash_recording(self, pairs, options=None):
        s = 'resyncflash -dev %s -persist -record' % self.arrayname
        if self.bgcopy:
            s += ' -cp'
        else:
            s += ' -nocp'
        l = [s]
        if options is not None:
            l.append(options)
        l.append(' '.join(pairs))
        cmd = ' '.join(l)
        out, err = self.array.dscli(cmd, log=self.log)
        if len(err) > 0:
            raise ex.excError(err)

    def can_sync(self, target=None):
        self.get_last()
        if self.skip_sync(self.last):
            return False
        return True

    def get_last(self, data=None):
        if data is None:
            data = self.lsflash()
        if len(data) == 0:
            return
        lastsync = datetime.datetime.now()
        for _data in data:
            _lastsync = _data['DateSynced']
            try:
                _lastsync = datetime.datetime.strptime(_lastsync, "%a %b %d %H:%M:%S %Z %Y")
            except ValueError:
                # workaround hp-ux python 2.6
                _lastsync = _lastsync.replace("CET", "MET")
                _lastsync = datetime.datetime.strptime(_lastsync, "%a %b %d %H:%M:%S %Z %Y")

            if _lastsync < lastsync:
                lastsync = _lastsync
        self.last = lastsync

    def _status(self, verbose=False):
        try:
            data = self.lsflash()
            self.get_last(data)
        except ex.excError as e:
            self.status_log(str(e))
            return rcStatus.WARN
        r = rcStatus.UP

        record_disabled = []
        persist_disabled = []
        record_enabled = []
        state_invalid = []

        for _data in data:
            if _data['Recording'] == "Disabled":
                record_disabled.append(_data['ID'])
            elif _data['Recording'] == "Enabled":
                record_enabled.append(_data['ID'])
            if _data['State'] != "Valid":
                state_invalid.append(_data['ID'])
            if _data['Persistent'] == "Disabled":
                persist_disabled.append(_data['ID'])

        if self.recording and len(record_disabled) > 0:
            self.status_log("Recording disabled on %s"%','.join(record_disabled))
            r = rcStatus.WARN
        elif not self.recording and len(record_enabled) > 0:
            self.status_log("Recording enabled on %s"%','.join(record_enabled))
            r = rcStatus.WARN
        if len(state_invalid) > 0:
            self.status_log("State not valid on %s"%','.join(state_invalid))
            r = rcStatus.WARN
        if len(persist_disabled) > 0:
            self.status_log("Persistent disabled on %s"%','.join(persist_disabled))
            r = rcStatus.WARN

        pairs = []
        for d in data:
            if 'ID' not in d:
                continue
            pairs.append(d['ID'])
        missing = set(self.pairs) - set(pairs)
        missing = sorted(list(missing))
        if len(missing) > 0:
            self.status_log("Missing flashcopy on %s"%','.join(missing))
            r = rcStatus.WARN

        if self.last is None:
            return rcStatus.WARN
        elif self.last < datetime.datetime.now() - datetime.timedelta(seconds=self.sync_max_delay):
            self.status_log("Last sync on %s older than %s"%(self.last, print_duration(self.sync_max_delay)))
            return rcStatus.WARN
        elif r == rcStatus.WARN:
            return rcStatus.WARN
        self.status_log("Last sync on %s"%self.last)
        return rcStatus.UP

    def sync_break(self):
        pass

    def sync_resync(self):
        self.resyncflash()

    @resSync.notify
    def sync_update(self):
        self.resyncflash()

    def start(self):
        pass

    def __init__(self,
                 rid=None,
                 pairs=[],
                 array=None,
                 bgcopy=True,
                 recording=True,
                 **kwargs):
        resSync.Sync.__init__(self,
                              rid=rid,
                              type="sync.ibmdssnap",
                              **kwargs)

        self.label = "flash copy %s"%','.join(pairs)
        self.pairs = pairs
        self.arrayname = array
        self.recording = recording
        self.bgcopy = bgcopy
        self.array = None
        self.last = None
        self.params = "setenv -banner off -header on -format delim\n"
        self.default_schedule = "@0"

    def lsflash(self):
        if self.array is None:
            self.array = rcIbmDs.IbmDss(node=self.svc.node).get(self.arrayname)
        out, err = self.array.dscli(self.params+'lsflash -l -dev %s ' % self.arrayname + ' '.join(self.pairs))
        if 'No Flash Copy found' in out:
            return []
        data = self.parseblock(0, out)
        return data

    def getblock(self, n, s):
        lines = s.replace('dscli> ', '').split('\n')
        begin = None
        end = None
        met = 0
        for i, line in enumerate(lines):
             if line.startswith("==="):
                 met += 1
                 if met < n:
                     continue
                 if begin is None:
                     begin = i-1
                 else:
                     end = i-1
                     break
        if end is None:
           end = i
        return lines[begin:end]

    def parseblock(self, n, out):
        data = []
        lines = self.getblock(n, out)
        if len(lines) < 3:
            return
        headers = lines[0].split(',')
        headers_multipliers = []
        for i, h in enumerate(headers):
            if '^' not in h:
                headers_multipliers.append(None)
                continue
            x = h[h.index('^')+1:h.index('B)')]
            x = int(x)
            headers_multipliers.append((2**x)/1024/1024)
            stripped_header = headers[i][:headers[i].index(' (')]
            while stripped_header in headers:
                stripped_header += "_"
            headers[i] = stripped_header
        for line in lines[2:]:
            d = {}
            l = line.split(',')
            for i, key in enumerate(headers):
                if i >= len(l):
                    raise ex.excError("the command dataset does not match its advertized columning")
                key = key.strip()
                if headers_multipliers[i] is not None:
                    try:
                        d[key] = int(float(l[i]) * headers_multipliers[i])
                    except:
                        d[key] = l[i]
                else:
                    d[key] = l[i]
            data.append(d)
        return data

    def __str__(self):
        return "%s pairs=%s" % (resSync.Sync.__str__(self), ','.join(self.pairs))

