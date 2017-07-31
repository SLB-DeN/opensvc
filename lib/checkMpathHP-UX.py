import checks
from rcUtilities import justcall

class check(checks.check):
    chk_type = "mpath"
    svcdevs = {}

    def find_svc(self, dev):
        for svc in self.svcs:
            if svc not in self.svcdevs:
                try:
                    devs = svc.sub_devs()
                except Exception as e:
                    devs = []
                self.svcdevs[svc] = devs
            if dev in self.svcdevs[svc]:
                return svc.svcname
        return ''

    def do_check(self):
        cmd = ['scsimgr', 'lun_map']
        out, err, ret = justcall(cmd)
        if ret != 0:
            return self.undef
        lines = out.split('\n')
        if len(lines) < 1:
            return self.undef
        r = []
        dev = None
        wwid = None
        for line in lines:
            if "LUN PATH INFORMATION FOR LUN" in line:
                # new mpath
                # - store previous
                # - reset path counter
                if dev is not None and not dev.startswith('/dev/pt/pt') and wwid != '=' and "Virtual" not in proto:
                    r.append({'chk_instance': wwid,
                              'chk_value': str(n),
                              'chk_svcname': self.find_svc(dev),
                             })
                n = 0
                l = line.split()
                if len(l) < 2:
                    continue
                dev = l[-1]
            elif line.startswith("World Wide Identifier"):
                wwid = line.split()[-1].replace("0x","")
            elif line.startswith("SCSI transport protocol"):
                proto = line.split("=")[-1]
            elif line.startswith("State"):
                state = line.split("=")[-1].strip()
            elif line.startswith("Last Open or Close state"):
                last_known_state = line.split("=")[-1].strip()
                if state in ("ACTIVE", "STANDBY"):
                    n += 1
                elif state == "UNOPEN" and last_known_state in ("ACTIVE", "STANDBY"):
                    n += 1
        if dev is not None and not dev.startswith('/dev/pt/pt') and wwid != '=' and "Virtual" not in proto:
            r.append({'chk_instance': wwid,
                      'chk_value': str(n),
                      'chk_svcname': self.find_svc(dev),
            })
        return r
