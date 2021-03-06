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
    mode = 'amazon'

    def __init__(self, s, auth):
        rcCloud.Cloud.__init__(self, s, auth)
        if 'access_key_id' not in auth:
            raise ex.excInitError("option 'access_key_id' is mandatory in amazon section")
        if 'provider' not in auth:
            raise ex.excInitError("option 'provider' is mandatory in amazon section")
        if 'secret_key' not in auth:
            raise ex.excInitError("option 'secret_key' is mandatory in amazon section")
        o = get_driver(auth['provider'])
        self.driver = o(auth['access_key_id'], auth['secret_key'])
        if 'proxy' in auth:
            self.driver.connection.set_http_proxy(proxy_url=auth['proxy'])

    def app_id(self, name):
        return name.rstrip(self.auth['manager']).split('.')[-2]

    def cloud_id(self):
        return self.auth['provider']

    def app_cloud_id(self):
        return self.cloud_id()

    def list_names(self):
        l = []
        return l

