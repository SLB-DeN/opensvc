# -*- coding: utf8 -*-

"""
This module implements the Node class.
The node
* handles communications with the collector
* holds the list of services
* has a scheduler
"""
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

import os
import datetime
import sys
import json
import socket
import time
import re

if sys.version_info[0] < 3:
    from urllib2 import Request, urlopen
    from urllib2 import HTTPError
    from urllib import urlencode
else:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from urllib.parse import urlencode

import svcBuilder
import xmlrpcClient
from rcGlobalEnv import rcEnv, Storage
import rcLogger
import rcExceptions as ex
import rcConfigParser
from freezer import Freezer
from rcScheduler import Scheduler, SchedOpts, sched_action
from rcColor import formatter
from rcUtilities import justcall, lazy, lazy_initialized, vcall, check_privs, \
                        call, which, purge_cache_expired, read_cf, unset_lazy, \
                        drop_option, is_string, try_decode
from converters import *
from comm import Crypt
import nodedict

if sys.version_info[0] < 3:
    BrokenPipeError = IOError

os.environ['LANG'] = 'C'

DEFAULT_STATUS_GROUPS = [
    "hb",
    "arbitrator",
]

REMOTE_ACTIONS = [
    "freeze",
    "thaw",
]

ACTIONS_NO_PARALLEL = [
    "edit_config",
    "get",
    "print_config",
    "print_resource_status",
    "print_schedule",
    "print_status",
]

ACTIONS_NO_MULTIPLE_SERVICES = [
    "print_resource_status",
]

CONFIG_DEFAULTS = {
    "node_env": "TST",
    "push_schedule": "00:00-06:00@361 mon-sun",
    "sync_schedule": "04:00-06:00@121 mon-sun",
    "comp_schedule": "02:00-06:00@241 sun",
    "collect_stats_schedule": "@10",
    "no_schedule": "",
}

UNPRIVILEGED_ACTIONS = [
    "collector_cli",
]

class Node(Crypt):
    """
    Defines a cluster node.  It contain list of Svc.
    Implements node-level actions and checks.
    """
    def __str__(self):
        return self.nodename

    def __init__(self):
        self.config = None
        self.auth_config = None
        self.clouds = None
        self.paths = Storage(
            reboot_flag=os.path.join(rcEnv.paths.pathvar, "REBOOT_FLAG"),
            tmp_cf=os.path.join(rcEnv.paths.pathvar, "node.conf.tmp"),
        )
        self.services = None
        self.load_config()
        self.options = Storage(
            cron=False,
            syncrpc=False,
            force=False,
            debug=False,
            stats_dir=None,
            begin=None,
            end=None,
            moduleset="",
            module="",
            node=None,
            ruleset_date="",
            objects=[],
            format=None,
            user=None,
            api=None,
            resource=[],
            mac=None,
            broadcast=None,
            param=None,
            value=None,
            extra_argv=[],
        )
        self.set_collector_env()
        self.log = rcLogger.initLogger(rcEnv.nodename)

    @lazy
    def devnull(self):
        return os.open(os.devnull, os.O_RDWR)

    @lazy
    def var_d(self):
        var_d = os.path.join(rcEnv.paths.pathvar, "node")
        if not os.path.exists(var_d):
            os.makedirs(var_d, 0o755)
        return var_d

    @property
    def svcs(self):
        if self.services is None:
            return None
        return list(self.services.values())

    @lazy
    def freezer(self):
        """
        Lazy allocator for the freezer object.
        """
        return Freezer("node")

    @lazy
    def sched(self):
        """
        Lazy initialization of the node Scheduler object.
        """
        return Scheduler(
            config_defaults=CONFIG_DEFAULTS,
            options=self.options,
            config=self.config,
            log=self.log,
            scheduler_actions={
                "checks": SchedOpts(
                    "checks"
                ),
                "dequeue_actions": SchedOpts(
                    "dequeue_actions",
                    schedule_option="no_schedule"
                ),
                "pushstats": SchedOpts(
                    "stats"
                ),
                "collect_stats": SchedOpts(
                    "stats_collection",
                    schedule_option="collect_stats_schedule"
                ),
                "pushpkg": SchedOpts(
                    "packages"
                ),
                "pushpatch": SchedOpts(
                    "patches"
                ),
                "pushasset": SchedOpts(
                    "asset"
                ),
                "pushnsr": SchedOpts(
                    "nsr",
                    schedule_option="no_schedule"
                ),
                "pushhp3par": SchedOpts(
                    "hp3par",
                    schedule_option="no_schedule"
                ),
                "pushemcvnx": SchedOpts(
                    "emcvnx",
                    schedule_option="no_schedule"
                ),
                "pushcentera": SchedOpts(
                    "centera",
                    schedule_option="no_schedule"
                ),
                "pushnetapp": SchedOpts(
                    "netapp",
                    schedule_option="no_schedule"
                ),
                "pushibmds": SchedOpts(
                    "ibmds",
                    schedule_option="no_schedule"
                ),
                "pushdcs": SchedOpts(
                    "dcs",
                    schedule_option="no_schedule"
                ),
                "pushfreenas": SchedOpts(
                    "freenas",
                    schedule_option="no_schedule"
                ),
                "pushxtremio": SchedOpts(
                    "xtremio",
                    schedule_option="no_schedule"
                ),
                "pushgcedisks": SchedOpts(
                    "gcedisks",
                    schedule_option="no_schedule"
                ),
                "pushhds": SchedOpts(
                    "hds",
                    schedule_option="no_schedule"
                ),
                "pushnecism": SchedOpts(
                    "necism",
                    schedule_option="no_schedule"
                ),
                "pusheva": SchedOpts(
                    "eva",
                    schedule_option="no_schedule"
                ),
                "pushibmsvc": SchedOpts(
                    "ibmsvc",
                    schedule_option="no_schedule"
                ),
                "pushvioserver": SchedOpts(
                    "vioserver",
                    schedule_option="no_schedule"
                ),
                "pushsym": SchedOpts(
                    "sym",
                    schedule_option="no_schedule"
                ),
                "pushbrocade": SchedOpts(
                    "brocade", schedule_option="no_schedule"
                ),
                "pushdisks": SchedOpts(
                    "disks"
                ),
                "sysreport": SchedOpts(
                    "sysreport"
                ),
                "compliance_auto": SchedOpts(
                    "compliance",
                    fname="last_comp_check",
                    schedule_option="comp_schedule"
                ),
                "rotate_root_pw": SchedOpts(
                    "rotate_root_pw",
                    fname="last_rotate_root_pw",
                    schedule_option="no_schedule"
                ),
                "auto_reboot": SchedOpts(
                    "reboot",
                    fname="last_auto_reboot",
                    schedule_option="no_schedule"
                )
            },
        )

    @lazy
    def collector(self):
        """
        Lazy initialization of the node Collector object.
        """
        self.log.debug("initialize node::collector")
        return xmlrpcClient.Collector(node=self)

    @lazy
    def nodename(self):
        """
        Lazy initialization of the node name.
        """
        self.log.debug("initialize node::nodename")
        return socket.gethostname().lower()

    @lazy
    def system(self):
        """
        Lazy initialization of the operating system object, which implements
        specific methods like crash or fast-reboot.
        """
        try:
            rcos = __import__('rcOs'+rcEnv.sysname)
        except ImportError:
            rcos = __import__('rcOs')
        return rcos.Os()

    @lazy
    def compliance(self):
        from compliance import Compliance
        comp = Compliance(self)
        return comp

    @staticmethod
    def check_privs(action):
        """
        Raise if the action requires root privileges but the current
        running user is not root.
        """
        if action in UNPRIVILEGED_ACTIONS:
            return
        check_privs()

    @staticmethod
    def split_url(url, default_app=None):
        """
        Split a node.conf node.dbopensvc style url into a
        (protocol, host, port, app) tuple.
        """
        if url == 'None':
            return 'https', '127.0.0.1', '443', '/'

        # transport
        if url.startswith('https'):
            transport = 'https'
            url = url.replace('https://', '')
        elif url.startswith('http'):
            transport = 'http'
            url = url.replace('http://', '')
        else:
            transport = 'https'

        elements = url.split('/')
        if len(elements) < 1:
            raise ex.excError("url %s should have at least one slash")

        # app
        if len(elements) > 1:
            app = elements[1]
        else:
            app = default_app

        # host/port
        subelements = elements[0].split(':')
        if len(subelements) == 1:
            host = subelements[0]
            if transport == 'http':
                port = '80'
            else:
                port = '443'
        elif len(subelements) == 2:
            host = subelements[0]
            port = subelements[1]
        else:
            raise ex.excError("too many columns in %s" % ":".join(subelements))

        return transport, host, port, app

    def set_collector_env(self):
        """
        Store the collector connection elements parsed from the node.conf
        node.uuid, node.dbopensvc and node.dbcompliance as properties of the
        rcEnv class.
        """
        if self.config is None:
            self.load_config()
        if self.config is None:
            return
        if self.config.has_option('node', 'dbopensvc'):
            url = self.config.get('node', 'dbopensvc')
            try:
                (
                    rcEnv.dbopensvc_transport,
                    rcEnv.dbopensvc_host,
                    rcEnv.dbopensvc_port,
                    rcEnv.dbopensvc_app
                ) = self.split_url(url, default_app="feed")
                rcEnv.dbopensvc = "%s://%s:%s/%s/default/call/xmlrpc" % (
                    rcEnv.dbopensvc_transport,
                    rcEnv.dbopensvc_host,
                    rcEnv.dbopensvc_port,
                    rcEnv.dbopensvc_app
                )
            except ex.excError as exc:
                self.log.error("malformed dbopensvc url: %s (%s)",
                               rcEnv.dbopensvc, str(exc))
        else:
            rcEnv.dbopensvc_transport = None
            rcEnv.dbopensvc_host = None
            rcEnv.dbopensvc_port = None
            rcEnv.dbopensvc_app = None
            rcEnv.dbopensvc = None

        if self.config.has_option('node', 'dbcompliance'):
            url = self.config.get('node', 'dbcompliance')
            try:
                (
                    rcEnv.dbcompliance_transport,
                    rcEnv.dbcompliance_host,
                    rcEnv.dbcompliance_port,
                    rcEnv.dbcompliance_app
                ) = self.split_url(url, default_app="init")
                rcEnv.dbcompliance = "%s://%s:%s/%s/compliance/call/xmlrpc" % (
                    rcEnv.dbcompliance_transport,
                    rcEnv.dbcompliance_host,
                    rcEnv.dbcompliance_port,
                    rcEnv.dbcompliance_app
                )
            except ex.excError as exc:
                self.log.error("malformed dbcompliance url: %s (%s)",
                               rcEnv.dbcompliance, str(exc))
        else:
            rcEnv.dbcompliance_transport = rcEnv.dbopensvc_transport
            rcEnv.dbcompliance_host = rcEnv.dbopensvc_host
            rcEnv.dbcompliance_port = rcEnv.dbopensvc_port
            rcEnv.dbcompliance_app = "init"
            rcEnv.dbcompliance = "%s://%s:%s/%s/compliance/call/xmlrpc" % (
                rcEnv.dbcompliance_transport,
                rcEnv.dbcompliance_host,
                rcEnv.dbcompliance_port,
                rcEnv.dbcompliance_app
            )

        if self.config.has_option('node', 'uuid'):
            rcEnv.uuid = self.config.get('node', 'uuid')
        else:
            rcEnv.uuid = ""

    def call(self, *args, **kwargs):
        """
        Wrap rcUtilities call function, setting the node logger.
        """
        kwargs["log"] = self.log
        return call(*args, **kwargs)

    def vcall(self, *args, **kwargs):
        """
        Wrap rcUtilities vcall function, setting the node logger.
        """
        kwargs["log"] = self.log
        return vcall(*args, **kwargs)

    def svcs_selector(self, selector):
        """
        Given a selector string, return a list of service names.
        This exposed method only aggregates ORed elements.
        """
        if os.environ.get("OSVC_SERVICE_LINK"):
            return [os.environ.get("OSVC_SERVICE_LINK")]
        self.build_services(minimal=True)
        try:
            return self._svcs_selector(selector)
        finally:
            del self.services
            self.services = None

    def _svcs_selector(self, selector):
        if selector is None:
            return [svc.svcname for svc in self.svcs]
        elif "," in selector:
            ored_selectors = selector.split(",")
        else:
            ored_selectors = [selector]
        if selector in (None, ""):
            result = [svc.svcname for svc in self.svcs]
        else:
            result = []
            for _selector in ored_selectors:
                for svcname in self.__svcs_selector(_selector):
                    if svcname not in result:
                        result.append(svcname)
        import re
        if len(result) == 0 and re.findall(r"[,\+\*=\^:~><]", selector) == []:
            raise ex.excError("service not found")
        return result

    def __svcs_selector(self, selector):
        """
        Given a selector string, return a list of service names.
        This method only intersect the ANDed elements.
        """
        if selector is None:
            return
        elif "+" in selector:
            anded_selectors = selector.split("+")
        else:
            anded_selectors = [selector]
        if selector in (None, ""):
            result = [svc.svcname for svc in self.svcs]
        else:
            result = None
            for _selector in anded_selectors:
                svcnames = self.___svcs_selector(_selector)
                if result is None:
                    result = svcnames
                else:
                    common = set(result) & set(svcnames)
                    result = [svcname for svcname in result if svcname in common]
        return result

    def ___svcs_selector(self, selector):
        """
        Given a basic selector string (no AND nor OR), return a list of service
        names.
        """
        import fnmatch
        import re

        ops = r"(<=|>=|<|>|=|~|:)"
        negate = selector[0] == "!"
        selector = selector.lstrip("!")
        elts = re.split(ops, selector)

        def matching(current, op, value):
            if op in ("<", ">", ">=", "<="):
                try:
                    current = float(current)
                except ValueError:
                    return False
            if op == "=":
                match = current == value
            elif op == "~":
                match = re.search(value, current)
            elif op == ">":
                match = current > value
            elif op == ">=":
                match = current >= value
            elif op == "<":
                match = current < value
            elif op == "<=":
                match = current <= value
            elif op == ":":
                match = True
            return match

        def svc_matching(svc, param, op, value):
            try:
                current = svc._get(param, evaluate=True)
            except ex.excError:
                current = None
            if current is None:
                if "." in param:
                    group, _param = param.split(".", 1)
                else:
                    group = param
                    _param = None
                rids = [section for section in svc.config.sections() if section.split('#')[0] == group]
                if op == ":" and len(rids) > 0 and _param is None:
                    return True
                elif _param:
                    for rid in rids:
                        try:
                            _current = svc._get(rid+"."+_param, evaluate=True)
                        except ex.excError:
                            continue
                        if matching(_current, op, value):
                            return True
                return False
            if current is None:
                if op == ":":
                    return True
            elif matching(current, op, value):
                return True
            return False

        if len(elts) == 1:
            return [svc.svcname for svc in self.svcs if negate ^ fnmatch.fnmatch(svc.svcname, selector)]
        elif len(elts) != 3:
            return []
        param, op, value = elts
        if op in ("<", ">", ">=", "<="):
            try:
                value = float(value)
            except ValueError:
                return []
        result = []
        for svc in self.svcs:
            ret = svc_matching(svc, param, op, value)
            if ret ^ negate:
                result.append(svc.svcname)

        return result


    def build_services(self, *args, **kwargs):
        """
        Instanciate a Svc objects for each requested services and add it to
        the node.
        """
        if self.svcs is not None and \
           ('svcnames' not in kwargs or \
           (isinstance(kwargs['svcnames'], list) and len(kwargs['svcnames']) == 0)):
            return

        if 'svcnames' in kwargs and \
           isinstance(kwargs['svcnames'], list) and \
           len(kwargs['svcnames']) > 0 and \
           self.svcs is not None:
            svcnames_request = set(kwargs['svcnames'])
            svcnames_actual = set([s.svcname for s in self.svcs])
            if len(svcnames_request-svcnames_actual) == 0:
                return

        self.services = {}

        kwargs["node"] = self
        svcs, errors = svcBuilder.build_services(*args, **kwargs)
        if 'svcnames' in kwargs:
            self.check_build_errors(kwargs['svcnames'], svcs, errors)

        opt_status = kwargs.get("status")
        for svc in svcs:
            if opt_status is not None and not svc.status() in opt_status:
                continue
            self += svc

        rcLogger.set_namelen(self.svcs)

    @staticmethod
    def check_build_errors(svcnames, svcs, errors):
        """
        Raise error if the service builder did not return a Svc object for
        each service we requested.
        """
        if isinstance(svcnames, list):
            n_args = len(svcnames)
        else:
            n_args = 1
        n_svcs = len(svcs)
        if n_svcs == n_args:
            return 0
        msg = ""
        if n_args > 1:
            msg += "%d services validated out of %d\n" % (n_svcs, n_args)
        if len(errors) == 1:
            msg += errors[0]
        else:
            msg += "\n".join(["- "+err for err in errors])
        raise ex.excError(msg)

    def rebuild_services(self, svcnames, minimal):
        """
        Delete the list of Svc objects in the Node object and create a new one.

        Args:
          svcnames: add only Svc objects for services specified
          minimal: include a minimal set of properties in the new Svc objects
        """
        del self.services
        self.services = None
        self.build_services(svcnames=svcnames, minimal=minimal,
                            node=self)

    def close(self):
        """
        Stop the node class workers
        """
        if lazy_initialized(self, "devnull"):
            os.close(self.devnull)

        import gc
        import threading
        gc.collect()
        for thr in threading.enumerate():
            if thr.name == 'QueueFeederThread' and thr.ident is not None:
                thr.join(1)


    def make_temp_config(self):
        """
        Copy the current service configuration file to a temporary
        location for edition.
        If the temp file already exists, propose the --discard
        or --recover options.
        """
        import shutil
        if os.path.exists(self.paths.tmp_cf):
            if self.options.recover:
                pass
            elif self.options.discard:
                shutil.copy(rcEnv.paths.nodeconf, self.paths.tmp_cf)
            else:
                self.edit_config_diff()
                print("%s exists: node conf is already being edited. Set "
                      "--discard to edit from the current configuration, "
                      "or --recover to open the unapplied config" % \
                      self.paths.tmp_cf, file=sys.stderr)
                raise ex.excError
        else:
            shutil.copy(rcEnv.paths.nodeconf, self.paths.tmp_cf)
        return self.paths.tmp_cf

    def edit_config_diff(self):
        """
        Display the diff between the current config and the pending
        unvalidated config.
        """
        from subprocess import call

        def diff_capable(opts):
            cmd = ["diff"] + opts + [rcEnv.paths.nodeconf, self.paths.cf]
            cmd_results = justcall(cmd)
            if cmd_results[2] == 0:
                return True
            return False

        if not os.path.exists(self.paths.tmp_cf):
            return
        if diff_capable(["-u", "--color"]):
            cmd = ["diff", "-u", "--color", rcEnv.paths.nodeconf, self.paths.tmp_cf]
        elif diff_capable(["-u"]):
            cmd = ["diff", "-u", rcEnv.paths.nodeconf, self.paths.tmp_cf]
        else:
            cmd = ["diff", rcEnv.paths.nodeconf, self.paths.tmp_cf]
        call(cmd)

    def edit_config(self):
        """
        Execute an editor on the node configuration file.
        When the editor exits, validate the new configuration file.
        If validation pass, install the new configuration,
        else keep the previous configuration in place and offer the
        user the --recover or --discard choices for its next edit
        config action.
        """
        if "EDITOR" in os.environ:
            editor = os.environ["EDITOR"]
        elif os.name == "nt":
            editor = "notepad"
        else:
            editor = "vi"
        from rcUtilities import which, fsum
        if not which(editor):
            print("%s not found" % editor, file=sys.stderr)
            return 1
        path = self.make_temp_config()
        os.environ["LANG"] = "en_US.UTF-8"
        os.system(' '.join((editor, path)))
        if fsum(path) == fsum(rcEnv.paths.nodeconf):
            os.unlink(path)
            return 0
        results = self._validate_config(path=path)
        if results["errors"] == 0:
            import shutil
            shutil.copy(path, rcEnv.paths.nodeconf)
            os.unlink(path)
        else:
            print("your changes were not applied because of the errors "
                  "reported above. you can use the edit config command "
                  "with --recover to try to fix your changes or with "
                  "--discard to restart from the live config")
        return results["errors"] + results["warnings"]

    def edit_authconfig(self):
        """
        edit_authconfig node action entrypoint
        """
        fpath = os.path.join(rcEnv.paths.pathetc, "auth.conf")
        return self.edit_cf(fpath)

    @staticmethod
    def edit_cf(fpath):
        """
        Choose an editor, setup the LANG, and exec the editor on the
        file passed as argument.
        """
        if "EDITOR" in os.environ:
            editor = os.environ["EDITOR"]
        elif os.name == "nt":
            editor = "notepad"
        else:
            editor = "vi"
        if not which(editor):
            print("%s not found" % editor, file=sys.stderr)
            return 1
        os.environ["LANG"] = "en_US.UTF-8"
        return os.system(' '.join((editor, fpath)))

    def write_config(self):
        """
        Rewrite node.conf using the in-memory ConfigParser object as reference.
        """
        for option in CONFIG_DEFAULTS:
            if self.config.has_option('DEFAULT', option):
                self.config.remove_option('DEFAULT', option)
        for section in self.config.sections():
            if '#sync#' in section:
                self.config.remove_section(section)
        import tempfile
        import shutil
        import codecs
        try:
            tmpf = tempfile.NamedTemporaryFile()
            fpath = tmpf.name
            tmpf.close()
            if sys.version_info[0] >= 3:
                with codecs.open(fpath, "w", "utf-8") as tmpf:
                    self.config.write(tmpf)
            else:
                with open(fpath, "w") as tmpf:
                    self.config.write(tmpf)
            shutil.move(fpath, rcEnv.paths.nodeconf)
        except (OSError, IOError) as exc:
            print("failed to write new %s (%s)" % (rcEnv.paths.nodeconf, str(exc)),
                  file=sys.stderr)
            raise ex.excError
        try:
            os.chmod(rcEnv.paths.nodeconf, 0o0600)
        except OSError:
            pass
        self.load_config()

    def purge_status_last(self):
        """
        Purge the cached status of each and every services and resources.
        """
        for svc in self.svcs:
            svc.purge_status_last()

    def load_config(self):
        """
        Parse the node.conf configuration file and store the RawConfigParser
        object as self.config.
        IOError is catched and self.config is left to its current value, which
        initially is None. So users of self.config should check for this None
        value before use.
        """
        try:
            self.config = read_cf(rcEnv.paths.nodeconf, CONFIG_DEFAULTS)
        except rcConfigParser.ParsingError as exc:
            print(str(exc), file=sys.stderr)
        except IOError:
            # some action don't need self.config
            pass

    def load_auth_config(self):
        """
        Parse the auth.conf configuration file and store the RawConfigParser
        object as self.auth_config.
        The actual parsing is done only on first call. This method is a noop
        on subsequent calls.
        """
        if self.auth_config is not None:
            return
        self.auth_config = read_cf(rcEnv.paths.authconf)

    def setup_sync_outdated(self):
        """
        Return True if any configuration file has changed in the last 10'
        else return False
        """
        import glob
        setup_sync_flag = os.path.join(self.var_d, 'last_setup_sync')
        fpaths = glob.glob(os.path.join(rcEnv.paths.pathetc, '*.conf'))
        if not os.path.exists(setup_sync_flag):
            return True
        for fpath in fpaths:
            try:
                mtime = os.stat(fpath).st_mtime
                with open(setup_sync_flag) as ofile:
                    last = float(ofile.read())
            except (OSError, IOError):
                return True
            if mtime > last:
                return True
        return False

    def __iadd__(self, svc):
        """
        Implement the Node() += Svc() operation, setting the node backpointer
        in the added service, storing the service in a list
        """
        if not hasattr(svc, "svcname"):
            return self
        if self.services is None:
            self.services = {}
        self.services[svc.svcname] = svc
        return self

    def action(self, action, options=None):
        """
        The node action wrapper.
        Looks up which method to handle the action (some are not implemented
        in the Node class), and call the handling method.
        """
        try:
            self.async_action(action)
        except ex.excAbortAction:
            return 0
        return self._action(action, options)

    @sched_action
    def _action(self, action, options=None):
        if "_json_" in action:
            self.options.format = "json"
            action = action.replace("_json_", "_")
        if action.startswith("json_"):
            self.options.format = "json"
            action = "print" + action[4:]

        if action.startswith("compliance_"):
            if self.options.cron and action == "compliance_auto" and \
               self.config.has_option('compliance', 'auto_update') and \
               self.config.getboolean('compliance', 'auto_update'):
                self.compliance.updatecomp = True
                self.compliance.node = self
            return getattr(self.compliance, action)()
        elif action.startswith("collector_") and action != "collector_cli":
            from collector import Collector
            coll = Collector(self.options, self)
            data = getattr(coll, action)()
            self.print_data(data)
            return 0
        elif action.startswith("print"):
            getattr(self, action)()
            return 0
        else:
            ret = getattr(self, action)()
            if ret is None:
                return 0
            elif isinstance(ret, bool):
                if ret:
                    return 1
                else:
                    return 0
            return ret

    @formatter
    def print_data(self, data):
        """
        A dummy method decorated by the formatter function.
        The formatter needs self to access the formatting options, so this
        can't be a staticmethod.
        """
        fmt = self.options.format if self.options.format else "default"
        self.log.debug("format data using the %s formatter", fmt)
        return data

    def print_schedule(self):
        """
        The 'print schedule' node and service action entrypoint.
        """
        return self.sched.print_schedule()

    def get_push_objects(self, section):
        """
        Returns the object names to do inventory on.
        Object names passed as nodemgr argument take precedence.
        If not specified by argument, objet names found in the
        configuration file section are returned.
        """
        if len(self.options.objects) > 0:
            return self.options.objects
        if self.config and self.config.has_option(section, "objects"):
            return self.config.get(section, "objects").split(",")
        return []

    def collect_stats(self):
        """
        Choose the os specific stats collection module and call its collect
        method.
        """
        try:
            mod = __import__("rcStatsCollect"+rcEnv.sysname)
        except ImportError:
            return
        mod.collect(self)

    def pushstats(self):
        """
        Set stats range to push to "last successful pushstat => now"

        Enforce a minimum interval of 21m, and a maximum of 1450m.

        The scheduled task that collects system statistics from system tools
        like sar, and sends the data to the collector.
        A list of metrics can be disabled from the task configuration section,
        using the 'disable' option.
        """
        fpath = self.sched.get_timestamp_f(self.sched.scheduler_actions["pushstats"].fname,
                                           success=True)
        try:
            with open(fpath, "r") as ofile:
                buff = ofile.read()
            start = datetime.datetime.strptime(buff, "%Y-%m-%d %H:%M:%S.%f\n")
            now = datetime.datetime.now()
            delta = now - start
            interval = delta.days * 1440 + delta.seconds // 60 + 10
        except:
            interval = 1450
        if interval < 21:
            interval = 21

        def get_disable_stats():
            """
            Returns the list of stats metrics collection disabled through the
            configuration file stats.disable option.
            """
            if not self.config.has_option("stats", "disable"):
                return []
            disable = self.config.get("stats", "disable")
            try:
                return json.loads(disable)
            except ValueError:
                pass
            if ',' in disable:
                return disable.replace(' ', '').split(',')
            return disable.split()

        disable = get_disable_stats()
        return self.collector.call('push_stats',
                                   stats_dir=self.options.stats_dir,
                                   stats_start=self.options.begin,
                                   stats_end=self.options.end,
                                   interval=interval,
                                   disable=disable)

    @lazy
    def asset(self):
        try:
            m = __import__('rcAsset'+rcEnv.sysname)
        except ImportError:
            raise ex.excError("pushasset methods not implemented on %s"
                              "" % rcEnv.sysname)
        return m.Asset(self)

    def pushpkg(self):
        """
        The pushpkg action entrypoint.
        Inventories the installed packages.
        """
        self.collector.call('push_pkg')

    def pushpatch(self):
        """
        The pushpatch action entrypoint.
        Inventories the installed patches.
        """
        self.collector.call('push_patch')

    def pushasset(self):
        """
        The pushasset action entrypoint.
        Inventories the server properties.
        """
        data = self.asset.get_asset_dict()
        try:
            if self.options.format is None:
                self.print_asset(data)
                return
            self.print_data(data)
        finally:
            self.collector.call('push_asset', self, data)

    def print_asset(self, data):
        from forest import Forest
        from rcColor import color
        tree = Forest()
        head_node = tree.add_node()
        head_node.add_column(rcEnv.nodename, color.BOLD)
        head_node.add_column("Value", color.BOLD)
        head_node.add_column("Source", color.BOLD)
        for key, _data in data.items():
            node = head_node.add_node()
            if key not in ("targets", "lan", "uids", "gids", "hba", "hardware"):
                if _data["value"] is None:
                    _data["value"] = ""
                node.add_column(_data["title"], color.LIGHTBLUE)
                if "formatted_value" in _data:
                    node.add_column(_data["formatted_value"])
                else:
                    node.add_column(_data["value"])
                node.add_column(_data["source"])

        if 'uids' in data:
            node = head_node.add_node()
            node.add_column("uids", color.LIGHTBLUE)
            node.add_column(str(len(data['uids'])))
            node.add_column(self.asset.s_probe)

        if 'gids' in data:
            node = head_node.add_node()
            node.add_column("gids", color.LIGHTBLUE)
            node.add_column(str(len(data['gids'])))
            node.add_column(self.asset.s_probe)

        if 'hardware' in data:
            node = head_node.add_node()
            node.add_column("hardware", color.LIGHTBLUE)
            node.add_column(str(len(data['hardware'])))
            node.add_column(self.asset.s_probe)
            for _data in data['hardware']:
                _node = node.add_node()
                _node.add_column("%s %s" % (_data["type"], _data["path"]))
                _node.add_column("%s: %s [%s]" % (_data["class"], _data["description"], _data["driver"]))

        if 'hba' in data:
            node = head_node.add_node()
            node.add_column("host bus adapters", color.LIGHTBLUE)
            node.add_column(str(len(data['hba'])))
            node.add_column(self.asset.s_probe)
            for _data in data['hba']:
                _node = node.add_node()
                _node.add_column(_data["hba_id"])
                _node.add_column(_data["hba_type"])

                if 'targets' in data:
                    hba_targets = [_d for _d in data['targets'] if _d["hba_id"] == _data["hba_id"]]
                    if len(hba_targets) == 0:
                        continue
                    __node = _node.add_node()
                    __node.add_column("targets")
                    for _d in hba_targets:
                        ___node = __node.add_node()
                        ___node.add_column(_d["tgt_id"])

        if 'lan' in data:
            node = head_node.add_node()
            node.add_column("ip addresses", color.LIGHTBLUE)
            n = 0
            for mac, _data in data['lan'].items():
                for __data in _data:
                    _node = node.add_node()
                    addr = __data["addr"]
                    if __data["mask"]:
                        addr += "/" + __data["mask"]
                    _node.add_column(addr)
                    _node.add_column(__data["intf"])
                    n += 1
            node.add_column(str(n))
            node.add_column(self.asset.s_probe)

        tree.print()


    def pushnsr(self):
        """
        The pushnsr action entrypoint.
        Inventories Networker Backup Server index databases.
        """
        self.collector.call('push_nsr')

    def pushhp3par(self):
        """
        The push3par action entrypoint.
        Inventories HP 3par storage arrays.
        """
        self.collector.call('push_hp3par', self.options.objects)

    def pushnetapp(self):
        """
        The pushnetapp action entrypoint.
        Inventories NetApp storage arrays.
        """
        self.collector.call('push_netapp', self.options.objects)

    def pushcentera(self):
        """
        The pushcentera action entrypoint.
        Inventories Centera storage arrays.
        """
        self.collector.call('push_centera', self.options.objects)

    def pushemcvnx(self):
        """
        The pushemcvnx action entrypoint.
        Inventories EMC VNX storage arrays.
        """
        self.collector.call('push_emcvnx', self.options.objects)

    def pushibmds(self):
        """
        The pushibmds action entrypoint.
        Inventories IBM DS storage arrays.
        """
        self.collector.call('push_ibmds', self.options.objects)

    def pushgcedisks(self):
        """
        The pushgcedisks action entrypoint.
        Inventories Google Compute Engine disks.
        """
        self.collector.call('push_gcedisks', self.options.objects)

    def pushfreenas(self):
        """
        The pushfreenas action entrypoint.
        Inventories FreeNas storage arrays.
        """
        self.collector.call('push_freenas', self.options.objects)

    def pushxtremio(self):
        """
        The pushxtremio action entrypoint.
        Inventories XtremIO storage arrays.
        """
        self.collector.call('push_xtremio', self.options.objects)

    def pushdcs(self):
        """
        The pushdcs action entrypoint.
        Inventories DataCore SAN Symphony storage arrays.
        """
        self.collector.call('push_dcs', self.options.objects)

    def pushhds(self):
        """
        The pushhds action entrypoint.
        Inventories Hitachi storage arrays.
        """
        self.collector.call('push_hds', self.options.objects)

    def pushnecism(self):
        """
        The pushnecism action entrypoint.
        Inventories NEC iSM storage arrays.
        """
        self.collector.call('push_necism', self.options.objects)

    def pusheva(self):
        """
        The pusheva action entrypoint.
        Inventories HP EVA storage arrays.
        """
        self.collector.call('push_eva', self.options.objects)

    def pushibmsvc(self):
        """
        The pushibmsvc action entrypoint.
        Inventories IBM SVC storage arrays.
        """
        self.collector.call('push_ibmsvc', self.options.objects)

    def pushvioserver(self):
        """
        The pushvioserver action entrypoint.
        Inventories IBM vio server storage arrays.
        """
        self.collector.call('push_vioserver', self.options.objects)

    def pushsym(self):
        """
        The pushsym action entrypoint.
        Inventories EMC Symmetrix server storage arrays.
        """
        objects = self.get_push_objects("sym")
        self.collector.call('push_sym', objects)

    def pushbrocade(self):
        """
        The pushsym action entrypoint.
        Inventories Brocade SAN switches.
        """
        self.collector.call('push_brocade', self.options.objects)

    def auto_rotate_root_pw(self):
        """
        The rotate_root_pw node action entrypoint.
        """
        self.rotate_root_pw()

    def unschedule_reboot(self):
        """
        Unflag the node for reboot during the next allowed period.
        """
        if not os.path.exists(self.paths.reboot_flag):
            print("reboot already not scheduled")
            return
        os.unlink(self.paths.reboot_flag)
        print("reboot unscheduled")

    def schedule_reboot(self):
        """
        Flag the node for reboot during the next allowed period.
        """
        if not os.path.exists(self.paths.reboot_flag):
            with open(self.paths.reboot_flag, "w") as ofile:
                ofile.write("")
        import stat
        statinfo = os.stat(self.paths.reboot_flag)
        if statinfo.st_uid != 0:
            os.chown(self.paths.reboot_flag, 0, -1)
            print("set %s root ownership"%self.paths.reboot_flag)
        if statinfo.st_mode & stat.S_IWOTH:
            mode = statinfo.st_mode ^ stat.S_IWOTH
            os.chmod(self.paths.reboot_flag, mode)
            print("set %s not world-writable"%self.paths.reboot_flag)
        print("reboot scheduled")

    def schedule_reboot_status(self):
        """
        Display information about the next scheduled reboot
        """
        import stat
        if os.path.exists(self.paths.reboot_flag):
            statinfo = os.stat(self.paths.reboot_flag)
        else:
            statinfo = None

        if statinfo is None or \
           statinfo.st_uid != 0 or statinfo.st_mode & stat.S_IWOTH:
            print("reboot is not scheduled")
        else:
            sch = self.sched.scheduler_actions["auto_reboot"]
            schedule = self.sched.sched_get_schedule_raw(sch.section, sch.schedule_option)
            print("reboot is scheduled")
            print("reboot schedule: %s" % schedule)

        result = self.sched.get_next_schedule("auto_reboot")
        if result["next_sched"]:
            print("next reboot slot:",
                  result["next_sched"].strftime("%a %Y-%m-%d %H:%M"))
        else:
            print("next reboot slot: none in the next %d days" % (result["minutes"]/144))

    def auto_reboot(self):
        """
        The scheduler task executing the node reboot if the scheduler
        constraints are satisfied and the reboot flag is set.
        """
        if not os.path.exists(self.paths.reboot_flag):
            print("%s is not present. no reboot scheduled" % self.paths.reboot_flag)
            return
        import stat
        statinfo = os.stat(self.paths.reboot_flag)
        if statinfo.st_uid != 0:
            print("%s does not belong to root. abort scheduled reboot" % self.paths.reboot_flag)
            return
        if statinfo.st_mode & stat.S_IWOTH:
            print("%s is world writable. abort scheduled reboot" % self.paths.reboot_flag)
            return
        try:
            once = self.config.get("reboot", "once")
            once = convert_boolean(once)
        except:
            once = True
        if once:
            print("remove %s and reboot" % self.paths.reboot_flag)
            os.unlink(self.paths.reboot_flag)
        self.reboot()

    def pushdisks(self):
        """
        The pushdisks node action entrypoint.

        Send to the collector the list of disks visible on this node, and
        their use by service.
        """
        try:
            data = self.push_disks_data()
            if self.options.format is None:
                self.print_push_disks(data)
                return
            self.print_data(data)
        finally:
            self.collector.call('push_disks', data)

    def print_push_disks(self, data):
        from forest import Forest
        from rcColor import color
        from converters import print_size

        tree = Forest()
        head_node = tree.add_node()
        head_node.add_column(rcEnv.nodename, color.BOLD)
        head_node.add_column("Size.Used", color.BOLD)
        head_node.add_column("Vendor", color.BOLD)
        head_node.add_column("Model", color.BOLD)

        if len(data["disks"]) > 0:
            disks_node = head_node.add_node()
            disks_node.add_column("disks", color.BROWN)

        if len(data["served_disks"]) > 0:
            sdisks_node = head_node.add_node()
            sdisks_node.add_column("served disks", color.BROWN)

        for disk_id, disk in data["disks"].items():
            disk_node = disks_node.add_node()
            disk_node.add_column(disk_id, color.LIGHTBLUE)
            disk_node.add_column(print_size(disk["size"]))
            disk_node.add_column(disk["vendor"])
            disk_node.add_column(disk["model"])

            for svcname, service in disk["services"].items():
                svc_node = disk_node.add_node()
                svc_node.add_column(svcname, color.LIGHTBLUE)
                svc_node.add_column(print_size(service["used"]))

            if disk["used"] < disk["size"]:
                svc_node = disk_node.add_node()
                svc_node.add_column(rcEnv.nodename, color.LIGHTBLUE)
                svc_node.add_column(print_size(disk["size"] - disk["used"]))

        for disk_id, disk in data["served_disks"].items():
            disk_node = disks_node.add_node()
            disk_node.add_column(disk_id, color.LIGHTBLUE)
            disk_node.add_column(print_size(disk["size"]))
            disk_node.add_column(disk["vdisk_id"])

        tree.print()

    def push_stats_fs_u(self, l, sync=True):
        args = [l[0], l[1]]
        if self.auth_node:
            args += [(rcEnv.uuid, rcEnv.nodename)]
        self.proxy.insert_stats_fs_u(*args)

    def push_disks_data(self):
        if self.svcs is None:
            self.build_services()

        import re
        di = __import__('rcDiskInfo'+rcEnv.sysname)
        data = {
            "disks": {},
            "served_disks": {},
        }

        for svc in self.svcs:
            # hash to add up disk usage inside a service
            for r in svc.get_resources():
                if hasattr(r, "name"):
                    disk_dg = r.name
                elif hasattr(r, "dev"):
                    disk_dg = r.dev
                else:
                    disk_dg = r.rid

                if hasattr(r, 'devmap') and hasattr(r, 'vm_hostname'):
                    for dev_id, vdev_id in r.devmap():
                        try:
                            disk_id = self.diskinfo.disk_id(dev_id)
                        except:
                            continue
                        try:
                            disk_size = self.diskinfo.disk_size(dev_id)
                        except:
                            continue
                        data["served_disks"][disk_id] = {
                            "dev_id": dev_id,
                            "vdev_id": vdev_id,
                            "vdisk_id": r.vm_hostname+'.'+vdev_id,
                            "size": disk_size,
                            "cluster": self.cluster_name.cluster,
                        }

                try:
                    devpaths = r.sub_devs()
                except Exception as e:
                    print(e)
                    devpaths = []

                for devpath in devpaths:
                    for d, used, region in self.devtree.get_top_devs_usage_for_devpath(devpath):
                        disk_id = self.diskinfo.disk_id(d)
                        if disk_id is None or disk_id == "":
                            continue
                        if disk_id.startswith(rcEnv.nodename+".loop"):
                            continue
                        disk_size = self.devtree.get_dev_by_devpath(d).size

                        if disk_id not in data["disks"]:
                            data["disks"][disk_id] = {
                                "size": disk_size,
                                "vendor": self.diskinfo.disk_vendor(d),
                                "model": self.diskinfo.disk_model(d),
                                "used": 0,
                                "services": {},
                            }

                        if svc.svcname not in data["disks"][disk_id]["services"]:
                            data["disks"][disk_id]["services"][svc.svcname] = {
                                "svcname": svc.svcname,
                                "used": used,
                                "dg": disk_dg,
                                "region": region
                            }
                        else:
                            # consume space at service level
                            data["disks"][disk_id]["services"][svc.svcname]["dg"] = "multi"
                            data["disks"][disk_id]["services"][svc.svcname]["used"] += used
                            if data["disks"][disk_id]["services"][svc.svcname]["used"] > disk_size:
                                data["disks"][disk_id]["services"][svc.svcname]["used"] = disk_size

                        # consume space at disk level
                        data["disks"][disk_id]["used"] += used
                        if data["disks"][disk_id]["used"] > disk_size:
                            data["disks"][disk_id]["used"] = disk_size

        done = []

        try:
            devpaths = self.devlist()
        except Exception as e:
            print(e)
            devpaths = []

        for devpath in devpaths:
            disk_id = self.diskinfo.disk_id(devpath)
            if disk_id is None or disk_id == "":
                continue
            if disk_id.startswith(rcEnv.nodename+".loop"):
                continue
            if re.match(r"/dev/rdsk/.*s[01345678]", devpath):
                # don't report partitions
                continue

            # Linux Node:devlist() reports paths, so we can have duplicate
            # disks here.
            if disk_id in done:
                continue
            done.append(disk_id)
            if disk_id in data["disks"]:
                continue

            disk_size = self.devtree.get_dev_by_devpath(devpath).size

            data["disks"][disk_id] = {
                "size": disk_size,
                "vendor": self.diskinfo.disk_vendor(devpath),
                "model": self.diskinfo.disk_model(devpath),
                "used": 0,
                "services": {},
            }

        return data

    def shutdown(self):
        """
        The shutdown node action entrypoint.
        To be overloaded by child classes.
        """
        self.log.warning("to be implemented")

    def reboot(self):
        """
        The reboot node action entrypoint.
        """
        self.do_triggers("reboot", "pre")
        self.log.info("reboot")
        self._reboot()

    def do_triggers(self, action, when):
        """
        Determine which triggers need to be executed for the action and
        executes them when appropriate.
        """
        trigger = None
        blocking_trigger = None
        try:
            trigger = self.config.get(action, when)
        except:
            pass
        try:
            blocking_trigger = self.config.get(action, "blocking_"+when)
        except:
            pass
        if trigger:
            self.log.info("execute trigger %s", trigger)
            try:
                self.do_trigger(trigger)
            except ex.excError:
                pass
        if blocking_trigger:
            self.log.info("execute blocking trigger %s", trigger)
            try:
                self.do_trigger(blocking_trigger)
            except ex.excError:
                if when == "pre":
                    self.log.error("blocking pre trigger error: abort %s", action)
                raise

    def do_trigger(self, cmd, err_to_warn=False):
        """
        The trigger execution wrapper.
        """
        import shlex
        _cmd = shlex.split(cmd)
        ret, out, err = self.vcall(_cmd, err_to_warn=err_to_warn)
        if ret != 0:
            raise ex.excError((ret, out, err))

    def _reboot(self):
        """
        A system reboot method to be implemented by child classes.
        """
        self.log.warning("to be implemented")

    def sysreport(self):
        """
        The sysreport node action entrypoint.

        Send to the collector a tarball of the files the user wants to track
        that changed since the last call.
        If the force option is set, send all files the user wants to track.
        """
        try:
            mod = __import__('rcSysReport'+rcEnv.sysname)
        except ImportError:
            print("sysreport is not supported on this os")
            return
        mod.SysReport(node=self).sysreport(force=self.options.force)

    def get_prkey(self):
        """
        Returns the persistent reservation key.
        Once generated from the algorithm, the prkey is written to the config
        file to ensure its stability.
        """
        if self.config.has_option("node", "prkey"):
            hostid = self.config.get("node", "prkey")
            if len(hostid) > 18 or not hostid.startswith("0x") or \
               len(set(hostid[2:]) - set("0123456789abcdefABCDEF")) > 0:
                raise ex.excError("prkey in node.conf must have 16 significant"
                                  " hex digits max (ex: 0x90520a45138e85)")
            return hostid
        self.log.info("can't find a prkey forced in node.conf. generate one.")
        hostid = "0x"+self.hostid()
        if not self.config.has_section("node"):
            self.config.add_section("node")
        self.config.set('node', 'prkey', hostid)
        self.write_config()
        return hostid

    def prkey(self):
        """
        Print the persistent reservation key.
        """
        print(self.get_prkey())

    @staticmethod
    def hostid():
        """
        Return a stable host unique id
        """
        mod = __import__('hostid'+rcEnv.sysname)
        return mod.hostid()

    def checks(self):
        """
        The checks node action entrypoint.
        Runs health checks.
        """
        import checks
        if self.svcs is None:
            self.build_services()
        checkers = checks.checks(self.svcs)
        checkers.node = self
        data = checkers.do_checks()
        self.print_data(data)

    def wol(self):
        """
        Send a Wake-On-LAN packet to a mac address on the broadcast address.
        """
        import rcWakeOnLan
        if self.options.mac is None:
            print("missing parameter. set --mac argument. multiple mac "
                  "addresses must be separated by comma", file=sys.stderr)
            print("example 1 : --mac 00:11:22:33:44:55", file=sys.stderr)
            print("example 2 : --mac 00:11:22:33:44:55,66:77:88:99:AA:BB",
                  file=sys.stderr)
            return 1
        if self.options.broadcast is None:
            print("missing parameter. set --broadcast argument. needed to "
                  "identify accurate network to use", file=sys.stderr)
            print("example 1 : --broadcast 10.25.107.255", file=sys.stderr)
            print("example 2 : --broadcast 192.168.1.5,10.25.107.255",
                  file=sys.stderr)
            return 1
        macs = self.options.mac.split(',')
        broadcasts = self.options.broadcast.split(',')
        for brdcast in broadcasts:
            for mac in macs:
                req = rcWakeOnLan.wolrequest(macaddress=mac, broadcast=brdcast)
                if not req.check_broadcast():
                    print("Error : skipping broadcast address <%s>, not in "
                          "the expected format 123.123.123.123" % req.broadcast,
                          file=sys.stderr)
                    break
                if not req.check_mac():
                    print("Error : skipping mac address <%s>, not in the "
                          "expected format 00:11:22:33:44:55" % req.mac,
                          file=sys.stderr)
                    continue
                if req.send():
                    print("Sent Wake On Lan packet to mac address <%s>"%req.mac)
                else:
                    print("Error while trying to send Wake On Lan packet to "
                          "mac address <%s>" % req.mac, file=sys.stderr)

    def unset(self):
        """
        The 'unset' action entrypoint.
        Verifies the --param and --value are set, and finally call the _unset
        internal method.
        """
        if self.options.param is None:
            print("no parameter. set --param", file=sys.stderr)
            return 1
        elements = self.options.param.split('.')
        if len(elements) != 2:
            print("malformed parameter. format as 'section.key'",
                  file=sys.stderr)
            return 1
        section, option = elements
        if section in DEFAULT_STATUS_GROUPS:
            err = 0
            for rid in [rid for rid in self.config.sections() if rid.startswith(section+"#")]:
                try:
                    self._unset(rid, option)
                except ex.excError as exc:
                    print(exc, file=sys.stderr)
                    err += 1
            return err
        else:
            try:
                self._unset(section, option)
                return 0
            except ex.excError as exc:
                print(exc, file=sys.stderr)
                return 1

    def _unset(self, section, option):
        """
        Delete an option in the service configuration file specified section.
        """
        lines = self._read_cf().splitlines()
        lines = self.__unset(lines, section, option)
        try:
            self._write_cf(lines)
        except (IOError, OSError) as exc:
            raise ex.excError(str(exc))

    def __unset(self, lines, section, option):
        section = "[%s]" % section
        need_write = False
        in_section = False
        for i, line in enumerate(lines):
            sline = line.strip()
            if sline == section:
                in_section = True
            elif in_section:
                if sline.startswith("["):
                    break
                elif "=" in sline:
                    elements = sline.split("=")
                    _option = elements[0].strip()
                    if option != _option:
                        continue
                    del lines[i]
                    need_write = True
                    while i < len(lines) and "=" not in lines[i] and \
                          not lines[i].strip().startswith("[") and \
                          lines[i].strip() != "":
                        del lines[i]

        if not in_section:
            raise ex.excError("section %s not found" % section)

        if not need_write:
            raise ex.excError("option '%s' not found in section %s" % (option, section))

        return lines

    def _read_cf(self):
        """
        Return the service config file content.
        """
        import codecs
        with codecs.open(rcEnv.paths.nodeconf, "r", "utf8") as ofile:
            buff = ofile.read()
        return buff

    def _write_cf(self, buff):
        """
        Truncate the service config file and write buff.
        """
        import codecs
        import tempfile
        import shutil
        if isinstance(buff, list):
            buff = "\n".join(buff) + "\n"
        ofile = tempfile.NamedTemporaryFile(delete=False, dir=rcEnv.paths.pathtmp, prefix="node")
        fpath = ofile.name
        os.chmod(fpath, 0o0644)
        ofile.close()
        with codecs.open(fpath, "w", "utf8") as ofile:
            ofile.write(buff)
            ofile.flush()
            os.fsync(ofile)
        shutil.move(fpath, rcEnv.paths.nodeconf)

    def allocate_rid(self, group, sections):
        """
        Return an unused rid in <group>.
        """
        prefix = group + "#"
        rids = [section for section in sections if section.startswith(prefix)]
        idx = 1
        while True:
            rid = "#".join((group, str(idx)))
            if rid in rids:
                idx += 1
                continue
            return rid

    def get(self):
        """
        The 'get' action entrypoint.
        Verifies the --param is set, set DEFAULT as section if no section was
        specified, and finally print,
        * the raw value if --eval is not set
        * the dereferenced and evaluated value if --eval is set
        """
        try:
            print(self._get(self.options.param, self.options.eval))
        except ex.OptNotFound as exc:
            print(exc.default)
        except ex.RequiredOptNotFound as exc:
            return 1
        except ex.excError as exc:
            print(exc, file=sys.stderr)
            return 1
        except Exception:
            return 1
        return 0

    def _get(self, param=None, evaluate=False):
        """
        Verifies the param is set, set DEFAULT as section if no section was
        specified, and finally return,
        * the raw value if evaluate is False
        * the dereferenced and evaluated value if evaluate is True
        """
        if param is None:
            raise ex.excError("no parameter. set --param")
        elements = param.split('.')
        if len(elements) != 2:
            raise ex.excError("malformed parameter. format as 'section.key'")
        section, option = elements
        if section == "DEFAULT":
            raise ex.excError("the DEFAULT section is not allowed in %s" % rcEnv.paths.nodeconf)
        if not self.config.has_section(section):
            raise ex.excError("section [%s] not found" % section)
        if evaluate:
            return self.conf_get(section, option, "string", scope=True)
        else:
            return self.config.get(section, option)

    def set(self):
        """
        The 'set' action entrypoint.
        Verifies the --param and --value are set, and set the value using the
        internal _set() method.
        """
        if self.options.kw is not None:
            return self.set_multi(self.options.kw)
        else:
            return self.set_mono()

    def set_multi(self, kws):
        changes = []
        self.set_multi_cache = {}
        for kw in kws:
            if "=" not in kw:
                raise ex.excError("malformed kw expression: %s: no '='" % kw)
            keyword, value = kw.split("=", 1)
            if keyword[-1] == "-":
                op = "remove"
                keyword = keyword[:-1]
            elif keyword[-1] == "+":
                op = "add"
                keyword = keyword[:-1]
            else:
                op = "set"
            index = None
            if "[" in keyword:
                keyword, right = keyword.split("[", 1)
                if not right.endswith("]"):
                    raise ex.excError("malformed kw expression: %s: no trailing"
                                      " ']' at the end of keyword" % kw)
                try:
                    index = int(right[:-1])
                except ValueError:
                    raise ex.excError("malformed kw expression: %s: index is "
                                      "not integer" % kw)
            if "." in keyword and "#" not in keyword:
                # <group>.keyword[@<scope>] format => loop over all rids in group
                group = keyword.split(".")[0]
                if group in DEFAULT_STATUS_GROUPS:
                    for rid in [rid for rid in self.config.sections() if rid.startswith(group+"#")]:
                        keyword = rid + keyword[keyword.index("."):]
                        changes.append(self.set_mangle(keyword, op, value, index))
                else:
                    # <section>.keyword[@<scope>]
                    changes.append(self.set_mangle(keyword, op, value, index))
            else:
                # <rid>.keyword[@<scope>]
                changes.append(self.set_mangle(keyword, op, value, index))
        self._set_multi(changes)

    def set_mono(self):
        self.set_multi_cache = {}
        if self.options.param is None:
            print("no parameter. set --param", file=sys.stderr)
            return 1
        if self.options.value is None and \
           self.options.add is None and \
           self.options.remove is None:
            print("no value. set --value, --add or --remove", file=sys.stderr)
            return 1
        if self.options.add:
            op = "add"
            value = self.options.add
        elif self.options.remove:
            op = "remove"
            value = self.options.remove
        else:
            op = "set"
            value = self.options.value
        keyword = self.options.param
        index = self.options.index
        changes = []
        if "." in keyword and "#" not in keyword:
            # <group>.keyword[@<scope>] format => loop over all rids in group
            group = keyword.split(".")[0]
            if group in DEFAULT_STATUS_GROUPS:
                for rid in [rid for rid in self.config.sections() if rid.startswith(group+"#")]:
                    keyword = rid + keyword[keyword.index("."):]
                    changes.append(self.set_mangle(keyword, op, value, index))
            else:
                # <section>.keyword[@<scope>]
                changes.append(self.set_mangle(keyword, op, value, index))
        else:
            # <rid>.keyword[@<scope>]
            changes.append(self.set_mangle(keyword, op, value, index))
        self._set_multi(changes)

    def set_mangle(self, keyword, op, value, index):
        def list_value(keyword):
            import rcConfigParser
            if keyword in self.set_multi_cache:
                return self.set_multi_cache[keyword].split()
            try:
                _value = self._get(keyword, self.options.eval).split()
            except (ex.excError, rcConfigParser.NoOptionError) as exc:
                _value = []
            return _value

        if op == "remove":
            _value = list_value(keyword)
            if value not in _value:
                return
            _value.remove(value)
            _value = " ".join(_value)
            self.set_multi_cache[keyword] = _value
        elif op == "add":
            _value = list_value(keyword)
            if value in _value:
                return
            index = index if index is not None else len(_value)
            _value.insert(index, value)
            _value = " ".join(_value)
            self.set_multi_cache[keyword] = _value
        else:
            _value = value
        elements = keyword.split('.')
        if len(elements) != 2:
            raise ex.excError("malformed kw: format as 'section.key'")
        return elements[0], elements[1], _value

    def _set(self, section, option, value):
        self._set_multi([[section, option, value]])

    def _set_multi(self, changes):
        changed = False
        lines = self._read_cf().splitlines()
        for change in changes:
            if change is None:
                continue
            section, option, value = change
            lines = self.__set(lines, section, option, value)
            changed = True
        if not changed:
            # all changes were None
            return
        try:
            self._write_cf(lines)
        except (IOError, OSError) as exc:
            raise ex.excError(str(exc))

    def __set(self, lines, section, option, value):
        """
        Set <option> to <value> in <section> of the configuration file.
        """
        section = "[%s]" % section
        done = False
        in_section = False
        value = try_decode(value)

        for idx, line in enumerate(lines):
            sline = line.strip()
            if sline == section:
                in_section = True
            elif in_section:
                if sline.startswith("["):
                    if done:
                        # matching section done and new section begins
                        break
                    else:
                        # section found and parsed and no option => add option
                        section_idx = idx
                        while section_idx > 0 and lines[section_idx-1].strip() == "":
                            section_idx -= 1
                        lines.insert(section_idx, "%s = %s" % (option, value))
                        done = True
                        break
                elif "=" in sline:
                    elements = sline.split("=")
                    _option = elements[0].strip()

                    if option != _option:
                        continue

                    if done:
                        # option already set : remove dup
                        del lines[idx]
                        while idx < len(lines) and "=" not in lines[idx] and \
                              not lines[idx].strip().startswith("[") and \
                              lines[idx].strip() != "":
                            del lines[idx]
                        continue

                    _value = elements[1].strip()
                    section_idx = idx

                    while section_idx < len(lines)-1 and  \
                          "=" not in lines[section_idx+1] and \
                          not lines[section_idx+1].strip().startswith("["):
                        section_idx += 1
                        if lines[section_idx].strip() == "":
                            continue
                        _value += " %s" % lines[section_idx].strip()

                    if value.replace("\n", " ") == _value:
                        return lines

                    lines[idx] = "%s = %s" % (option, value)
                    section_idx = idx

                    while section_idx < len(lines)-1 and \
                          "=" not in lines[section_idx+1] and \
                          not lines[section_idx+1].strip().startswith("[") and \
                          lines[section_idx+1].strip() != "":
                        del lines[section_idx+1]

                    done = True

        if not done:
            while len(lines) > 0 and lines[-1].strip() == "":
                lines.pop()
            if not in_section:
                # section in last position and no option => add section
                lines.append("")
                lines.append(section)
            lines.append("%s = %s" % (option, value))

        return lines

    def register_as_user(self):
        """
        Returns a node registration unique id, authenticating to the
        collector as a user.
        """
        data = self.collector.call('register_node')
        if data is None:
            raise ex.excError("failed to obtain a registration number")
        elif isinstance(data, dict) and "ret" in data and data["ret"] != 0:
            msg = "failed to obtain a registration number"
            if "msg" in data and len(data["msg"]) > 0:
                msg += "\n" + data["msg"]
            raise ex.excError(msg)
        elif isinstance(data, list):
            raise ex.excError(data[0])
        return data

    def register_as_node(self):
        """
        Returns a node registration unique id, authenticating to the
        collector as a node.
        """
        try:
            data = self.collector_rest_post("/register", {
                "nodename": rcEnv.nodename,
                "app": self.options.app
            })
        except Exception as exc:
            raise ex.excError(str(exc))
        if "error" in data:
            raise ex.excError(data["error"])
        return data["data"]["uuid"]

    def register(self):
        """
        Do anonymous or indentified node register to obtain a node uuid
        that will be used as a password valid for the current hostname used
        as a username in the application code context.
        """
        if self.options.user is None:
            register_fn = "register_as_user"
        else:
            register_fn = "register_as_node"
        try:
            rcEnv.uuid = getattr(self, register_fn)()
        except ex.excError as exc:
            print(exc, file=sys.stderr)
            return 1

        if not self.config.has_section('node'):
            self.config.add_section('node')
        self.config.set('node', 'uuid', rcEnv.uuid)

        try:
            self.write_config()
        except ex.excError:
            print("failed to write registration number: %s" % rcEnv.uuid,
                  file=sys.stderr)
            return 1

        print("registered")
        self.pushasset()
        self.pushdisks()
        self.pushpkg()
        self.pushpatch()
        self.sysreport()
        self.checks()
        return 0

    def service_action_worker(self, svc, action, options):
        """
        The method the per-service subprocesses execute
        """
        try:
            ret = svc.action(action, options)
        finally:
            self.close()
            sys.exit(1)
        self.close()
        sys.exit(ret)

    @lazy
    def diskinfo(self):
        di = __import__('rcDiskInfo'+rcEnv.sysname)
        return di.diskInfo()

    @lazy
    def devtree(self):
        try:
            mod = __import__("rcDevTree"+rcEnv.sysname)
        except ImportError:
            return
        tree = mod.DevTree()
        tree.load()
        return tree

    def devlist(self):
        """
        Return the node's top-level device paths
        """
        devpaths = []
        for dev in self.devtree.get_top_devs():
            if len(dev.devpath) > 0:
                devpaths.append(dev.devpath[0])
        return devpaths

    def updatecomp(self):
        """
        Downloads and installs the compliance module archive from the url
        specified as node.repocomp or node.repo in node.conf.
        """
        if self.config.has_option('node', 'repocomp'):
            pkg_name = self.config.get('node', 'repocomp').strip('/') + "/current"
        elif self.config.has_option('node', 'repo'):
            pkg_name = self.config.get('node', 'repo').strip('/') + "/compliance/current"
        else:
            if self.options.cron:
                return 0
            print("node.repo or node.repocomp must be set in node.conf",
                  file=sys.stderr)
            return 1
        import tempfile
        tmpf = tempfile.NamedTemporaryFile()
        fpath = tmpf.name
        tmpf.close()
        try:
            ret = self._updatecomp(pkg_name, fpath)
        finally:
            if os.path.exists(fpath):
                os.unlink(fpath)
        return ret

    def _updatecomp(self, pkg_name, fpath):
        """
        Downloads and installs the compliance module archive from the url
        specified by the pkg_name argument. The download destination file
        is specified by fpath. The caller is responsible for its deletion.
        """
        print("get %s (%s)"%(pkg_name, fpath))
        try:
            self.urlretrieve(pkg_name, fpath)
        except IOError as exc:
            print("download failed", ":", exc, file=sys.stderr)
            if self.options.cron:
                return 0
            return 1
        tmpp = os.path.join(rcEnv.paths.pathtmp, 'compliance')
        backp = os.path.join(rcEnv.paths.pathtmp, 'compliance.bck')
        compp = os.path.join(rcEnv.paths.pathvar, 'compliance')
        if not os.path.exists(compp):
            os.makedirs(compp, 0o755)
        import shutil
        try:
            shutil.rmtree(backp)
        except (OSError, IOError):
            pass
        print("extract compliance in", rcEnv.paths.pathtmp)
        import tarfile
        tar = tarfile.open(fpath)
        os.chdir(rcEnv.paths.pathtmp)
        try:
            tar.extractall()
            tar.close()
        except (OSError, IOError):
            print("failed to unpack", file=sys.stderr)
            return 1
        print("install new compliance")
        for root, dirs, files in os.walk(tmpp):
            for fpath in dirs:
                os.chown(os.path.join(root, fpath), 0, 0)
                for fpath in files:
                    os.chown(os.path.join(root, fpath), 0, 0)
        shutil.move(compp, backp)
        shutil.move(tmpp, compp)
        return 0

    def updatepkg(self):
        """
        Downloads and upgrades the OpenSVC agent, using the system-specific
        packaging tools.
        """
        try:
            branch = self.config.get("node", "branch")
            if branch != "":
                branch = "/" + branch
        except Exception:
            branch = ""

        modname = 'rcUpdatePkg'+rcEnv.sysname
        if not os.path.exists(os.path.join(rcEnv.paths.pathlib, modname+'.py')):
            print("updatepkg not implemented on", rcEnv.sysname, file=sys.stderr)
            return 1
        mod = __import__(modname)
        if self.config.has_option('node', 'repopkg'):
            pkg_name = self.config.get('node', 'repopkg').strip('/') + \
                       "/" + mod.repo_subdir + branch + '/current'
        elif self.config.has_option('node', 'repo'):
            pkg_name = self.config.get('node', 'repo').strip('/') + \
                       "/packages/" + mod.repo_subdir + branch + '/current'
        else:
            print("node.repo or node.repopkg must be set in node.conf",
                  file=sys.stderr)
            return 1
        import tempfile
        tmpf = tempfile.NamedTemporaryFile()
        fpath = tmpf.name
        tmpf.close()
        print("get %s (%s)"%(pkg_name, fpath))
        try:
            self.urlretrieve(pkg_name, fpath)
        except IOError as exc:
            print("download failed", ":", exc, file=sys.stderr)
            try:
                os.unlink(fpath)
            except OSError:
                pass
            return 1
        print("updating opensvc")
        mod.update(fpath)
        print("clean up")
        try:
            os.unlink(fpath)
        except OSError:
            pass
        self.action("pushasset")
        self.build_services()
        return 0

    def array(self):
        """
        Execute a array command, passing extra_argv to the array driver.
        """
        array_name = None
        for idx, arg in enumerate(self.options.extra_argv):
            if arg.startswith("--array="):
                array_name = arg[arg.index("=")+1:]
                break
            if (arg == "-a" or arg == "--array") and idx+1 < len(self.options.extra_argv):
                array_name = self.options.extra_argv[idx+1]
                break

        if array_name is None:
            raise ex.excError("can not determine array driver (no --array)")

        self.load_auth_config()

        section = None

        for _section in self.auth_config.sections():
            if _section == array_name:
                section = _section
                break
            if self.auth_config.has_option(_section, "array") and \
               array_name in self.auth_config.get(_section, "array").split():
                section = _section
                break

        if section is None:
            raise ex.excError("array '%s' not found in %s" % (array_name, rcEnv.paths.authconf))

        if not self.auth_config.has_option(section, "type"):
            raise ex.excError("%s must have a '%s.type' option" % (rcEnv.paths.authconf, section))

        driver = self.auth_config.get(section, "type")
        rtype = driver[0].upper() + driver[1:].lower()
        modname = "rc" + rtype
        try:
            mod = __import__(modname)
        except ImportError as exc:
            raise ex.excError("driver %s load error: %s" % (modname, str(exc)))
        return mod.main(self.options.extra_argv, node=self)

    def get_ruser(self, node):
        """
        Returns the remote user to use for remote commands on the node
        specified as argument.
        If not specified as node.ruser in the node configuration file,
        the root user is returned.
        """
        default = "root"
        if not self.config.has_option('node', "ruser"):
            return default
        node_ruser = {}
        elements = self.config.get('node', 'ruser').split()
        for element in elements:
            subelements = element.split("@")
            if len(subelements) == 1:
                default = element
            elif len(subelements) == 2:
                _ruser, _node = subelements
                node_ruser[_node] = _ruser
            else:
                continue
        if node in node_ruser:
            return node_ruser[node]
        return default

    def dequeue_actions(self):
        """
        The dequeue_actions node action entrypoint.
        Poll the collector action queue until emptied.
        """
        actions = self.collector.call('collector_get_action_queue')
        if actions is None:
            return "unable to fetch actions scheduled by the collector"
        import re
        regex = re.compile(r"\x1b\[([0-9]{1,3}(;[0-9]{1,3})*)?[m|K|G]", re.UNICODE)
        data = []
        for action in actions:
            ret, out, err = self.dequeue_action(action)
            out = regex.sub('', out)
            err = regex.sub('', err)
            data.append((action.get('id'), ret, out, err))
        if len(actions) > 0:
            self.collector.call('collector_update_action_queue', data)

    @staticmethod
    def dequeue_action(action):
        """
        Execute the nodemgr or svcmgr action described in payload element
        received from the collector's action queue.
        """
        if action.get("svcname") is None or action.get("svcname") == "":
            cmd = [rcEnv.paths.nodemgr]
        else:
            cmd = [rcEnv.paths.svcmgr, "-s", action.get("svcname")]
        import shlex
        cmd += shlex.split(action.get("command", ""))
        print("dequeue action %s" % " ".join(cmd))
        out, err, ret = justcall(cmd)
        return ret, out, err

    def rotate_root_pw(self):
        """
        Generate a random password, send it to the collector and set it as
        the root user password.
        """
        passwd = self.genpw()

        from collector import Collector
        coll = Collector(self.options, self)
        try:
            getattr(coll, 'rotate_root_pw')(passwd)
        except Exception as exc:
            print("unexpected error sending the new password to the collector "
                  "(%s). Abording password change." % str(exc), file=sys.stderr)
            return 1

        try:
            mod = __import__('rcPasswd'+rcEnv.sysname)
        except ImportError:
            print("not implemented")
            return 1
        ret = mod.change_root_pw(passwd)
        if ret == 0:
            print("root password changed")
        else:
            print("failed to change root password")
        return ret

    @staticmethod
    def genpw():
        """
        Returns a random password.
        """
        import string
        chars = string.letters + string.digits + r'+/'
        assert 256 % len(chars) == 0
        pwd_len = 16
        return ''.join(chars[ord(c) % len(chars)] for c in os.urandom(pwd_len))

    def scanscsi(self):
        """
        Rescans the scsi host buses for new logical units discovery.
        """
        try:
            mod = __import__("rcDiskInfo"+rcEnv.sysname)
        except ImportError:
            print("scanscsi is not supported on", rcEnv.sysname, file=sys.stderr)
            return 1
        diskinfo = mod.diskInfo()
        if not hasattr(diskinfo, 'scanscsi'):
            print("scanscsi is not implemented on", rcEnv.sysname, file=sys.stderr)
            return 1
        return diskinfo.scanscsi(
            hba=self.options.hba,
            target=self.options.target,
            lun=self.options.lun,
        )

    def discover(self):
        """
        Auto configures services wrapping cloud compute instances
        """
        self.cloud_init()

    def cloud_init(self):
        """
        Initializes a cloud object for each cloud seaction in the configuration
        file.
        """
        ret = 0
        for section in self.config.sections():
            try:
                self.cloud_init_section(section)
            except ex.excInitError as exc:
                print(str(exc), file=sys.stderr)
                ret |= 1
        return ret

    def cloud_get(self, section):
        """
        Get the cloud object instance handling the config file section passed
        as argument. If not already instanciated, create the instance and
        store it in a dict hashed by section name.
        """
        if not section.startswith("cloud"):
            return

        if not section.startswith("cloud#"):
            raise ex.excInitError("cloud sections must have a unique name in "
                                  "the form '[cloud#n] in %s" % rcEnv.paths.nodeconf)

        if self.clouds and section in self.clouds:
            return self.clouds[section]

        if not self.config.has_option(section, "type"):
            raise ex.excInitError("type option is mandatory in cloud section "
                                  "in %s" % rcEnv.paths.nodeconf)
        cloud_type = self.config.get(section, 'type')

        if len(cloud_type) == 0:
            raise ex.excInitError("invalid cloud type in %s"%rcEnv.paths.nodeconf)

        self.load_auth_config()
        if not self.auth_config.has_section(section):
            raise ex.excInitError("%s must have a '%s' section" % (rcEnv.paths.authconf, section))

        auth_dict = {}
        for key, val in self.auth_config.items(section):
            auth_dict[key] = val

        mod_name = "rcCloud" + cloud_type[0].upper() + cloud_type[1:].lower()

        try:
            mod = __import__(mod_name)
        except ImportError:
            raise ex.excInitError("cloud type '%s' is not supported"%cloud_type)

        if self.clouds is None:
            self.clouds = {}
        cloud = mod.Cloud(section, auth_dict)
        self.clouds[section] = cloud
        return cloud

    def cloud_init_section(self, section):
        """
        Detects all cloud instances in the section, and init a service for each
        """
        cloud = self.cloud_get(section)

        if cloud is None:
            return

        cloud_id = cloud.cloud_id()
        svcnames = cloud.list_svcnames()

        self.cloud_purge_services(cloud_id, [x[1] for x in svcnames])

        for vmname, svcname in svcnames:
            self.cloud_init_service(cloud, vmname, svcname)

    @staticmethod
    def cloud_purge_services(suffix, svcnames):
        """
        Purge a lingering service no longer detected in the cloud.
        """
        import glob
        fpaths = glob.glob(os.path.join(rcEnv.paths.pathetc, '*.conf'))
        for fpath in fpaths:
            svcname = os.path.basename(fpath)[:-5]
            if svcname.endswith(suffix) and svcname not in svcnames:
                print("purge_service(svcname)", svcname)

    @staticmethod
    def cloud_init_service(cloud, vmname, svcname):
        """
        Init a service for a detected cloud instance.
        """
        import glob
        fpaths = glob.glob(os.path.join(rcEnv.paths.pathetc, '*.conf'))
        fpath = os.path.join(rcEnv.paths.pathetc, svcname+'.conf')
        if fpath in fpaths:
            print(svcname, "is already defined")
            return
        print("initialize", svcname)

        defaults = {
            "app": cloud.app_id(svcname),
            "mode": cloud.mode,
            "nodes": rcEnv.nodename,
            "service_type": "TST",
            "vm_name": vmname,
            "cloud_id": cloud.cid,
        }
        config = rcConfigParser.RawConfigParser(defaults)

        try:
            ofile = open(fpath, 'w')
            config.write(ofile)
            ofile.close()
        except:
            print("failed to write %s"%fpath, file=sys.stderr)
            raise Exception()

        basename = fpath[:-5]
        launchers_d = basename + '.dir'
        launchers_l = basename + '.d'
        try:
            os.makedirs(launchers_d)
        except OSError:
            pass
        try:
            os.symlink(launchers_d, launchers_l)
        except OSError:
            pass
        try:
            os.symlink(rcEnv.paths.svcmgr, basename)
        except OSError:
            pass

    def can_parallel(self, action, options):
        """
        Returns True if the action can be run in a subprocess per service
        """
        if options.parallel and action not in ACTIONS_NO_PARALLEL:
            return True
        return False

    @staticmethod
    def action_need_aggregate(action, options):
        """
        Returns True if the action returns data from multiple sources (nodes
        or services) to arrange for display.
        """
        if action.startswith("print_") and options.format == "json":
            return True
        if action.startswith("json_"):
            return True
        if action.startswith("collector_"):
            return True
        if "_json_" in action:
            return True
        return False

    def do_svcs_action(self, action, options):
        """
        The services action wrapper.
        Takes care of
        * parallelization of the action in per-service subprocesses
        * collection and aggregation of returned data and errors
        """
        if action == "ls":
            for svc in self.svcs:
                print(svc.svcname)
            return

        err = 0
        data = Storage()
        data.outs = {}
        need_aggregate = self.action_need_aggregate(action, options)

        # generic cache janitoring
        purge_cache_expired()
        self.log.debug("session uuid: %s", rcEnv.session_uuid)

        if action in ACTIONS_NO_MULTIPLE_SERVICES and len(self.svcs) > 1:
            print("action '%s' is not allowed on multiple services" % action, file=sys.stderr)
            return 1

        if self.can_parallel(action, options):
            from multiprocessing import Process
            if rcEnv.sysname == "Windows":
                from multiprocessing import set_executable
                set_executable(os.path.join(sys.exec_prefix, 'pythonw.exe'))
            data.procs = {}
            data.svcs = {}
            try:
                max_parallel = int(self._get("node.max_parallel"))
            except:
                max_parallel = 10

        def running():
            count = 0
            for proc in data.procs.values():
                if proc.is_alive():
                    count += 1
            return count

        for svc in self.svcs:
            if self.can_parallel(action, options):
                while running() >= max_parallel:
                    time.sleep(1)
                data.svcs[svc.svcname] = svc
                data.procs[svc.svcname] = Process(
                    target=self.service_action_worker,
                    name='worker_'+svc.svcname,
                    args=[svc, action, options],
                )
                data.procs[svc.svcname].start()
            else:
                try:
                    ret = svc.action(action, options)
                    if need_aggregate:
                        if ret is not None:
                            data.outs[svc.svcname] = ret
                    elif action.startswith("print_"):
                        self.print_data(ret)
                        ret = 0
                    else:
                        if ret is None:
                            ret = 0
                        elif isinstance(ret, list):
                            ret = 0
                        elif isinstance(ret, dict):
                            if "error" in ret:
                                print(ret["error"], file=sys.stderr)
                            else:
                                print("unsupported format for this action", file=sys.stderr)
                            ret = 1
                        err += ret
                except ex.excError as exc:
                    if not need_aggregate:
                        print("%s: %s" % (svc.svcname, exc), file=sys.stderr)
                    continue
                except ex.excSignal:
                    break

        if self.can_parallel(action, options):
            for svcname in data.procs:
                data.procs[svcname].join()
                ret = data.procs[svcname].exitcode
                if ret > 0:
                    # r is negative when data.procs[svcname] is killed by signal.
                    # in this case, we don't want to decrement the err counter.
                    err += ret

        if need_aggregate:
            if self.options.single_service:
                svcname = self.svcs[0].svcname
                if svcname not in data.outs:
                    return 1
                self.print_data(data.outs[svcname])
            else:
                self.print_data(data.outs)

        return err

    def collector_cli(self):
        """
        The collector cli entrypoint.
        """
        data = {}

        if os.getuid() == 0:
            if self.options.user is None:
                user, password = self.collector_auth_node()
                data["user"] = user
                data["password"] = password
            if self.options.api is None:
                if rcEnv.dbopensvc is None:
                    raise ex.excError("node.dbopensvc is not set in node.conf")
                data["api"] = rcEnv.dbopensvc.replace("/feed/default/call/xmlrpc", "/init/rest/api")
        from rcCollectorCli import Cli
        cli = Cli(**data)
        return cli.run()

    def collector_api(self, svcname=None):
        """
        Prepare the authentication info, either as node or as user.
        Fetch and cache the collector's exposed rest api metadata.
        """
        if rcEnv.dbopensvc is None:
            raise ex.excError("node.dbopensvc is not set in node.conf")
        data = {}
        if self.options.user is None:
            username, password = self.collector_auth_node()
            if svcname:
                username = svcname+"@"+username
        else:
            username, password = self.collector_auth_user()
        data["username"] = username
        data["password"] = password
        data["url"] = rcEnv.dbopensvc.replace("/feed/default/call/xmlrpc", "/init/rest/api")
        return data

    def collector_auth_node(self):
        """
        Returns the authentcation info for login as node
        """
        username = rcEnv.nodename
        if not self.config.has_option("node", "uuid"):
            raise ex.excError("the node is not registered yet. use 'nodemgr register [--user <user>]'")
        password = self.config.get("node", "uuid")
        return username, password

    def collector_auth_user(self):
        """
        Returns the authentcation info for login as user
        """
        username = self.options.user

        if self.options.password and self.options.password != "?":
            return username, self.options.password

        import getpass
        try:
            password = getpass.getpass()
        except EOFError:
            raise KeyboardInterrupt()
        return username, password

    def collector_request(self, path, svcname=None):
        """
        Make a request to the collector's rest api
        """
        import base64
        api = self.collector_api(svcname=svcname)
        url = api["url"]
        if not url.startswith("https"):
            raise ex.excError("refuse to submit auth tokens through a "
                              "non-encrypted transport")
        request = Request(url+path)
        auth_string = '%s:%s' % (api["username"], api["password"])
        if sys.version_info[0] >= 3:
            base64string = base64.encodestring(auth_string.encode()).decode()
        else:
            base64string = base64.encodestring(auth_string)
        base64string = base64string.replace('\n', '')
        request.add_header("Authorization", "Basic %s" % base64string)
        return request

    def collector_rest_get(self, path, data=None, svcname=None):
        """
        Make a GET request to the collector's rest api
        """
        return self.collector_rest_request(path, data=data, svcname=svcname)

    def collector_rest_post(self, path, data=None, svcname=None):
        """
        Make a POST request to the collector's rest api
        """
        return self.collector_rest_request(path, data, svcname=svcname, get_method="POST")

    def collector_rest_put(self, path, data=None, svcname=None):
        """
        Make a PUT request to the collector's rest api
        """
        return self.collector_rest_request(path, data, svcname=svcname, get_method="PUT")

    def collector_rest_delete(self, path, data=None, svcname=None):
        """
        Make a DELETE request to the collector's rest api
        """
        return self.collector_rest_request(path, data, svcname=svcname, get_method="DELETE")

    @staticmethod
    def set_ssl_context(kwargs):
        """
        Python 2.7.9+ verifies certs by default and support the creationn
        of an unverified context through ssl._create_unverified_context().
        This method add an unverified context to a kwargs dict, when
        necessary.
        """
        try:
            import ssl
            kwargs["context"] = ssl._create_unverified_context()
        except (ImportError, AttributeError):
            pass
        return kwargs

    def urlretrieve(self, url, fpath):
        """
        A chunked download method
        """
        request = Request(url)
        kwargs = {}
        kwargs = self.set_ssl_context(kwargs)
        ufile = urlopen(request, **kwargs)
        with open(fpath, 'wb') as ofile:
            for chunk in iter(lambda: ufile.read(4096), b""):
                ofile.write(chunk)
        ufile.close()

    def collector_rest_request(self, path, data=None, svcname=None, get_method="GET"):
        """
        Make a request to the collector's rest api
        """
        if data is not None and get_method == "GET":
            if len(data) == 0 or not isinstance(data, dict):
                data = None
            else:
                path += "?" + urlencode(data)
                data = None

        request = self.collector_request(path, svcname=svcname)
        if get_method:
            request.get_method = lambda: get_method
        if data is not None:
            try:
                request.add_data(urlencode(data))
            except AttributeError:
                request.data = urlencode(data).encode('utf-8')
        kwargs = {}
        kwargs = self.set_ssl_context(kwargs)
        try:
            ufile = urlopen(request, **kwargs)
        except HTTPError as exc:
            try:
                err = json.loads(exc.read())["error"]
                exc = ex.excError(err)
            except (ValueError, TypeError):
                pass
            raise exc
        except IOError as exc:
            if hasattr(exc, "reason"):
                raise ex.excError(exc.reason)
            raise ex.excError(str(exc))
        data = json.loads(ufile.read().decode("utf-8"))
        ufile.close()
        return data

    def collector_rest_get_to_file(self, path, fpath):
        """
        Download bulk chunked data from the collector's rest api
        """
        request = self.collector_request(path)
        kwargs = {}
        kwargs = self.set_ssl_context(kwargs)
        try:
            ufile = urlopen(request, **kwargs)
        except HTTPError as exc:
            try:
                err = json.loads(exc.read())["error"]
                exc = ex.excError(err)
            except ValueError:
                pass
            raise exc
        with open(fpath, 'wb') as ofile:
            for chunk in iter(lambda: ufile.read(4096), b""):
                ofile.write(chunk)
        ufile.close()

    def install_svc_conf_from_templ(self, svcname, template):
        """
        Download a provisioning template from the collector's rest api,
        and installs it as the service configuration file.
        """
        fpath = os.path.join(rcEnv.paths.pathetc, svcname+'.conf')
        try:
            int(template)
            url = "/provisioning_templates/"+str(template)+"?props=tpl_definition&meta=0"
        except ValueError:
            url = "/provisioning_templates?filters=tpl_name="+template+"&props=tpl_definition&meta=0"
        data = self.collector_rest_get(url)
        if "error" in data:
            raise ex.excError(data["error"])
        if len(data["data"]) == 0:
            raise ex.excError("service not found on the collector")
        if len(data["data"][0]["tpl_definition"]) == 0:
            raise ex.excError("service has an empty configuration")
        with open(fpath, "w") as ofile:
            ofile.write(data["data"][0]["tpl_definition"].replace("\\n", "\n").replace("\\t", "\t"))
        self.install_svc_conf_from_file(svcname, fpath)

    def install_svc_conf_from_uri(self, svcname, fpath):
        """
        Download a provisioning template from an arbitrary uri,
        and installs it as the service configuration file.
        """
        import tempfile
        tmpf = tempfile.NamedTemporaryFile()
        tmpfpath = tmpf.name
        tmpf.close()
        print("get %s (%s)" % (fpath, tmpfpath))
        try:
            self.urlretrieve(fpath, tmpfpath)
        except IOError as exc:
            print("download failed", ":", exc, file=sys.stderr)
            try:
                os.unlink(tmpfpath)
            except OSError:
                pass
        self.install_svc_conf_from_file(svcname, tmpfpath)

    @staticmethod
    def install_svc_conf_from_file(svcname, fpath):
        """
        Installs a local template as the service configuration file.
        """
        if not os.path.exists(fpath):
            raise ex.excError("%s does not exists" % fpath)

        import shutil

        # install the configuration file in etc/
        src_cf = os.path.realpath(fpath)
        dst_cf = os.path.join(rcEnv.paths.pathetc, svcname+'.conf')
        if dst_cf != src_cf:
            shutil.copy2(src_cf, dst_cf)

    def install_service(self, svcname, fpath=None, template=None):
        """
        Pick a collector's template, arbitrary uri, or local file service
        configuration file fetching method. Run it, and create the
        service symlinks and launchers directory.
        """
        if isinstance(svcname, list):
            if len(svcname) != 1:
                raise ex.excError("only one service must be specified")
            svcname = svcname[0]

        if fpath is None and template is None:
            return

        if fpath is not None and template is not None:
            raise ex.excError("--config and --template can't both be specified")

        if template is not None:
            if "://" in template:
                self.install_svc_conf_from_uri(svcname, template)
            elif os.path.exists(template):
                self.install_svc_conf_from_file(svcname, template)
            else:
                self.install_svc_conf_from_templ(svcname, template)
        else:
            if "://" in fpath:
                self.install_svc_conf_from_uri(svcname, fpath)
            else:
                self.install_svc_conf_from_file(svcname, fpath)

        self.install_service_files(svcname)

    @staticmethod
    def install_service_files(svcname):
        """
        Given a service name, install the symlink to svcmgr.
        """
        if rcEnv.sysname == 'Windows':
            return

        # install svcmgr link
        svcmgr_l = os.path.join(rcEnv.paths.pathetc, svcname)
        if not os.path.exists(svcmgr_l):
            Freezer(svcname).freeze()
            os.symlink(rcEnv.paths.svcmgr, svcmgr_l)
        elif os.path.realpath(rcEnv.paths.svcmgr) != os.path.realpath(svcmgr_l):
            os.unlink(svcmgr_l)
            os.symlink(rcEnv.paths.svcmgr, svcmgr_l)

    def set_rlimit(self):
        """
        Set the operating system nofile rlimit to a sensible value for the
        number of services configured.
        """
        nofile = 4096
        if self.svcs and isinstance(self.svcs, list):
            proportional_nofile = 64 * len(self.svcs)
            if proportional_nofile > nofile:
                nofile = proportional_nofile

        try:
            import resource
            _vs, _vg = resource.getrlimit(resource.RLIMIT_NOFILE)
            if _vs < nofile:
                self.log.debug("raise nofile resource from %d limit to %d", _vs, nofile)
                if nofile > _vg:
                    _vg = nofile
                resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, _vg))
            else:
                self.log.debug("current nofile %d already over minimum %d", _vs, nofile)
        except Exception as exc:
            self.log.debug(str(exc))

    def logs(self):
        """
        logs node action entrypoint.
        Read the node.log file, colorize its content and print.
        """
        if self.options.debug:
            logfile = os.path.join(rcEnv.paths.pathlog, "node.debug.log")
        else:
            logfile = os.path.join(rcEnv.paths.pathlog, "node.log")
        if not os.path.exists(logfile):
            return
        from rcColor import color, colorize

        def highlighter(line):
            """
            Colorize interesting parts to help readability
            """
            line = line.rstrip("\n")
            elements = line.split(" - ")

            if len(elements) < 3 or elements[2] not in ("DEBUG", "INFO", "WARNING", "ERROR"):
                # this is a log line continuation (command output for ex.)
                return line

            if len(elements[1]) > rcLogger.namelen:
                elements[1] = "*"+elements[1][-(rcLogger.namelen-1):]
            elements[1] = rcLogger.namefmt % elements[1]
            elements[1] = colorize(elements[1], color.BOLD)
            elements[2] = "%-7s" % elements[2]
            elements[2] = elements[2].replace("ERROR", colorize("ERROR", color.RED))
            elements[2] = elements[2].replace("WARNING", colorize("WARNING", color.BROWN))
            elements[2] = elements[2].replace("INFO", colorize("INFO", color.LIGHTBLUE))
            return " ".join(elements)

        pipe = sys.stdout
        if not self.options.nopager:
            try:
                pipe = os.popen('TERM=xterm less -R', 'w')
            except:
                pass

        try:
            for _logfile in [logfile+".1", logfile]:
                if not os.path.exists(_logfile):
                    continue
                with open(_logfile, "r") as ofile:
                    for line in ofile.readlines():
                        line = highlighter(line)
                        if line:
                            pipe.write(line+'\n')
        except BrokenPipeError:
            try:
                sys.stdout = os.fdopen(1)
            except (AttributeError, OSError, IOError):
                pass
        finally:
            if pipe != sys.stdout:
                pipe.close()

    @formatter
    def print_config_data(self, src_config):
        """
        Return a simple dict (OrderedDict if possible), fed with the
        service configuration sections and keys
        """
        try:
            from collections import OrderedDict
            best_dict = OrderedDict
        except ImportError:
            best_dict = dict

        config = best_dict()

        for section in src_config.sections():
            options = src_config.options(section)
            tmpsection = best_dict()
            for option in options:
                tmpsection[option] = src_config.get(section, option)
            config[section] = tmpsection
        return config

    def print_devs(self):
        if self.options.reverse:
            self.devtree.print_tree_bottom_up(devices=self.options.devices,
                                              verbose=self.options.verbose)
        else:
            self.devtree.print_tree(devices=self.options.devices,
                                    verbose=self.options.verbose)

    def print_config(self):
        """
        print_config node action entrypoint
        """
        if self.options.format is not None:
            return self.print_config_data(self.config)
        from rcColor import print_color_config
        print_color_config(rcEnv.paths.nodeconf)

    def print_authconfig(self):
        """
        print_authconfig node action entrypoint
        """
        if self.options.format is not None:
            self.load_auth_config()
            return self.print_config_data(self.auth_config)
        from rcColor import print_color_config
        print_color_config(rcEnv.paths.authconf)

    def agent_version(self):
        try:
            import version
        except ImportError:
            pass

        try:
            reload(version)
            return version.version
        except:
            pass

        try:
            import imp
            imp.reload(version)
            return version.version
        except:
            pass

        try:
            import importlib
            importlib.reload(version)
            return version.version
        except:
            pass
        if which("git"):
            cmd = ["git", "--git-dir", os.path.join(rcEnv.paths.pathsvc, ".git"),
                   "describe", "--tags", "--abbrev=0"]
            out, err, ret = justcall(cmd)
            if ret != 0:
                return "dev"
            _version = out.strip()
            cmd = ["git", "--git-dir", os.path.join(rcEnv.paths.pathsvc, ".git"),
                   "describe", "--tags"]
            out, err, ret = justcall(cmd)
            if ret != 0:
                return "dev"
            _release = out.strip().split("-")[1]
            return "-".join((_version, _release+"dev"))

        return "dev"

    def frozen(self):
        """
        Return True if the node frozen flag is set.
        """
        return self.freezer.node_frozen()

    def freeze(self):
        """
        Set the node frozen flag.
        """
        self.freezer.node_freeze()

    def thaw(self):
        """
        Unset the node frozen flag.
        """
        self.freezer.node_thaw()

    def stonith(self):
        self._stonith(self.options.node)

    def _stonith(self, node):
        if node in (None, ""):
            raise ex.excError("--node is mandatory")
        if node == rcEnv.nodename:
            raise ex.excError("refuse to stonith ourself")
        if node not in self.cluster_nodes:
            raise ex.excError("refuse to stonith node %s not member of our cluster" % node)
        try:
            cmd = self._get("stonith#%s.cmd" % node)
        except ex.excError as exc:
            raise ex.excError("the stonith#%s.cmd keyword must be set in "
                              "node.conf" % node)
        import shlex
        cmd = shlex.split(cmd)
        self.log.info("stonith node %s", node)
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError()

    def prepare_async_cmd(self):
        cmd = sys.argv[1:]
        cmd = drop_option("--node", cmd, drop_value=True)
        return cmd

    def async_action(self, action, timeout=None, wait=None):
        """
        Set the daemon global expected state if the action can be handled
        by the daemons.
        """
        if wait is None:
            wait = self.options.wait
        if timeout is None:
            timeout = self.options.time
        if timeout is not None:
            timeout = convert_duration(timeout)
        if self.options.node is not None and self.options.node != "" and \
           action in REMOTE_ACTIONS:
            cmd = self.prepare_async_cmd()
            ret = self.daemon_node_action(cmd)
            if ret == 0:
                raise ex.excAbortAction()
            else:
                raise ex.excError()
        states = {
            "freeze": "frozen",
            "thaw": "thawed",
        }
        if self.options.local:
            return
        if action not in states:
            return
        self.set_node_monitor(global_expect=states[action])
        self.log.info("%s action requested", action)
        if not wait:
            raise ex.excAbortAction()
        self.poll_async_action(states[action], timeout=timeout)

    def poll_async_action(self, state, timeout=None):
        """
        Display an asynchronous action progress until its end or timeout
        """
        prev_global_expect_set = set()

        for _ in range(timeout):
            data = self._daemon_status()
            if data is None:
                raise ex.excError("the daemon is not running")
            global_expect_set = []
            for nodename in data["monitor"]["nodes"]:
                try:
                    _data = data["monitor"]["nodes"][nodename]
                except (KeyError, TypeError) as exc:
                    continue
                if _data["monitor"].get("global_expect") == state:
                    global_expect_set.append(nodename)
            if prev_global_expect_set != global_expect_set:
                for nodename in set(global_expect_set) - prev_global_expect_set:
                    self.log.info(" work starting on %s", nodename)
                for nodename in prev_global_expect_set - set(global_expect_set):
                    self.log.info(" work over on %s", nodename)
            if not global_expect_set:
                self.log.info("final status: frozen=%s",
                              data["monitor"]["frozen"])
                return
            prev_global_expect_set = set(global_expect_set)
            time.sleep(1)
        raise ex.excError("wait timeout exceeded")

    #
    # daemon actions
    #
    def daemon_collector_xmlrpc(self, *args, **kwargs):
        data = self.daemon_send(
            {
                "action": "collector_xmlrpc",
                "options": {
                    "args": args,
                    "kwargs": kwargs,
                },
            },
            nodename=self.options.node,
            silent=True,
        )
        return data

    def _daemon_status(self, silent=False, refresh=False):
        data = self.daemon_send(
            {
                "action": "daemon_status",
                "options": {
                    "refresh": refresh,
                },
            },
            nodename=self.options.node,
            silent=silent,
        )
        return data

    def daemon_status(self, svcnames=None, preamble=""):
        data = self._daemon_status()
        if data is None or data.get("status", 0) != 0:
            return

        from rcColor import format_json, colorize, color, unicons
        if self.options.format == "json":
            format_json(data)
            return

        from rcStatus import Status, colorize_status
        out = []

        def get_nodes():
            try:
                hb_id = [thr_id for thr_id in data.keys() if thr_id.startswith("hb#")][0]
                hb = data[hb_id]
                return sorted(hb["peers"].keys())
            except:
                return [rcEnv.nodename]

        self.build_services(minimal=True)
        nodenames = get_nodes()
        services = {}
        if self.options.node:
            daemon_node = self.options.node
        else:
            daemon_node = rcEnv.nodename

        def load_header(title):
            line = [
                title,
                "",
                "",
                "",
            ]
            for nodename in nodenames:
                line.append(colorize(nodename, color.BOLD))
            out.append(line)

        def load_svc(svcname, data):
            if svcname not in services:
                return
            try:
                topology = services[svcname].topology
            except KeyError:
                topology = ""
            try:
                nodes = self.services[svcname].nodes
                drpnodes = self.services[svcname].drpnodes
            except KeyError:
                nodes = set()
                drpnodes = set()
            if rcEnv.nodename in drpnodes:
                topology = "drp " + topology
            status = colorize_status(data["avail"], lpad=0)
            if data["overall"] == "warn":
                status += colorize("!", color.BROWN)
            if data["placement"] == "non-optimal":
                status += colorize("^", color.RED)
            line = [
                " "+colorize(svcname, color.BOLD),
                status,
                topology,
                "|",
            ]
            for nodename in nodenames:
                if nodename in services[svcname]["nodes"]:
                    val = []
                    # frozen unicon
                    if services[svcname]["nodes"][nodename]["frozen"]:
                        frozen_icon = colorize(unicons["frozen"], color.BLUE)
                    else:
                        frozen_icon = ""
                    # avail status unicon
                    avail = services[svcname]["nodes"][nodename]["avail"]
                    if avail == "unknown":
                        avail_icon = colorize("?", color.RED)
                    else:
                        avail_icon = colorize_status(avail, lpad=0).replace(avail, unicons[avail])
                    # overall status unicon
                    overall = services[svcname]["nodes"][nodename]["overall"]
                    if overall == "warn":
                        overall_icon = colorize_status(overall, lpad=0).replace(overall, unicons[overall])
                    else:
                        overall_icon = ""
                    # mon status
                    smon = services[svcname]["nodes"][nodename]["mon"]
                    if smon == "idle":
                        # don't display 'idle', as its to normal status and thus repeated as nauseam
                        smon = ""
                    else:
                        smon = " " + smon
                    # leader
                    if services[svcname]["nodes"][nodename]["placement"] == "leader":
                        leader = colorize("^", color.LIGHTBLUE)
                    else:
                        leader = ""
                    # provisioned
                    if services[svcname]["nodes"][nodename]["provisioned"] is False:
                        provisioned = colorize("P", color.RED)
                    else:
                        provisioned = ""

                    val.append(avail_icon)
                    val.append(overall_icon)
                    val.append(leader)
                    val.append(frozen_icon)
                    val.append(provisioned)
                    val.append(smon)
                    line.append("".join(val))
                elif nodename not in nodes | drpnodes:
                    line.append("")
                else:
                    line.append(colorize("?", color.RED))
            out.append(line)

        def load_hb(key, _data):
            if _data["state"] == "running":
                state = colorize(_data["state"], color.GREEN)
            else:
                state = colorize(_data["state"], color.RED)
            if "addr" in _data["config"] and "port" in _data["config"]:
                config = _data["config"]["addr"]+":"+str(_data["config"]["port"])
            elif "dev" in _data["config"]:
                config = os.path.basename(_data["config"]["dev"])
            elif "relay" in _data["config"]:
                config = _data["config"]["relay"]
            else:
                config = ""
            line = [
                " "+colorize(key, color.BOLD),
                state,
                config,
                "|",
            ]
            for nodename in nodenames:
                if nodename not in _data["peers"] or "beating" not in _data["peers"][nodename]:
                    status = "n/a"
                elif _data["peers"][nodename]["beating"]:
                    status = "up"
                else:
                    status = "down"
                status = colorize_status(status, lpad=0).replace(status, unicons[status])
                line.append(status)
            out.append(line)

        def load_listener(key, _data):
            if _data["state"] == "running":
                state = colorize(_data["state"], color.GREEN)
            else:
                state = colorize(_data["state"], color.RED)
            out.append((
                " "+colorize(key, color.BOLD),
                state,
                _data["config"]["addr"]+":"+str(_data["config"]["port"]),
            ))

        def load_thread(key, _data):
            if _data["state"] == "running":
                state = colorize(_data["state"], color.GREEN)
            else:
                state = colorize(_data["state"], color.RED)
            out.append((
                " "+colorize(key, color.BOLD),
                state,
            ))

        if sys.version_info[0] < 3:
            pad = " "
            def print_bytes(val):
                print(val)
            def bare_len(val):
                import re
                ansi_escape = re.compile(r'\x1b[^m]*m')
                val = ansi_escape.sub('', val)
                val = bytes(val).decode("utf-8")
                return len(val)
        else:
            pad = b" "
            def print_bytes(val):
                print(val.decode("utf-8"))
            def bare_len(val):
                import re
                ansi_escape = re.compile(b'\x1b[^m]*m')
                val = ansi_escape.sub(b'', val)
                val = bytes(val).decode("utf-8")
                return len(val)

        def list_print(data):
            if len(data) == 0:
                return
            widths = [0] * len(data[0])
            _data = []
            for line in data:
                _data.append(tuple(map(lambda x: x.encode("utf-8") if x is not None else "".encode("utf-8"), line)))
            for line in _data:
                for i, val in enumerate(line):
                    strlen = bare_len(val)
                    if strlen > widths[i]:
                        widths[i] = strlen
            for line in _data:
                _line = []
                for i, val in enumerate(line):
                    val = val + pad*(widths[i]-bare_len(val))
                    _line.append(val)
                _line = pad.join(_line)
                print_bytes(_line)

        def print_section(data):
            if len(data) == 0:
                return
            list_print(data)

        def load_threads():
            for key in sorted(list(data.keys())):
                if key.startswith("hb#"):
                    load_hb(key, data[key])
                elif key == "listener":
                    load_listener(key, data[key])
                else:
                    load_thread(key, data[key])

        def load_metrics():
            line = [
                colorize(" 15m", color.BOLD),
                "",
                "",
                "|",
            ]
            for nodename in nodenames:
                line.append(str(data["monitor"]["nodes"].get(nodename, {}).get("load", {}).get("15m", "")))
            out.append(line)

        def load_node_state():
            line = [
                colorize(" state", color.BOLD),
                "",
                "",
                "|",
            ]
            for nodename in nodenames:
                nmon_state = data["monitor"]["nodes"].get(nodename, {}).get("monitor", {}).get("status", "")
                if nmon_state == "idle":
                    nmon_state = ""
                if data["monitor"]["nodes"].get(nodename, {}).get("frozen", ""):
                    frozen = frozen_icon = colorize(unicons["frozen"], color.BLUE)
                else:
                    frozen = ""
                line.append(str(nmon_state)+frozen)
            out.append(line)

        def load_node_compat():
            if data["monitor"].get("compat") is True:
                # no need to clutter if the situation is normal
                return
            line = [
                colorize(" compat", color.BOLD),
                colorize("warn", color.BROWN),
                "",
                "|",
            ]
            for nodename in nodenames:
                compat = data["monitor"]["nodes"].get(nodename, {}).get("compat", "")
                line.append(str(compat))
            out.append(line)

        # init the services hash
        for node in nodenames:
            if node not in data["monitor"]["nodes"]:
                continue
            for svcname, _data in data["monitor"]["nodes"][node]["services"]["status"].items():
                #if svcname not in data["monitor"]["nodes"][rcEnv.nodename]["services"]["status"]:
                #    # no local instance
                #    continue
                if svcnames and svcname not in svcnames:
                    continue
                if svcname not in services:
                    services[svcname] = Storage({
                        "topology": _data.get("topology", ""),
                        "avail": Status(),
                        "overall": "",
                        "nodes": {}
                    })
                services[svcname].nodes[node] = {
                    "avail": _data.get("avail", "undef"),
                    "overall": _data.get("overall", "undef"),
                    "frozen": _data.get("frozen", False),
                    "mon": _data["monitor"]["status"],
                    "placement": _data["monitor"].get("placement", ""),
                    "provisioned": _data.get("provisioned"),
                }
        for svcname, _data in data["monitor"]["services"].items():
            if svcnames and svcname not in svcnames:
                continue
            if svcname not in services:
                services[svcname] = Storage({
                    "avail": Status(),
                    "overall": "",
                    "nodes": {}
                })
            services[svcname].avail = _data["avail"]
            services[svcname].overall = _data["overall"]
            services[svcname].placement = _data["placement"]

        # load data in lists
        load_header("Threads")
        load_threads()
        out.append([])
        load_header("Nodes")
        load_metrics()
        load_node_compat()
        load_node_state()
        out.append([])
        load_header("Services")
        for svcname in sorted(list(services.keys())):
            load_svc(svcname, services[svcname])

        # print tabulated lists
        print(preamble)
        print_section(out)

    def daemon_blacklist_clear(self):
        """
        Tell the daemon to clear the senders blacklist
        """
        data = self.daemon_send(
            {"action": "daemon_blacklist_clear"},
            nodename=self.options.node,
        )
        print(json.dumps(data, indent=4, sort_keys=True))

    def daemon_blacklist_status(self):
        """
        Show the daemon senders blacklist
        """
        data = self.daemon_send(
            {"action": "daemon_blacklist_status"},
            nodename=self.options.node,
        )
        print(json.dumps(data, indent=4, sort_keys=True))

    def _ping(self, node):
        """
        Fetch the daemon senders blacklist as a ping test, from either
        a peer or an arbitrator node, swiching between secrets as appropriate.
        """
        if node not in self.cluster_nodes:
            secret = None
            for section in self.config.sections():
                if not section.startswith("arbitrator#"):
                    continue
                try:
                    arbitrator = self.config.get(section, "name")
                except Exception:
                    continue
                if arbitrator != node:
                    continue
                try:
                    secret = self.config.get(section, "secret")
                    cluster_name = "join"
                    break
                except Exception:
                    self.log.warning("missing 'secret' in configuration section %s" % section)
                    continue
            if secret is None:
                raise ex.excError("unable to find a secret for node '%s': neither in cluster.nodes nor arbitrator#*.name" % node)
        else:
            cluster_name = None
            secret = None
        data = self.daemon_send(
            {"action": "daemon_blacklist_status"},
            nodename=node,
            cluster_name=cluster_name,
            secret=secret,
        )
        if data is None or "status" not in data or data["status"] != 0:
            return 1
        return 0

    def ping(self):
        try:
            ret = self._ping(self.options.node)
        except ex.excError as exc:
            print(exc)
            ret = 2
        if ret == 0:
            print("%s is alive" % self.options.node)
        elif ret == 1:
            print("%s is not alive" % self.options.node)
        return ret

    def systemd_restart(self):
        if not which("systemctl"):
            return False
        out, err, ret = justcall(["runlevel"])
        if ret != 0:
            return False
        last, current = out.split()
        if current in ("0", "1", "6"):
            return False
        return True

    def daemon_shutdown(self):
        if self.systemd_restart():
            # called by "systemd restart opensvc-agent", which can be triggered
            # * directly
            # * via "nodemgr daemon restart"
            # detect these case and don't stop the services
            self.log.info("requalify shutdown as stop")
            data = self._daemon_stop()
            if data is None:
                return
            if data.get("status") == 0:
                return
            return data
        self.log.info("really shutdown")
        return self._daemon_shutdown()

    def _daemon_shutdown(self):
        """
        Tell the daemon to shutdown all local service instances then die.
        """
        data = self.daemon_send(
            {"action": "daemon_shutdown"},
            nodename=self.options.node,
        )
        if data is None:
            raise ex.excError
        print(json.dumps(data, indent=4, sort_keys=True))

    def _daemon_stop(self):
        """
        Tell the daemon to die or stop a specified thread.
        """
        options = {}
        if self.options.thr_id:
            options["thr_id"] = self.options.thr_id
        data = self.daemon_send(
            {"action": "daemon_stop", "options": options},
            nodename=self.options.node,
        )
        while True:
            if not self._daemon_running():
                break
            time.sleep(0.1)
        return data

    def daemon_stop(self):
        data = None
        if self.options.thr_id is None and self.daemon_handled_by_systemd():
            # 'systemctl restart <osvcunit>' when the daemon has been started
            # manually causes a direct 'nodemgr daemon start', which fails,
            # and systemd fallbacks to 'nodemgr daemon stop' and leaves the
            # daemon stopped.
            # Detect this situation and stop the daemon ourselves.
            if self.daemon_active_systemd():
                data = self.daemon_stop_systemd()
            else:
                if self._daemon_running():
                    data = self._daemon_stop()
        elif self._daemon_running():
            data = self._daemon_stop()
        if data is None:
            return
        if data.get("status") == 0:
            return
        raise ex.excError(json.dumps(data, indent=4, sort_keys=True))

    def daemon_start(self):
        if self.options.thr_id:
            return self.daemon_start_thread()
        if self.daemon_handled_by_systemd():
            return self.daemon_start_systemd()
        return os.system(sys.executable+" "+os.path.join(rcEnv.paths.pathlib, "osvcd.py"))

    def daemon_start_thread(self):
        options = {}
        options["thr_id"] = self.options.thr_id
        data = self.daemon_send(
            {"action": "daemon_start", "options": options},
            nodename=self.options.node,
        )
        if data.get("status") == 0:
            return
        raise ex.excError(json.dumps(data, indent=4, sort_keys=True))

    def daemon_running(self):
        if self._daemon_running():
            return
        else:
            raise ex.excError

    def _daemon_running(self):
        if self.options.thr_id:
            data = self._daemon_status()
            if self.options.thr_id not in data:
                return False
            return data[self.options.thr_id].get("state") == "running"
        from lock import lock, unlock
        lockfd = None
        try:
            lockfd = lock(lockfile=rcEnv.paths.daemon_lock, timeout=0, delay=0)
        except:
            return True
        finally:
            unlock(lockfd)
        return False

    def daemon_start_systemd(self):
        """
        Do daemon start through the systemd, so that the daemon is
        service status is correctly reported.
        """
        os.system("systemctl reset-failed opensvc-agent")
        os.system("systemctl start opensvc-agent")

    def daemon_stop_systemd(self):
        """
        Do daemon stop through the systemd, so that the daemon is not restarted
        by systemd, and the systemd service status is correctly reported.
        """
        os.system("systemctl stop opensvc-agent")

    def daemon_restart_systemd(self):
        """
        Do daemon restart through the systemd, so that the daemon pid is updated
        in systemd, and the systemd service status is correctly reported.
        """
        os.system("systemctl restart opensvc-agent")

    def daemon_handled_by_systemd(self):
        """
        Return True if the system has systemd.
        """
        if which("systemctl") is None:
            return False
        if os.environ.get("LOGNAME") is None:
            # do as if we're not handled by systemd if we're already run by
            # systemd
            return False
        return True

    def daemon_active_systemd(self):
        cmd = ["systemctl", "show", "-p" "ActiveState", "opensvc-agent.service"]
        out, err, ret = justcall(cmd)
        if ret != 0:
            return False
        if out.split("=")[-1].strip() == "active":
            return True
        return False

    def daemon_restart(self):
        if self.daemon_handled_by_systemd():
            # 'systemctl restart <osvcunit>' when the daemon has been started
            # manually causes a direct 'nodemgr daemon start', which fails,
            # and systemd fallbacks to 'nodemgr daemon stop' and leaves the
            # daemon stopped.
            # Detect this situation and stop the daemon ourselves.
            if not self.daemon_active_systemd():
                if self._daemon_running():
                    self._daemon_stop()
            return self.daemon_restart_systemd()
        if self._daemon_running():
            self._daemon_stop()
        self.daemon_start()

    @lazy
    def sorted_cluster_nodes(self):
        return sorted(self.cluster_nodes)

    def daemon_leave(self):
        cluster_nodes = self.sorted_cluster_nodes
        if len(self.sorted_cluster_nodes) == 0:
            self.log.info("local node is not member of a cluster")
            return
        if rcEnv.nodename in cluster_nodes:
            cluster_nodes.remove(rcEnv.nodename)

        # freeze and remember the initial frozen state
        initially_frozen = self.frozen()
        if not initially_frozen:
            self.freeze()
            self.log.info("freeze local node")
        else:
            self.log.info("local node is already frozen")

        # leave other nodes
        options = {"thr_id": "tx", "wait": True}
        data = self.daemon_send(
            {"action": "daemon_stop", "options": options},
            nodename=rcEnv.nodename,
        )

        errors = 0
        for nodename in cluster_nodes:
            data = self.daemon_send(
                {"action": "leave"},
                nodename=nodename,
            )
            if data is None:
                self.log.error("leave node %s failed", nodename)
                errors += 1
            else:
                self.log.info("leave node %s", nodename)

        # remove obsolete hb configurations
        for section in self.config.sections():
            if section.startswith("hb#") or \
               section.startswith("arbitrator#"):
                self.log.info("remove configuration %s", section)
                self.config.remove_section(section)
            self.config.remove_section("cluster")

        self.write_config()
        unset_lazy(self, "cluster_nodes")

        # leave node frozen if initially frozen or we failed joining all nodes
        if initially_frozen:
            self.log.warning("local node is left frozen as it was already before leave")
        elif errors > 0:
            self.log.warning("local node is left frozen due to leave errors")
        else:
            self.thaw()
            self.log.info("thaw local node")

    def daemon_join(self):
        if self.options.secret is None:
            raise ex.excError("--secret must be set")
        if self.options.node is None:
            raise ex.excError("--node must be set")

        # freeze and remember the initial frozen state
        initially_frozen = self.frozen()
        if not initially_frozen:
            self.freeze()
            self.log.info("freeze local node")
        else:
            self.log.info("local node is already frozen")

        if not self.config.has_section("cluster"):
            self.config.add_section("cluster")
        data = self.daemon_send(
            {"action": "join"},
            nodename=self.options.node,
            cluster_name="join",
            secret=self.options.secret,
        )
        if data is None:
            raise ex.excError("join node %s failed" % self.options.node)
        data = data.get("data")
        if data is None:
            raise ex.excError("join failed: no data in response")
        cluster_name = data.get("cluster", {}).get("name")
        if cluster_name is None:
            raise ex.excError("join failed: no cluster.name in response")
        cluster_nodes = data.get("cluster", {}).get("nodes")
        if cluster_nodes is None:
            raise ex.excError("join failed: no cluster.nodes in response")
        if not isinstance(cluster_nodes, list):
            raise ex.excError("join failed: cluster.nodes value is not a list")
        quorum = data.get("cluster", {}).get("quorum", False)

        self.config.set("cluster", "name", cluster_name)
        self.config.set("cluster", "nodes", " ".join(cluster_nodes))
        self.config.set("cluster", "secret", self.options.secret)
        if quorum:
            self.config.set("cluster", "quorum", "true")
        elif self.config.has_option("cluster", "quorum"):
            self.config.remove_option("cluster", "quorum")

        for section, _data in data.items():
            if section.startswith("hb#"):
                if self.config.has_section(section):
                    self.log.info("update heartbeat %s", section)
                    self.config.remove_section(section)
                else:
                    self.log.info("add heartbeat %s", section)
                self.config.add_section(section)
                for option, value in _data.items():
                    self.config.set(section, option, value)
            elif section.startswith("stonith#"):
                if self.config.has_section(section):
                    self.log.info("update stonith %s", section)
                    self.config.remove_section(section)
                else:
                    self.log.info("add stonith %s", section)
                self.config.add_section(section)
                for option, value in _data.items():
                    self.config.set(section, option, value)
            elif section.startswith("arbitrator#"):
                if self.config.has_section(section):
                    self.log.info("update arbitrator %s", section)
                    self.config.remove_section(section)
                else:
                    self.log.info("add arbitrator %s", section)
                self.config.add_section(section)
                for option, value in _data.items():
                    self.config.set(section, option, value)

        # remove obsolete hb configurations
        for section in self.config.sections():
            if section.startswith("hb#") and section not in data:
                self.log.info("remove heartbeat %s", section)
                self.config.remove_section(section)

        # remove obsolete stonith configurations
        for section in self.config.sections():
            if section.startswith("stonith#") and section not in data:
                self.log.info("remove stonith %s", section)
                self.config.remove_section(section)

        # remove obsolete arbitrator configurations
        for section in self.config.sections():
            if section.startswith("arbitrator#") and section not in data:
                self.log.info("remove arbitrator %s", section)
                self.config.remove_section(section)

        self.write_config()
        self.log.info("join node %s", self.options.node)

        # join other nodes
        errors = 0
        for nodename in cluster_nodes:
            if nodename in (rcEnv.nodename, self.options.node):
                continue
            data = self.daemon_send(
                {"action": "join"},
                nodename=nodename,
                cluster_name="join",
                secret=self.options.secret,
            )
            if data is None:
                self.log.error("join node %s failed", nodename)
                errors += 1
            else:
                self.log.info("join node %s", nodename)

        # leave node frozen if initially frozen or we failed joining all nodes
        if initially_frozen:
            self.log.warning("local node is left frozen as it was already before join")
        elif errors > 0:
            self.log.warning("local node is left frozen due to join errors")
        else:
            self.thaw()
            self.log.info("thaw local node")

    def set_node_monitor(self, status=None, local_expect=None, global_expect=None):
        options = {
            "status": status,
            "local_expect": local_expect,
            "global_expect": global_expect,
        }
        try:
            data = self.daemon_send(
                {"action": "set_node_monitor", "options": options},
                nodename=self.options.node,
                silent=True,
            )
            if data is None:
                raise ex.excError("the daemon is not running")
            if data and data["status"] != 0:
                raise ex.excError("set monitor status failed")
        except Exception as exc:
            raise ex.excError("set monitor status failed: %s" % str(exc))

    def daemon_node_action(self, cmd, nodename=None, sync=True):
        """
        Execute a node action on a peer node.
        If sync is set, wait for the action result.
        """
        if nodename is None:
            nodename = self.options.node
        options = {
            "cmd": cmd,
            "sync": sync,
        }
        self.log.info("request node action '%s' on node %s", " ".join(cmd), nodename)
        try:
            data = self.daemon_send(
                {"action": "node_action", "options": options},
                nodename=nodename,
                silent=True,
            )
        except Exception as exc:
            self.log.error("node action on node %s failed: %s",
                           nodename, exc)
            return 1
        if data is None or data["status"] != 0:
            self.log.error("node action on node %s failed",
                           nodename)
            return 1
        if "data" not in data:
            return 0
        data = data["data"]
        if data.get("out") and len(data["out"]) > 0:
            for line in data["out"].splitlines():
               print(line)
        if data.get("err") and len(data["err"]) > 0:
            for line in data["err"].splitlines():
               print(line, file=sys.stderr)
        return data["ret"]

    def network_data(self):
        import glob
        import re
        nets = {}
        for net in glob.glob("/opt/cni/net.d/*.conf"):
            try:
                with open(net, "r") as ofile:
                    data = json.load(ofile)
            except ValueError:
                continue
            if data.get("type") == "portmap":
                continue
            net = os.path.basename(net)
            net = re.sub(".conf$", "", net)
            nets[net] = data
        return nets

    @formatter
    def network_ls(self):
        nets = self.network_data()
        if self.options.format == "json":
            return nets
        print("\n".join([net for net in nets]))

    @formatter
    def network_show(self):
        nets = self.network_data()
        if self.options.format == "json":
            if self.options.id not in nets:
                return {}
            return nets[self.options.id]
        if self.options.id not in nets:
            return
        from forest import Forest
        from rcColor import color
        tree = Forest()
        tree.load({self.options.id: nets[self.options.id]}, title=rcEnv.nodename)
        print(tree)

    #########################################################################
    #
    # config helpers
    #
    #########################################################################
    def handle_reference(self, ref, scope=False, impersonate=None, config=None):
            # hardcoded references
            if ref == "nodename":
                return rcEnv.nodename
            if ref == "short_nodename":
                return rcEnv.nodename.split(".")[0]
            if ref == "clusternodes":
                return " ".join(self.cluster_nodes)
            if ref == "svcmgr":
                return rcEnv.paths.svcmgr
            if ref == "nodemgr":
                return rcEnv.paths.nodemgr
            if ref == "etc":
                return rcEnv.paths.pathetc
            if ref == "var":
                return rcEnv.paths.pathvar

            if "[" in ref and ref.endswith("]"):
                i = ref.index("[")
                index = ref[i+1:-1]
                ref = ref[:i]
                index = int(self.handle_references(index, scope=scope,
                                                   impersonate=impersonate))
            else:
                index = None

            n_dots = ref.count(".")
            if n_dots == 1:
                _section, _v = ref.split(".")
            else:
                raise ex.excError("%s: reference can have only one dot" % ref)

            if len(_section) == 0:
                raise ex.excError("%s: reference section can not be empty" % ref)
            if len(_v) == 0:
                raise ex.excError("%s: reference option can not be empty" % ref)

            if _v[0] == "#":
                return_length = True
                _v = _v[1:]
            else:
                return_length = False

            val = self._handle_reference(ref, _section, _v, scope=scope,
                                         impersonate=impersonate,
                                         config=config)

            if val is None:
                # deferred
                return

            if return_length or index is not None:
                if is_string(val):
                    val = val.split()
                if return_length:
                    return str(len(val))
                if index is not None:
                    try:
                        return val[index]
                    except IndexError:
                        if _v in ("exposed_devs", "sub_devs", "base_devs"):
                            return
                        raise

            return val

    def _handle_reference(self, ref, _section, _v, scope=False,
                          impersonate=None, config=None):
        if config is None:
            config = self.config
        # give os env precedence over the env cf section
        if _section == "env" and _v.upper() in os.environ:
            return os.environ[_v.upper()]

        if _section != "DEFAULT" and not config.has_section(_section):
            raise ex.excError("%s: section %s does not exist" % (ref, _section))

        try:
            return self.conf_get(_section, _v, "string", scope=scope,
                                 impersonate=impersonate, config=config)
        except ex.OptNotFound as exc:
            return exc.default
        except ex.RequiredOptNotFound:
            raise ex.excError("%s: unresolved reference (%s)"
                              "" % (ref, str(exc)))

        raise ex.excError("%s: unknown reference" % ref)

    def _handle_references(self, s, scope=False, impersonate=None, config=None):
        if not is_string(s):
            return s
        while True:
            m = re.search(r'{\w*[\w#][\w\.\[\]]*}', s)
            if m is None:
                return s
            ref = m.group(0).strip("{}")
            val = self.handle_reference(ref, scope=scope,
                                        impersonate=impersonate,
                                        config=config)
            if val is None:
                # deferred
                return
            s = s[:m.start()] + val + s[m.end():]

    @staticmethod
    def _handle_expressions(s):
        if not is_string(s):
            return s
        while True:
            m = re.search(r'\$\((.+)\)', s)
            if m is None:
                return s
            expr = m.group(1)
            try:
                val = eval_expr(expr)
            except TypeError as exc:
                raise ex.excError("invalid expression: %s: %s" % (expr, str(exc)))
            s = s[:m.start()] + str(val) + s[m.end():]

    def handle_references(self, s, scope=False, impersonate=None, config=None):
        key = (s, scope, impersonate)
        if hasattr(self, "ref_cache") and self.ref_cache is not None \
           and key in self.ref_cache:
            return self.ref_cache[key]
        try:
            val = self._handle_references(s, scope=scope,
                                          impersonate=impersonate,
                                          config=config)
            val = self._handle_expressions(val)
            val = self._handle_references(val, scope=scope,
                                          impersonate=impersonate,
                                          config=config)
        except Exception as e:
            raise
            raise ex.excError("%s: reference evaluation failed: %s"
                              "" % (s, str(e)))
        if hasattr(self, "ref_cache") and self.ref_cache is not None:
            self.ref_cache[key] = val
        return val

    def conf_get(self, s, o, t=None, scope=None, impersonate=None,
                 use_default=True, config=None, verbose=True):
        """
        Handle keyword and section deprecation.
        """
        section = s.split("#")[0]
        if section in nodedict.DEPRECATED_SECTIONS:
            section, rtype = nodedict.DEPRECATED_SECTIONS[section]
            fkey = ".".join((section, rtype, o))
        else:
            try:
                rtype = self.config.get(s, "type")
                fkey = ".".join((section, rtype, o))
            except Exception:
                rtype = None
                fkey = ".".join((section, o))

        deprecated_keyword = nodedict.REVERSE_DEPRECATED_KEYWORDS.get(fkey)

        # 1st try: supported keyword
        try:
            return self._conf_get(s, o, t=t, scope=scope,
                                  impersonate=impersonate,
                                  use_default=use_default, config=config,
                                  section=section, rtype=rtype)
        except ex.RequiredOptNotFound:
            if deprecated_keyword is None:
                if verbose:
                    self.log.error("%s.%s is mandatory" % (s, o))
                raise
        except ex.OptNotFound:
            if deprecated_keyword is None:
                raise

        # 2nd try: deprecated keyword
        try:
            return self._conf_get(s, deprecated_keyword, t=t, scope=scope,
                                  impersonate=impersonate,
                                  use_default=use_default, config=config,
                                  section=section, rtype=rtype)
        except ex.RequiredOptNotFound:
            self.log.error("%s.%s is mandatory" % (s, o))
            raise

    def _conf_get(self, s, o, t=None, scope=None, impersonate=None,
                 use_default=True, config=None, section=None, rtype=None):
        """
        Get keyword properties and handle inheritance.
        """
        inheritance = "leaf"
        kwargs = {
            "default": None,
            "required": False,
            "deprecated": False,
            "impersonate": impersonate,
            "use_default": use_default,
            "config": config,
        }
        if s != "env":
            key = nodedict.NODEKEYS[section].getkey(o, rtype)
            if key is None:
                if scope is None and t is None:
                    raise ValueError("%s.%s not found in the node "
                                     "configuration dictionary" % (s, o))
                else:
                    # passing 't' and 'scope' skips NODEKEYS validation.
                    # used for keywords not in NODEKEYS.
                    pass
            else:
                kwargs["deprecated"] = (key.keyword != o)
                kwargs["required"] = key.required
                kwargs["default"] = key.default
                inheritance = key.inheritance
                if scope is None:
                    scope = key.at
                if t is None:
                    t = key.convert
        else:
            # env key are always string and scopable
            t = "string"
            scope = True

        if scope is None:
            scope = False
        if t is None:
            t = "string"

        kwargs["scope"] = scope
        kwargs["t"] = t

        # in order of probability
        if inheritance == "leaf > head":
            return self.__conf_get(s, o, **kwargs)
        if inheritance == "leaf":
            kwargs["use_default"] = False
            return self.__conf_get(s, o, **kwargs)
        raise ex.excError("unsupported inheritance value: %s" % str(inheritance))

    def __conf_get(self, s, o, t=None, scope=None, impersonate=None,
                   use_default=None, config=None, default=None, required=None,
                   deprecated=None):
        try:
            if not scope:
                val = self.conf_get_val_unscoped(s, o, use_default=use_default,
                                                 config=config)
            else:
                val = self.conf_get_val_scoped(s, o, use_default=use_default,
                                               config=config,
                                               impersonate=impersonate)
        except ex.OptNotFound as exc:
            if required:
                raise ex.RequiredOptNotFound
            else:
                exc.default = default
                raise exc

        try:
            val = self.handle_references(val, scope=scope,
                                         impersonate=impersonate,
                                         config=config)
        except ex.excError as exc:
            if o.startswith("pre_") or o.startswith("post_") or \
               o.startswith("blocking_"):
                pass
            else:
                raise

        if t in (None, "string"):
            return val
        return globals()["convert_"+t](val)

    def conf_get_val_unscoped(self, s, o, use_default=True, config=None):
        if config is None:
            config = self.config
        if config.has_option(s, o):
            return config.get(s, o)
        raise ex.OptNotFound("unscoped keyword %s.%s not found." % (s, o))

    def conf_has_option_scoped(self, s, o, impersonate=None, config=None, scope_order=None):
        """
        Handles the keyword scope_order property, at and impersonate
        """
        if config is None:
            config = self.config
        if impersonate is None:
            nodename = rcEnv.nodename
        else:
            nodename = impersonate

        if s != "DEFAULT" and not config.has_section(s):
            return

        if s == "DEFAULT":
            options = config.defaults().keys()
        else:
            options = config._sections[s].keys()

        candidates = [
            (o+"@"+nodename, True),
            (o, True),
        ]

        if scope_order == "head: generic > specific" and s == "DEFAULT":
            candidates.reverse()
        elif scope_order == "generic > specific":
            candidates.reverse()

        for option, condition in candidates:
            if option in options and condition:
                return option

    def conf_get_val_scoped(self, s, o, impersonate=None, use_default=True, config=None, scope_order=None):
        if config is None:
            config = self.config
        if impersonate is None:
            nodename = rcEnv.nodename
        else:
            nodename = impersonate

        option = self.conf_has_option_scoped(s, o, impersonate=impersonate,
                                             config=config,
                                             scope_order=scope_order)
        if option is None:
            raise ex.OptNotFound("scoped keyword %s.%s not found." % (s, o))

        try:
            val = config.get(s, option)
        except Exception as e:
            raise ex.excError("param %s.%s: %s"%(s, o, str(e)))

        return val

    def validate_config(self, path=None):
        """
        The validate config action entrypoint.
        """
        ret = self._validate_config(path=path)
        return ret["warnings"] + ret["errors"]

    def _validate_config(self, path=None):
        """
        The validate config core method.
        Returns a dict with the list of syntax warnings and errors.
        """
        try:
            import ConfigParser
        except ImportError:
            import configparser as ConfigParser

        ret = {
            "errors": 0,
            "warnings": 0,
        }

        if path is None:
            config = self.config
        else:
            try:
                config = read_cf(path)
            except ConfigParser.ParsingError:
                self.log.error("error parsing %s" % path)
                ret["errors"] += 1

        def check_scoping(key, section, option):
            """
            Verify the specified option scoping is allowed.
            """
            if not key.at and "@" in option:
                self.log.error("option %s.%s does not support scoping", section, option)
                return 1
            return 0

        def check_references(section, option):
            """
            Verify the specified option references.
            """
            value = config.get(section, option)
            try:
                value = self.handle_references(value, scope=True, config=config)
            except ex.excError as exc:
                if not option.startswith("pre_") and \
                   not option.startswith("post_") and \
                   not option.startswith("blocking_"):
                    self.log.error(str(exc))
                    return 1
            except Exception as exc:
                self.log.error(str(exc))
                return 1
            return 0

        def get_val(key, section, option):
            """
            Fetch the value and convert it to the expected type.
            """
            _option = option.split("@")[0]
            value = self.conf_get(section, _option, config=config)
            return value

        def check_candidates(key, section, option, value):
            """
            Verify the specified option value is in allowed candidates.
            """
            if not key.strict_candidates:
                return 0
            if key.candidates is None:
                return 0
            if isinstance(value, (list, tuple, set)):
                valid = len(set(value) - set(key.candidates)) == 0
            else:
                valid = value in key.candidates
            if not valid:
                if isinstance(key.candidates, (set, list, tuple)):
                    candidates = ", ".join(key.candidates)
                else:
                    candidates = str(key.candidates)
                self.log.error("option %s.%s value %s is not in valid candidates: %s",
                               section, option, str(value), candidates)
                return 1
            return 0

        def check_known_option(key, section, option):
            """
            Verify the specified option scoping, references and that the value
            is in allowed candidates.
            """
            err = 0
            err += check_scoping(key, section, option)
            if check_references(section, option) != 0:
                err += 1
                return err
            try:
                value = get_val(key, section, option)
            except ValueError as exc:
                self.log.warning(str(exc))
                return 0
            except ex.OptNotFound:
                return 0
            err += check_candidates(key, section, option, value)
            return err

        def validate_resources_options(config, ret):
            """
            Validate resource sections options.
            """
            for section in config.sections():
                if section == "env":
                    # the "env" section is not handled by a resource driver, and is
                    # unknown to the nodedict. Just ignore it.
                    continue
                family = section.split("#")[0]
                if config.has_option(section, "type"):
                    rtype = config.get(section, "type")
                else:
                    rtype = None
                if family not in list(nodedict.NODEKEYS.sections.keys()) + list(nodedict.DEPRECATED_SECTIONS.keys()):
                    self.log.warning("ignored section %s", section)
                    ret["warnings"] += 1
                    continue
                if family in nodedict.DEPRECATED_SECTIONS:
                    self.log.warning("deprecated section prefix %s", family)
                    ret["warnings"] += 1
                    family, rtype = nodedict.DEPRECATED_SECTIONS[family]
                for option in config.options(section):
                    if option in config.defaults():
                        continue
                    key = nodedict.NODEKEYS.sections[family].getkey(option, rtype=rtype)
                    if key is None:
                        key = nodedict.NODEKEYS.sections[family].getkey(option)
                    if key is None:
                        self.log.warning("ignored option %s.%s%s", section,
                                         option, ", driver %s" % rtype if rtype else "")
                        ret["warnings"] += 1
                    else:
                        ret["errors"] += check_known_option(key, section, option)
            return ret

        ret = validate_resources_options(config, ret)

        return ret



