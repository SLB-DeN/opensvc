"""
Generic ip resource driver.
"""
from __future__ import unicode_literals

import os
import re
import time

import resources as Res
import ipaddress
import lock
import rcStatus
import rcExceptions as ex
from rcGlobalEnv import rcEnv
from rcUtilities import qcall, which, getaddr, lazy, to_cidr
from converters import convert_duration, print_duration
from arp import send_arp

IFCONFIG_MOD = __import__('rcIfconfig'+rcEnv.sysname)

class Ip(Res.Resource):
    """
    Base ip resource driver.
    """

    def __init__(self,
                 rid=None,
                 ipdev=None,
                 ipname=None,
                 mask=None,
                 gateway=None,
                 type="ip",
                 expose=None,
                 check_carrier=True,
                 alias=True,
                 wait_dns=0,
                 **kwargs):
        Res.Resource.__init__(self, rid, type=type, **kwargs)
        self.ipdev = ipdev
        self.ipname = ipname
        self.mask = mask
        self.gateway = gateway
        self.lockfd = None
        self.stacked_dev = None
        self.addr = None
        self.expose = expose
        self.check_carrier = check_carrier
        self.alias = alias
        self.wait_dns = wait_dns

    def on_add(self):
        self.set_label()

    def wait_dns_records(self):
        if not self.wait_dns:
            return
        left = self.wait_dns
        timeout = time.time() + left
        self.svc.print_status_data_eval()
        self.log.info("wait address propagation to peers")
        path = ".monitor.nodes.'%s'.services.status.'%s'.resources.'%s'.info.ipaddr~[0-9]" % (rcEnv.nodename, self.svc.path, self.rid)
        try:
            result = self.svc.node._wait(path=path, duration=left)
        except KeyboardInterrupt:
            raise ex.excError("dns resolution not ready after %s (ip not in local dataset)" % print_duration(self.wait_dns))
        left = time.time() - timeout
        while left:
            result = self.svc.node.daemon_get({"action": "sync"}, timeout=left)
            if result["status"] == 0:
                break
            left = time.time() - timeout
            if left <= 0:
                raise ex.excError("dns resolution not ready after %s (cluster sync timeout)" % print_duration(self.wait_dns))

    @lazy
    def dns_name_suffix(self):
        try:
            dns_name_suffix = self.svc.conf_get(self.rid, "dns_name_suffix").strip("'\"$#")
        except ex.OptNotFound:
            dns_name_suffix = None
        return dns_name_suffix

    def set_label(self):
        """
        Set the resource label property.
        """
        try:
             self.get_mask()
        except ex.excError:
             pass
        try:
            self.getaddr()
            addr = self.addr
        except ex.excError:
            addr = self.ipname
        self.label = "%s/%s %s" % (addr, self.mask, self.ipdev)
        if self.ipname != addr:
            self.label += " " + self.ipname

    def _status_info(self):
        """
        Contribute resource key/val pairs to the resource info.
        """
        data = {}
        try:
            self.getaddr()
        except ex.excError:
            pass

        try:
            data["ipaddr"] = self.addr
        except:
            pass

        if self.ipdev:
            data["ipdev"] = self.ipdev
        if self.gateway:
            data["gateway"] = self.gateway
        if self.mask is not None:
            data["mask"] = to_cidr(self.mask)
        if self.expose:
            data["expose"] = self.expose
        return data

    def _info(self):
        """
        Contribute resource key/val pairs to the service's resinfo.
        """
        if self.ipname is None:
            return []
        try:
            self.getaddr()
        except ex.excError:
            pass
        data = [
            ["ipaddr", self.addr],
            ["ipname", self.ipname],
            ["ipdev", self.ipdev],
            ["gateway", str(self.gateway)],
        ]
        if self.mask is not None:
            data.append(["mask", str(to_cidr(self.mask))])
        return data

    def getaddr(self, cache_fallback=False):
        """
        Try resolving the ipname into an ip address. If the resolving fails and
        <cache_fallback> is True, use the last successful resolution result.
        """
        if self.ipname is None:
            raise ex.excError("ip address is not allocated yet")
        if self.addr is not None:
            return
        try:
            self.log.debug("resolving %s", self.ipname)
            self.addr = getaddr(self.ipname, cache_fallback=cache_fallback, log=self.log)
        except Exception as exc:
            if not self.is_disabled():
                raise ex.excError("could not resolve name %s: %s" % (self.ipname, str(exc)))

    def __str__(self):
        return "%s ipdev=%s ipname=%s" % (Res.Resource.__str__(self),\
                                         self.ipdev, self.ipname)
    def setup_environ(self):
        """
        Set the main resource properties as environment variables, so they
        are available to triggers.
        """
        os.environ['OPENSVC_IPDEV'] = str(self.ipdev)
        os.environ['OPENSVC_IPNAME'] = str(self.ipname)
        os.environ['OPENSVC_MASK'] = str(self.mask)
        try:
            self.getaddr()
            os.environ['OPENSVC_IPADDR'] = str(self.addr)
        except:
            pass
        elements = self.rid.split('#')
        if len(elements) == 2:
            index = elements[1]
        else:
            index = ''
        var = 'OPENSVC_IP'+index
        vals = []
        for prop in ['ipname', 'ipdev', 'addr', 'mask']:
            if getattr(self, prop) is not None:
                vals.append(str(getattr(self, prop)))
            else:
                vals.append('unknown')
        val = ' '.join(vals)
        os.environ[var] = val

    def _status(self, verbose=False):
        """
        Evaluate the ip resource status.
        """
        try:
            self.getaddr()
        except Exception as exc:
            self.status_log(str(exc))
            if "not allocated" in str(exc):
                return rcStatus.DOWN
            else:
                return rcStatus.WARN
        ifconfig = IFCONFIG_MOD.ifconfig()
        intf = ifconfig.interface(self.ipdev)
        mode = getattr(self, "mode") if hasattr(self, "mode") else None
        if intf is None and "dedicated" not in self.tags and mode != "dedicated":
            self.status_log("interface %s not found" % self.ipdev)
            return rcStatus.DOWN
        try:
            if self.is_up() and self.has_carrier(intf) is not False:
                return rcStatus.UP
            else:
                return rcStatus.DOWN
        except ex.excNotSupported:
            self.status_log("not supported", "info")
            return rcStatus.NA
        except ex.excError as exc:
            self.status_log(str(exc), "error")
            return rcStatus.WARN

    def arp_announce(self):
        """
        Announce to neighbors the ip address is plumbed on ipdev through a
        arping broadcast of unsollicited packets.
        """
        if ':' in self.addr or self.ipdev in ("lo", "lo0"):
            return
        self.log.info("send gratuitous arp to announce %s is at %s", self.addr, self.ipdev)
        send_arp(self.ipdev, self.addr)

    def abort_start(self):
        """
        Return True if the service start should be aborted because of a routed
        ip conflict.
        """
        if 'nonrouted' in self.tags or 'noaction' in self.tags:
            return False
        if self.addr is None:
            return False
        if not self.is_up() and self.check_ping():
            return True
        return False

    def start_link(self):
        """
        Start the ipdev link.
        """
        raise ex.MissImpl('start_link')

    def check_ping(self, count=1, timeout=5):
        """
        Test if the ip is seen as active on the newtorks.
        """
        raise ex.MissImpl('check_ping')

    def startip_cmd(self):
        """
        The os/driver specific start implementation.
        """
        raise ex.MissImpl('startip_cmd')

    def stopip_cmd(self):
        """
        The os/driver specific stop implementation.
        """
        raise ex.MissImpl('stopip_cmd')

    def is_up(self):
        """
        Return True if the ip is plumbed.
        """
        ifconfig = self.get_ifconfig()
        return self._is_up(ifconfig)

    def has_carrier(self, intf):
        if intf is None:
            return
        if not self.check_carrier:
            return
        mode = getattr(self, "mode") if hasattr(self, "mode") else None
        if "dedicated" in self.tags or mode == "dedicated":
            return
        if intf.flag_no_carrier:
            self.status_log("no carrier")
            return False
        return True

    def _is_up(self, ifconfig):
        intf = ifconfig.has_param("ipaddr", self.addr)
        if intf is not None:
            if isinstance(intf.ipaddr, list):
                idx = intf.ipaddr.index(self.addr)
                current_mask = to_cidr(intf.mask[idx])
            else:
                current_mask = to_cidr(intf.mask)
            if self.mask is None:
                self.status_log("netmask is not set nor guessable")
            elif current_mask != to_cidr(self.mask):
                self.status_log("current mask %s, expected %s" %
                                (current_mask, to_cidr(self.mask)))
            ref_dev = intf.name.split(":")[0]
            if self.type == "ip" and ref_dev != self.ipdev:
                self.status_log("current dev %s, expected %s" %
                                (ref_dev, self.ipdev))
            return True
        intf = ifconfig.has_param("ip6addr", self.addr)
        if intf is not None:
            if isinstance(intf.ip6addr, list):
                idx = intf.ip6addr.index(self.addr)
                current_mask = to_cidr(intf.ip6mask[idx])
            else:
                current_mask = to_cidr(intf.ip6mask)
            if current_mask != to_cidr(self.mask):
                self.status_log("current mask %s, expected %s" %
                                (current_mask, to_cidr(self.mask)))
            ref_dev = intf.name.split(":")[0]
            if self.type == "ip" and ref_dev != self.ipdev:
                self.status_log("current dev %s, expected %s" %
                                (ref_dev, self.ipdev))
            return True
        return False

    def allow_start(self):
        """
        Do sanity checks before allowing the start.
        """
        if self.is_up() is True:
            self.log.info("%s is already up on %s", self.addr, self.ipdev)
            raise ex.IpAlreadyUp(self.addr)
        ifconfig = IFCONFIG_MOD.ifconfig()
        intf = ifconfig.interface(self.ipdev)
        if self.has_carrier(intf) is False and not self.svc.options.force:
            self.log.error("interface %s no-carrier.", self.ipdev)
            raise ex.IpDevDown(self.ipdev)
        if intf is None:
            self.log.error("interface %s not found. Cannot stack over it.", self.ipdev)
            raise ex.IpDevDown(self.ipdev)
        if not intf.flag_up:
            if hasattr(intf, 'groupname') and intf.groupname != "":
                l = [_intf for _intf in ifconfig.get_matching_interfaces('groupname', intf.groupname) if _intf.flag_up]
                if len(l) == 1:
                    self.log.info("switch %s to valid alternate path %s", self.ipdev, l[0].name)
                    intf = l[0]
                    self.ipdev = l[0].name
            try:
                self.start_link()
            except ex.MissImpl:
                self.log.error("interface %s is not up. Cannot stack over it.", self.ipdev)
                raise ex.IpDevDown(self.ipdev)
        if not self.svc.abort_start_done and self.check_ping():
            self.log.error("%s is already up on another host", self.addr)
            raise ex.IpConflict(self.addr)
        return

    def lock(self):
        """
        Acquire the startip lock, protecting against allocation of the same
        ipdev stacked device to multiple resources or multiple services.
        """
        timeout = convert_duration(self.svc.options.waitlock)
        if timeout < 0:
            timeout = 120
        delay = 1
        lockfd = None
        action = "startip"
        lockfile = os.path.join(rcEnv.paths.pathlock, action)
        details = "(timeout %d, delay %d, action %s, lockfile %s)" % \
                  (timeout, delay, action, lockfile)
        self.log.debug("acquire startip lock %s", details)

        try:
            lockfd = lock.lock(timeout=timeout, delay=delay, lockfile=lockfile, intent="startip")
        except lock.LockTimeout as exc:
            raise ex.excError("timed out waiting for lock %s: %s" % (details, str(exc)))
        except lock.LockNoLockFile:
            raise ex.excError("lock_nowait: set the 'lockfile' param %s" % details)
        except lock.LockCreateError:
            raise ex.excError("can not create lock file %s" % details)
        except lock.LockAcquire as exc:
            raise ex.excError("another action is currently running %s: %s" % (details, str(exc)))
        except ex.excSignal:
            raise ex.excError("interrupted by signal %s" % details)
        except Exception as exc:
            self.save_exc()
            raise ex.excError("unexpected locking error %s: %s" % (details, str(exc)))

        if lockfd is not None:
            self.lockfd = lockfd

    def unlock(self):
        """
        Release the startip lock.
        """
        lock.unlock(self.lockfd)

    @staticmethod
    def get_ifconfig():
        """
        Wrapper around the os specific rcIfconfig module's ifconfig function.
        Return a parsed ifconfig dataset.
        """
        return IFCONFIG_MOD.ifconfig()

    def start(self):
        """
        Start the resource.
        """
        if self.ipname is None:
            self.log.warning("skip start: no ipname set")
            return
        self.getaddr()
        try:
            self.allow_start()
        except (ex.IpConflict, ex.IpDevDown):
            raise ex.excError
        except (ex.IpAlreadyUp, ex.IpNoActions):
            return
        self.log.debug('pre-checks passed')

        self.lock()
        try:
            arp_announce = self.start_locked()
        finally:
            self.unlock()

        if arp_announce:
            try:
                self.arp_announce()
            except AttributeError:
                self.log.info("arp announce not supported")

        try:
            self.dns_update()
        except ex.excError as exc:
            self.log.error(str(exc))
        self.wait_dns_records()

    def get_mask(self, ifconfig=None):
        if ifconfig is None:
            ifconfig = self.get_ifconfig()
        if self.mask is None:
            intf = ifconfig.interface(self.ipdev)
            if intf is None:
                raise ex.excError("netmask parameter is mandatory with 'noalias' tag")
            self.mask = intf.mask
        if not self.mask:
            if "noaction" not in self.tags:
                self.mask = None
                raise ex.excError("No netmask set on parent interface %s" % self.ipdev)
        if isinstance(self.mask, list):
            try:
                self.mask = self.mask[0]
            except IndexError:
                self.mask = None

    def start_locked(self):
        """
        The start codepath fragment protected by the startip lock.
        """
        ifconfig = self.get_ifconfig()
        self.get_mask(ifconfig)
        if 'noalias' in self.tags:
            self.stacked_dev = self.ipdev
        else:
            self.stacked_dev = ifconfig.get_stacked_dev(self.ipdev,\
                                                        self.addr,\
                                                        self.log)
        if self.stacked_dev is None:
            raise ex.excError("could not determine a stacked dev for parent "
                              "interface %s" % self.ipdev)

        arp_announce = True
        try:
            ret = self.startip_cmd()[0]
            self.can_rollback = True
        except ex.excNotSupported:
            self.log.info("start ip not supported")
            ret = 0
            arp_announce = False

        if ret != 0:
            raise ex.excError("failed")

        return arp_announce

    def dns_update(self):
        """
        Post a dns update request to the collector.
        """
        if self.svc.node.collector_env.dbopensvc is None:
            return

        if self.ipname is None:
            self.log.debug("skip dns update: ipname is not set")
            return

        try:
            self.svc.conf_get(self.rid, "dns_update")
        except ex.OptNotFound:
            self.log.debug("skip dns update: dns_update is not set")
            return

        if not self.is_up():
            self.log.debug("skip dns update: resource is not up")
            return

        if self.dns_name_suffix is None:
            self.log.debug("dns update: dns_name_suffix is not set")

        try:
            self.getaddr()
        except ex.excError as exc:
            self.log.error(str(exc))
            return

        post_data = {
            "content": self.addr,
        }

        if self.dns_name_suffix:
            post_data["name"] = self.dns_name_suffix

        try:
            data = self.svc.node.collector_rest_post(
                "/dns/services/records",
                post_data,
                path=self.dns_rec_name(),
            )
        except Exception as exc:
            raise ex.excError("dns update failed: "+str(exc))
        if "error" in data:
            raise ex.excError(data["error"])

        self.log.info("dns updated")

    def dns_rec_name(self):
        return self.svc.path

    def stop(self):
        """
        Stop the resource.
        """
        if self.ipname is None:
            self.log.info("skip stop: no ipname set")
            return
        self.getaddr(cache_fallback=True)
        if self.is_up() is False:
            self.log.info("%s is already down on %s", self.addr, self.ipdev)
            return
        ifconfig = self.get_ifconfig()
        if 'noalias' in self.tags:
            self.stacked_dev = self.ipdev
        else:
            self.stacked_dev = ifconfig.get_stacked_dev(self.ipdev,\
                                                        self.addr,\
                                                        self.log)
        if self.stacked_dev is None:
            raise ex.excError

        try:
            ret = self.stopip_cmd()[0]
        except ex.excNotSupported:
            self.log.info("stop ip not supported")
            return

        if ret != 0:
            self.log.error("failed")
            raise ex.excError

        tmo = 15
        idx = 0
        for idx in range(tmo):
            if not self.check_ping(count=1, timeout=1):
                break
            time.sleep(1)

        if idx == tmo-1:
            self.log.error("%s refuse to go down", self.addr)
            raise ex.excError

    def allocate(self):
        """
        Request an ip in the ipdev network from the collector.
        """
        if self.svc.node.collector_env.dbopensvc is None:
            return

        try:
            self.conf_get("ipname")
            self.log.info("skip allocate: an ip is already defined")
            return
        except ex.RequiredOptNotFound:
            pass
        except ex.OptNotFound:
            pass

        if self.ipdev is None:
            self.log.info("skip allocate: ipdev is not set")
            return

        try:
            # explicit network setting
            network = self.svc.conf_get(self.rid, "network")
        except ex.OptNotFound:
            network = None

        if network is None:
            # implicit network: the network of the first ipdev ip
            ifconfig = IFCONFIG_MOD.ifconfig()
            intf = ifconfig.interface(self.ipdev)
            try:
                if isinstance(intf.ipaddr, list):
                    baseaddr = intf.ipaddr[0]
                else:
                    baseaddr = intf.ipaddr
                network = str(ipaddress.IPv4Interface(baseaddr).network.network_address)
            except (ValueError, IndexError):
                self.log.info("skip allocate: ipdev %s has no configured address "
                              "and network is not set", self.ipdev)
                return

        post_data = {
            "network": network,
        }

        if self.dns_name_suffix:
            post_data["name"] = self.dns_name_suffix
        else:
            self.log.debug("allocate: dns_name_suffix is not set")

        try:
            data = self.svc.node.collector_rest_post(
                "/networks/%s/allocate" % network,
                post_data,
                path=self.dns_rec_name(),
            )
        except Exception as exc:
            raise ex.excError("ip allocation failed: "+str(exc))
        if "error" in data:
            raise ex.excError(data["error"])

        if "info" in data:
            self.log.info(data["info"])

        self.ipname = data["data"]["ip"]
        self.addr = self.ipname
        self.set_label()
        self.svc._set(self.rid, "ipname", self.ipname)
        if self.gateway in (None, ""):
            gateway = data.get("data", {}).get("network", {}).get("gateway")
            if gateway:
                self.log.info("set gateway=%s", gateway)
                self.svc._set(self.rid, "gateway", gateway)
                self.gateway = gateway
        if self.mask in (None, ""):
            netmask = data.get("data", {}).get("network", {}).get("netmask")
            if netmask:
                self.log.info("set netmask=%s", netmask)
                self.svc._set(self.rid, "netmask", netmask)
                self.mask = str(netmask)
        self.log.info("ip %s allocated", self.ipname)
        record_name = data["data"].get("record_name")
        if record_name:
            self.log.info("record %s created", record_name)

    def release(self):
        """
        Release an allocated ip a collector managed network.
        """
        if self.svc.node.collector_env.dbopensvc is None:
            return

        if self.ipname is None:
            self.log.info("skip release: no ipname set")
            return

        try:
            self.getaddr()
        except ex.excError:
            self.log.info("skip release: ipname does not resolve to an address")
            return

        post_data = {}

        if self.dns_name_suffix:
            post_data["name"] = self.dns_name_suffix
        else:
            self.log.debug("allocate: dns_name_suffix is not set")

        try:
            data = self.svc.node.collector_rest_post(
                "/networks/%s/release" % self.addr,
                post_data,
                path=self.dns_rec_name(),
            )
        except Exception as exc:
            raise ex.excError("ip release failed: "+str(exc))
        if "error" in data:
            self.log.warning(data["error"])
            return

        if "info" in data:
            self.log.info(data["info"])

        self.svc.unset_multi(["%s.ipname" % self.rid])
        self.log.info("ip %s released", self.ipname)

    def expose_data(self):
        if self.expose is None:
            return []
        data = []
        for expose in self.expose:
            data.append(self.parse_expose(expose))
        return data

    def parse_expose(self, expose):
        data = {}
        if "#" in expose:
           # expose data via reference
           resource = self.svc.get_resource(expose)
           data["port"] = resource.options.port
           data["protocol"] = resource.options.protocol
           try:
               data["host_port"] = resource.options.host_port
           except AttributeError:
               pass
           return data

        # expose data inline
        words = expose.split(":")
        if len(words) == 2:
            try:
                data["host_port"] = int(words[1])
            except ValueError:
                raise ex.excError("invalid host port format %s. expected integer" % words[1])
        words = re.split("[-/]", words[0])
        if len(words) != 2:
            raise ex.excError("invalid expose format %s. expected <nsport>/<proto>[:<hostport>]" % expose)
        try:
            data["port"] = int(words[0])
        except ValueError:
            raise ex.excError("invalid expose port format %s. expected integer" % words[0])
        if words[1] not in ("tcp", "udp"):
            raise ex.excError("invalid expose protocol %s. expected tcp or udp" % words[1])
        data["protocol"] = words[1]
        return data



