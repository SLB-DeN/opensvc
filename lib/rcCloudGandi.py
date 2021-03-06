import rcCloud
import rcExceptions as ex
import socket

try:
    from libcloud.compute.types import Provider
    from libcloud.compute.providers import get_driver
    import libcloud.security
except ImportError:
    raise ex.excInitError("apache-libcloud module must be installed")

class Cloud(rcCloud.Cloud):
    mode = 'gandi'

    def __init__(self, s, auth):
        rcCloud.Cloud.__init__(self, s, auth)
        if 'key' not in auth:
            raise ex.excInitError("option 'key' is mandatory in gandi section")
        gandi = get_driver(Provider.GANDI)
        try:
            self.driver = gandi(auth['key'])
        except Exception as e:
            raise ex.excInitError("error login to gandi cloud %s: %s"%(s, str(e)))

    def app_id(self):
        return ''

    def cloud_id(self):
        return self.mode

    def app_cloud_id(self):
        return self.mode

    def list_names(self):
        l = []
        _id = self.app_cloud_id()
        try:
            vapps = self.driver.list_nodes()
        except socket.error as e:
            raise ex.excError("error connecting to %s cloud manager" % self.cid)
        for vapp in vapps:
            __id = '.'.join((vapp.name, _id))
            for vm in vapp.extra['vms']:
                name = '.'.join((vm['name'], __id))
                l.append((vm['name'], name))
        return l

