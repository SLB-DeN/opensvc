""" Module providing device path remapping for libvirt VMs
"""

import resources as Res
import rcStatus
import rcExceptions as ex
from rcGlobalEnv import rcEnv

class Disk(Res.Resource):

    def __init__(self,
                 rid=None,
                 name=None,
                 devpath={},
                 **kwargs):
        Res.Resource.__init__(self,
                              rid,
                              "disk.vdisk",
                              **kwargs)
        self.label = "vdisk "+name
        self.name = name
        self.devpath = devpath

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

    def sub_devs(self):
        return self.devpath.keys()

    def remap(self):
        path = self.devpath[rcEnv.nodename]
        paths = set(self.devpath.values()) - set(self.devpath[rcEnv.nodename])
        from xml.etree.ElementTree import ElementTree, SubElement
        tree = ElementTree()
        try:
            tree.parse(self.svc.resources_by_id['container'].cf)
        except:
            self.log.error("failed to parse %s"%self.svc.resources_by_id['container'].cf)
            raise ex.excError
        for dev in tree.getiterator('disk'):
            s = dev.find('source')
            if s is None:
                 continue
            il = s.items()
            if len(il) != 1:
                 continue
            attr, devp = il[0]
            if devp in paths:
                self.log.info("remapping device path: %s -> %s"%(devp,path))
                s.set('dev', path)
                #SubElement(dev, "source", {'dev': path})
                tree.write(self.svc.resources_by_id['container'].cf)

    def stop(self):
        pass

    def start(self):
        self.remap()

    def _status(self, verbose=False):
        return rcStatus.NA

