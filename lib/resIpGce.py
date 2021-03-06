import resIp
import os
import rcStatus
from rcGlobalEnv import rcEnv
import rcExceptions as ex
from rcUtilities import getaddr, justcall
import json
import rcGce

rcIfconfig = __import__('rcIfconfig'+rcEnv.sysname)

class Ip(resIp.Ip, rcGce.GceMixin):
    def __init__(self,
                 rid=None,
                 ipname=None,
                 ipdev=None,
                 routename=None,
                 gce_zone=None,
                 **kwargs):
        resIp.Ip.__init__(self,
                          rid=rid,
                          ipname=ipname,
                          ipdev=ipdev,
                          **kwargs)
        self.label = "gce ip %s@%s" % (ipname, ipdev)
        self.routename = routename
        self.gce_zone = gce_zone

        # cache for route data
        self.gce_route_data = None

    def start_local_route(self):
        if self.has_local_route():
            self.log.info("ip route %s/32 dev %s is already installed" % (self.addr, self.ipdev))
            return
        self.add_local_route()

    def stop_local_route(self):
        if not self.has_local_route():
            self.log.info("ip route %s/32 dev %s is already uninstalled" % (self.addr, self.ipdev))
            return
        self.del_local_route()

    def add_local_route(self):
        cmd = ["ip", "route", "replace", self.addr+"/32", "dev", self.ipdev]
        self.vcall(cmd)

    def del_local_route(self):
        cmd = ["ip", "route", "del", self.addr+"/32", "dev", self.ipdev]
        self.vcall(cmd)

    def has_local_route(self):
        cmd = ["ip", "route", "list", self.addr+"/32", "dev", self.ipdev]
        out, err, ret = justcall(cmd)
        if ret != 0:
            return False
        if out == "":
            return False
        return True

    def start_gce_route(self):
        if not self.routename:
            return
        if self.has_gce_route():
            self.log.info("gce route %s, %s to instance %s is already installed" % (self.routename, self.addr, rcEnv.nodename))
            return
        if self.exist_gce_route():
            self.del_gce_route()
        self.add_gce_route()
        self.svc.gce_routes_cache[self.routename] = {
          "destRange": self.addr+"/32",
          "nextHopInstance": rcEnv.nodename,
        }

    def stop_gce_route(self):
        if not self.routename:
            return
        if not self.has_gce_route():
            self.log.info("gce route %s, %s to instance %s is already uninstalled" % (self.routename, self.addr, rcEnv.nodename))
            return
        self.del_gce_route()
        self.get_gce_routes_list(refresh=True)
        del(self.svc.gce_routes_cache[self.routename])

    def add_gce_route(self):
        cmd = ["gcloud", "compute", "routes", "-q", "create", self.routename,
               "--destination-range", self.addr+"/32",
               "--next-hop-instance", rcEnv.nodename,
               "--next-hop-instance-zone", self.gce_zone]
        self.vcall(cmd)

    def del_gce_route(self):
        cmd = ["gcloud", "compute", "routes", "-q", "delete", self.routename]
        self.vcall(cmd)

    def get_gce_route_data(self, refresh=False):
        data = self.get_gce_routes_list(refresh=refresh)
        if data is None:
            return
        if not self.routename in data:
            return
        return data[self.routename]

    def get_gce_routes_list(self, refresh=False):
        if not refresh and hasattr(self.svc, "gce_routes_cache"):
            return self.svc.gce_routes_cache
        self.svc.gce_routes_cache = self._get_gce_routes_list()
        return self.svc.gce_routes_cache

    def _get_gce_routes_list(self):
        if not self.routename:
            return
        routenames = " ".join([r.routename for r in self.svc.get_resources("ip") if hasattr(r, "routename")])
        self.wait_gce_auth()
        cmd = ["gcloud", "compute", "routes", "list", "--format", "json", routenames]
        out, err, ret = justcall(cmd)
        if ret != 0:
            raise ex.excError("gcloud route describe returned with error: %s, %s" % (out, err))
        try:
            data = json.loads(out)
        except:
            raise ex.excError("unable to parse gce route data: %s" % out)
        h = {}
        for route in data:
            h[route["name"]] = route
        return h

    def exist_gce_route(self):
        if not self.routename:
            return True
        data = self.get_gce_route_data()
        if not data:
            return False
        if data:
            return True
        return False

    def has_gce_route(self):
        if not self.routename:
            return True
        data = self.get_gce_route_data()
        if not data:
            return False
        if data.get("destRange") != self.addr+"/32":
            return False
        if data.get("nextHopInstance").split("/")[-1] != rcEnv.nodename:
            return False
        return True

    def is_up(self):
        """Returns True if ip is associated with this node
        """
        self.getaddr()
        if not self.has_local_route():
            return False
        if not self.has_gce_route():
            return False
        return True

    def _status(self, verbose=False):
        self.getaddr()
        try:
            local_status = self.has_local_route()
            if not local_status:
                self.status_log("local route is not installed")
        except ex.excError as e:
            self.status_log(str(e))
            local_status = False

        try:
            gce_status = self.has_gce_route()
            if not gce_status:
                self.status_log("gce route is not installed")
        except ex.excError as e:
            self.status_log(str(e))
            gce_status = False

        s = local_status & gce_status
        if s:
            return rcStatus.UP
        else:
            return rcStatus.DOWN

    def check_ping(self, count=1, timeout=5):
        pass

    def start(self):
        self.getaddr()
        self.start_local_route()
        self.start_gce_route()

    def stop(self):
        self.getaddr()
        self.stop_local_route()
        # don't unconfigure the gce route: too long. let the start replace it if necessary.

