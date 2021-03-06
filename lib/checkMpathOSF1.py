import checks
from rcUtilities import justcall
from rcDiskInfoOSF1 import diskInfo

class check(checks.check):
    chk_type = "mpath"
    svcdevs = {}

    def find_svc(self, dev):
        devpath = '/dev/rdisk/'+dev
        for svc in self.svcs:
            if svc not in self.svcdevs:
                try:
                    devs = svc.sub_devs()
                except Exception as e:
                    devs = []
                self.svcdevs[svc] = devs
            if dev in self.svcdevs[svc]:
                return svc.path
        return ''

    def do_check(self):
        di = diskInfo()
        r = []
        for dev, data in di.h.items():
            r.append({"instance": data['wwid'],
                      "value": str(data['path_count']),
                      "path": self.find_svc(dev),
                     })
        return r
