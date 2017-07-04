"""
The module defining the Svc class.
"""
from __future__ import print_function
from __future__ import unicode_literals

import sys
import os
import signal
import logging
import datetime
import time
import lock
import json
import re

from resources import Resource
from resourceset import ResourceSet
from freezer import Freezer
import rcStatus
from rcGlobalEnv import rcEnv, get_osvc_paths, Storage
from rcUtilities import justcall, lazy, unset_lazy, vcall, lcall, is_string, try_decode, eval_expr, convert_bool, action_triggers, read_cf
import rcExceptions as ex
import rcLogger
import node
from rcScheduler import scheduler_fork, Scheduler, SchedOpts
from comm import Crypt

if sys.version_info[0] < 3:
    BrokenPipeError = IOError

def signal_handler(*args):
    """
    A signal handler raising the excSignal exception.
    Args can be signum and frame, but we don't use them.
    """
    raise ex.excSignal

DEFAULT_WAITLOCK = 60

DEFAULT_STATUS_GROUPS = [
    "container",
    "ip",
    "disk",
    "fs",
    "share",
    "sync",
    "app",
    "stonith",
    "task",
]

CONFIG_DEFAULTS = {
    'push_schedule': '00:00-06:00@361',
    'sync_schedule': '04:00-06:00@121',
    'comp_schedule': '00:00-06:00@361',
    'status_schedule': '@9',
    'monitor_schedule': '@1',
    'resinfo_schedule': '@60',
    'no_schedule': '',
}

ACTIONS_ASYNC = [
    "freeze",
    "start",
    "stop",
    "thaw",
]

ACTIONS_NO_STATUS_CHANGE = [
    "autopush",
    "clear",
    "docker",
    "frozen",
    "get",
    "json_config",
    "json_status",
    "json_disklist",
    "json_devlist",
    "logs",
    "print_config",
    "print_devlist",
    "print_disklist",
    "print_config_mtime",
    "print_resource_status",
    "print_schedule",
    "print_status",
    "push",
    "push_resinfo",
    "push_config",
    "push_service_status",
    "prstatus",
    "scheduler",
    "status",
    "validate_config",
]

ACTIONS_ALLOW_ON_INVALID_NODE = [
    "delete",
    "edit_config",
    "frozen",
    "get",
    "logs",
    "print_config",
    "set",
    "unset",
    "update",
    "validate_config",
]

ACTIONS_NO_LOG = [
    "delete",
    "edit_config",
    "get",
    "group_status",
    "logs",
    "push",
    "push_resinfo",
    "push_config",
    "push_service_status",
    "resource_monitor",
    "scheduler",
    "set",
    "status",
    "unset",
    "validate_config",
]

ACTIONS_NO_TRIGGER = [
    "delete",
    "dns_update",
    "enable",
    "disable",
    "status",
    "scheduler",
    "pg_freeze",
    "pg_thaw",
    "pg_kill",
    "logs",
    "edit_config",
    "push_resinfo",
    "push",
    "group_status",
    "presync",
    "postsync",
    "resource_monitor",
]

ACTIONS_NO_LOCK = [
    "docker",
    "edit_config",
    "freeze",
    "frozen",
    "get",
    "logs",
    "push",
    "push_resinfo",
    "push_config",
    "push_service_status",
    "run",
    "scheduler",
    "status",
    "thaw",
    "toc",
    "validate_config",
]

DISK_TYPES = [
    "disk.drbd",
    "disk.gandi",
    "disk.gce",
    "disk.lock",
    "disk.loop",
    "disk.md",
    "disk.rados",
    "disk.raw",
    "disk.vg",
    "disk.zpool",
]

STATUS_TYPES = [
    "app",
    "container.amazon",
    "container.docker",
    "container.esx",
    "container.hpvm",
    "container.jail",
    "container.kvm",
    "container.lxc",
    "container.ldom",
    "container.openstack",
    "container.ovm",
    "container.srp",
    "container.vbox",
    "container.vcloud",
    "container.vz",
    "container.xen",
    "container.zone",
    "disk.drbd",
    "disk.gandi",
    "disk.gce",
    "disk.lock",
    "disk.loop",
    "disk.md",
    "disk.lv",
    "disk.raw",
    "disk.rados",
    "disk.scsireserv",
    "disk.vg",
    "disk.zpool",
    "fs",
    "ip",
    "ip.docker",
    "share.nfs",
    "sync.btrfs",
    "sync.btrfssnap",
    "sync.dcsckpt",
    "sync.dcssnap",
    "sync.dds",
    "sync.docker",
    "sync.evasnap",
    "sync.hp3par",
    "sync.hp3parsnap",
    "sync.ibmdssnap",
    "sync.necismsnap",
    "sync.netapp",
    "sync.nexenta",
    "sync.rados",
    "sync.rsync",
    "sync.symclone",
    "sync.symsnap",
    "sync.symsrdfs",
    "sync.s3",
    "sync.zfs",
    "stonith.callout",
    "stonith.ilo",
    "task",
]

ACTIONS_DO_MASTER_AND_SLAVE = [
    "boot",
    "migrate",
    "prstart",
    "prstop",
    "restart",
    "shutdown",
    "start",
    "startstandby",
    "stop",
    "stopstandby",
    "switch",
]

ACTIONS_NEED_SNAP_TRIGGER = [
    "sync_drp",
    "sync_nodes",
    "sync_resync",
    "sync_update",
]

CLUSTER_TYPES = [
    "failover",
    "flex",
]

os.environ['LANG'] = 'C'

def _slave_action(func):
    def need_specifier(self):
        """
        Raise an exception if --master or --slave(s) need to be set
        """
        if self.command_is_scoped():
            return
        if self.running_action in ACTIONS_DO_MASTER_AND_SLAVE:
            return
        if self.options.master or self.options.slaves or self.options.slave is not None:
            return
        raise ex.excError("specify either --master, --slave(s) or both (%s)" % func.__name__)

    def _func(self):
        if self.encap or not self.has_encap_resources:
            return
        if self.command_is_scoped() and \
           len(set(self.action_rid) & set(self.encap_resources.keys())) == 0:
            self.log.info("skip action on slaves: no encap resources are selected")
            return
        need_specifier(self)
        if self.options.slaves or \
           self.options.slave is not None or \
           (not self.options.master and not self.options.slaves and self.options.slave is None):
            try:
                func(self)
            except Exception as exc:
                raise ex.excError(str(exc))
    return _func

def _master_action(func):
    def need_specifier(self):
        """
        Raise an exception if --master or --slave(s) need to be set
        """
        if self.encap:
            return
        if not self.has_encap_resources:
            return
        if self.command_is_scoped():
            return
        if self.running_action in ACTIONS_DO_MASTER_AND_SLAVE:
            return
        if self.options.master or self.options.slaves or self.options.slave is not None:
            return
        raise ex.excError("specify either --master, --slave(s) or both (%s)" % func.__name__)

    def _func(self):
        need_specifier(self)
        if self.options.master or \
           (not self.options.master and not self.options.slaves and self.options.slave is None):
            func(self)
    return _func

class Svc(Crypt):
    """
    A OpenSVC service class.
    A service is a collection of resources.
    It exposes operations methods like provision, unprovision, stop, start,
    and sync.
    """

    def __init__(self, svcname=None):
        self.type = "hosted"
        self.svcname = svcname
        self.hostid = rcEnv.nodename
        self.paths = Storage(
            exe=os.path.join(rcEnv.paths.pathetc, self.svcname),
            cf=os.path.join(rcEnv.paths.pathetc, self.svcname+'.conf'),
            initd=os.path.join(rcEnv.paths.pathetc, self.svcname+'.d'),
            alt_initd=os.path.join(rcEnv.paths.pathetc, self.svcname+'.dir'),
            push_flag=os.path.join(rcEnv.paths.pathvar, self.svcname, 'last_pushed_config'),
        )
        self.resources_by_id = {}
        self.encap_resources = {}
        self.resourcesets = []
        self.resourcesets_by_type = {}
        self.disks = set()
        self.devs = set()

        self.ref_cache = {}
        self.encap_json_status_cache = {}
        self.rset_status_cache = None
        self.lockfd = None
        self.group_status_cache = None
        self.abort_start_done = False
        self.action_start_date = datetime.datetime.now()
        self.ha = False
        self.has_encap_resources = False
        self.encap = False
        self.action_rid = []
        self.action_rid_before_depends = []
        self.action_rid_depends = []
        self.dependencies = {}
        self.running_action = None
        self.need_postsync = set()

        # set by the builder
        self.conf = os.path.join(rcEnv.paths.pathetc, svcname+".conf")
        self.node = None
        self.comment = ""
        self.drp_type = ""
        self.drnoaction = False
        self.clustertype = "failover"
        self.placement = "nodes order"
        self.show_disabled = False
        self.svc_env = rcEnv.node_env
        self.nodes = set([rcEnv.nodename])
        self.ordered_nodes = [rcEnv.nodename]
        self.drpnodes = set()
        self.ordered_drpnodes = []
        self.drpnode = ""
        self.encapnodes = set()
        self.flex_primary = ""
        self.drp_flex_primary = ""
        self.sync_dblogger = False
        self.create_pg = False
        self.disable_rollback = False
        self.presync_done = False
        self.presnap_trigger = None
        self.postsnap_trigger = None
        self.monitor_action = None
        self.pre_monitor_action = None
        self.disabled = False
        self.anti_affinity = None
        self.affinity = None
        self.lock_timeout = DEFAULT_WAITLOCK

        # merged by the cmdline parser
        self.options = Storage(
            color="auto",
            crm=False,
            slaves=False,
            slave=None,
            master=False,
            cron=False,
            force=False,
            remote=False,
            debug=False,
            disable_rollback=False,
            show_disabled=None,
            moduleset="",
            module="",
            ruleset_date="",
            dry_run=False,
            refresh=False,
            rid=None,
            tags=None,
            subsets=None,
            discard=False,
            recover=False,
            waitlock=DEFAULT_WAITLOCK,
            wait=False,
        )

        self.scsirelease = self.prstop
        self.scsireserv = self.prstart
        self.scsicheckreserv = self.prstatus

    @lazy
    def log(self):
        return rcLogger.initLogger(self.svcname)

    @lazy
    def sched(self):
        """
        Lazy init of the service scheduler.
        """
        return Scheduler(
            name=self.svcname,
            config_defaults=CONFIG_DEFAULTS,
            options=self.options,
            config=self.config,
            log=self.log,
            svc=self,
            scheduler_actions={
                "compliance_auto": SchedOpts(
                    "DEFAULT",
                    fname=self.svcname+os.sep+"last_comp_check",
                    schedule_option="comp_schedule"
                ),
                "push_service_status": SchedOpts(
                    "DEFAULT",
                    fname=self.svcname+os.sep+"last_push_service_status",
                    schedule_option="status_schedule"
                ),
            },
        )

    @lazy
    def dockerlib(self):
        """
        Lazy allocator for the dockerlib object.
        """
        import rcDocker
        return rcDocker.DockerLib(self)

    @lazy
    def freezer(self):
        """
        Lazy allocator for the freezer object.
        """
        return Freezer(self.svcname)

    @lazy
    def compliance(self):
        from compliance import Compliance
        comp = Compliance(self)
        return comp

    @lazy
    def constraints(self):
        """
        Return True if no constraints is defined or if defined constraints are
        met. Otherwise return False.
        """
        try:
            return self.conf_get_boolean_scope("DEFAULT", "constraints")
        except ex.OptNotFound:
            return True
        except ex.excError:
            return True

    @lazy
    def app(self):
        """
        Return the service app code.
        """
        try:
            return self.conf_get_string_scope("DEFAULT", "app")
        except ex.OptNotFound:
            return ""

    def __lt__(self, other):
        """
        Order by service name
        """
        return self.svcname < other.svcname

    def register_dependency(self, action, _from, _to):
        if action not in self.dependencies:
            self.dependencies[action] = {}
        if _from not in self.dependencies[action]:
            self.dependencies[action][_from] = set()
        self.dependencies[action][_from].add(_to)

    def action_rid_dependencies(self, action, rid):
        if action in ("boot", "provision", "start"):
            action = "start"
        elif action in ("shutdown", "unprovision", "stop"):
            action = "stop"
        else:
            return set()
        if action not in self.dependencies:
            return set()
        if rid not in self.dependencies[action]:
            return set()
        return self.dependencies[action][rid]

    def action_rid_dependency_of(self, action, rid):
        if action in ("boot", "provision", "start"):
            action = "start"
        elif action in ("shutdown", "unprovision", "stop"):
            action = "stop"
        else:
            return set()
        if action not in self.dependencies:
            return set()
        dependency_of = set()
        for _rid, dependencies in self.dependencies[action].items():
            if rid in dependencies:
                dependency_of.add(_rid)
        return dependency_of

    def print_schedule(self):
        """
        The 'print schedule' node and service action entrypoint.
        """
        return self.sched.print_schedule()

    def scheduler(self):
        """
        The service scheduler action entrypoint.
        """
        self.options.cron = True
        self.sync_dblogger = True
        for action in self.sched.scheduler_actions:
            try:
                if action == "sync_all":
                    # save the action logging to the collector if sync_all
                    # is not needed
                    self.sched_sync_all()
                elif action.startswith("task#"):
                    self.run_task(action)
                elif action == "compliance_auto":
                    self.compliance_auto()
                else:
                    self.action(action)
            except ex.MonitorAction as exc:
                self.svcunlock()
                self.action("toc")
            except ex.excError as exc:
                self.log.error(exc)
            except:
                self.save_exc()

    def post_build(self):
        """
        A method run after the service is done building.
        Add resource-dependent tasks to the scheduler.
        """
        if not self.encap:
            self.sched.scheduler_actions["push_config"] = SchedOpts(
                "DEFAULT",
                fname=self.svcname+os.sep+"last_push_config",
                schedule_option="push_schedule"
            )

        try:
            monitor_schedule = self.conf_get_string_scope('DEFAULT', 'monitor_schedule')
        except ex.OptNotFound:
            monitor_schedule = None

        if (self.ha and "flex" not in self.clustertype) or monitor_schedule is not None:
            self.sched.scheduler_actions["resource_monitor"] = SchedOpts(
                "DEFAULT",
                fname=self.svcname+os.sep+"last_resource_monitor",
                schedule_option="monitor_schedule"
            )

        syncs = []

        for resource in self.get_resources("sync"):
            syncs += [SchedOpts(
                resource.rid,
                fname=self.svcname+os.sep+"last_syncall_"+resource.rid,
                schedule_option="sync_schedule"
            )]

        if len(syncs) > 0:
            self.sched.scheduler_actions["sync_all"] = syncs

        for resource in self.get_resources("task"):
            self.sched.scheduler_actions[resource.rid] = SchedOpts(
                resource.rid,
                fname=self.svcname+os.sep+"last_"+resource.rid,
                schedule_option="no_schedule"
            )

        self.sched.scheduler_actions["push_resinfo"] = SchedOpts(
            "DEFAULT",
            fname=self.svcname+os.sep+"last_push_resinfo",
            schedule_option="resinfo_schedule"
        )

    def purge_status_last(self):
        """
        Purge all service resources on-disk status caches.
        """
        for rset in self.resourcesets:
            rset.purge_status_last()

    def get_subset_parallel(self, rtype):
        """
        Return True if the resources of a resourceset can run an action in
        parallel executing per-resource workers.
        """
        rtype = rtype.split(".")[0]
        subset_section = 'subset#' + rtype
        if not self.config.has_section(subset_section):
            return False
        try:
            return self.conf_get_boolean_scope(subset_section, "parallel")
        except ex.OptNotFound:
            return False

    def __iadd__(self, other):
        """
        Svc += ResourceSet
        Svc += Resource
        """
        if hasattr(other, 'resources'):
            # new ResourceSet or ResourceSet-derived class
            self.resourcesets.append(other)
            self.resourcesets_by_type[other.type] = other
            other.svc = self
            return self

        if other.subset is not None:
            # the resource wants to be added to a specific resourceset
            # for action grouping, parallel execution or sub-resource
            # triggers
            base_type = other.type.split(".")[0]
            rtype = "%s:%s" % (base_type, other.subset)
        else:
            rtype = other.type

        if rtype in self.resourcesets_by_type:
            # the resource set already exists. add resource or resourceset.
            self.resourcesets_by_type[rtype] += other
        elif isinstance(other, Resource):
            parallel = self.get_subset_parallel(rtype)
            if hasattr(other, 'rset_class'):
                rset = other.rset_class(type=rtype, resources=[other], parallel=parallel)
            else:
                rset = ResourceSet(type=rtype, resources=[other], parallel=parallel)
            rset.rid = rtype
            rset.svc = self
            rset.pg_settings = self.get_pg_settings("subset#"+rtype)
            self.__iadd__(rset)
        else:
            self.log.debug("unexpected object addition to the service: %s",
                           str(other))

        if isinstance(other, Resource) and other.rid and "#" in other.rid:
            other.pg_settings = self.get_pg_settings(other.rid)
            self.resources_by_id[other.rid] = other

        other.svc = self

        if not other.disabled and hasattr(other, "on_add"):
            other.on_add()

        return self

    def dblogger(self, action, begin, end, actionlogfile):
        """
        Send to the collector the service status after an action, and
        the action log.
        """
        self.node.collector.call(
            'end_action', self, action, begin, end, actionlogfile,
            sync=self.sync_dblogger
        )
        g_vars, g_vals, r_vars, r_vals = self.svcmon_push_lists()
        self.node.collector.call(
            'svcmon_update_combo', g_vars, g_vals, r_vars, r_vals,
            sync=self.sync_dblogger
        )
        os.unlink(actionlogfile)
        try:
            logging.shutdown()
        except:
            pass

    def svclock(self, action=None, timeout=30, delay=1):
        """
        Acquire the service action lock.
        """
        suffix = None
        if action in ACTIONS_NO_LOCK or \
           action.startswith("collector") or \
           self.lockfd is not None:
            # explicitly blacklisted or
            # no need to serialize requests or
            # already acquired
            return

        if action.startswith("compliance"):
            # compliance modules are allowed to execute actions on the service
            # so give them their own lock
            suffix = "compliance"
        elif action.startswith("sync"):
            suffix = "sync"

        lockfile = os.path.join(rcEnv.paths.pathlock, self.svcname)
        if suffix is not None:
            lockfile = ".".join((lockfile, suffix))

        details = "(timeout %d, delay %d, action %s, lockfile %s)" % \
                  (timeout, delay, action, lockfile)
        self.log.debug("acquire service lock %s", details)
        try:
            lockfd = lock.lock(
                timeout=timeout,
                delay=delay,
                lockfile=lockfile,
                intent=action
            )
        except lock.lockTimeout as exc:
            raise ex.excError("timed out waiting for lock %s: %s" % (details, str(exc)))
        except lock.lockNoLockFile:
            raise ex.excError("lock_nowait: set the 'lockfile' param %s" % details)
        except lock.lockCreateError:
            raise ex.excError("can not create lock file %s" % details)
        except lock.lockAcquire as exc:
            raise ex.excError("another action is currently running %s: %s" % (details, str(exc)))
        except ex.excSignal:
            raise ex.excError("interrupted by signal %s" % details)
        except Exception as exc:
            self.save_exc()
            raise ex.excError("unexpected locking error %s: %s" % (details, str(exc)))

        if lockfd is not None:
            self.lockfd = lockfd

    def svcunlock(self):
        """
        Release the service action lock.
        """
        lock.unlock(self.lockfd)
        self.lockfd = None

    @staticmethod
    def setup_signal_handlers():
        """
        Install signal handlers.
        """
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def get_resource(self, rid):
        """
        Return a resource object by id.
        Return None if the rid is not found.
        """
        if rid not in self.resources_by_id:
            return
        return self.resources_by_id[rid]

    def get_resources(self, _type=None, strict=False, discard_disabled=True):
        """
        Return the list of resources matching criteria.
        """
        if _type is None:
            rsets = self.resourcesets
        else:
            rsets = self.get_resourcesets(_type, strict=strict)

        resources = []
        for rset in rsets:
            for resource in rset.resources:
                if not self.encap and 'encap' in resource.tags:
                    continue
                if discard_disabled and resource.disabled:
                    continue
                resources.append(resource)
        return resources

    def get_resourcesets(self, _type, strict=False):
        """
        Return the list of resourceset matching the specified types.
        """
        if not isinstance(_type, list):
            _types = [_type]
        else:
            _types = _type
        rsets_by_type = {}
        for rset in self.resourcesets:
            if ':' in rset.type and rset.has_resource_with_types(_types, strict=strict):
                # subset
                rsets_by_type[rset.type] = rset
                continue
            rs_base_type = rset.type.split(".")[0]
            if rset.type in _types:
                # exact match
                if rs_base_type not in rsets_by_type:
                    rsets_by_type[rs_base_type] = type(rset)(type=rs_base_type)
                    rsets_by_type[rs_base_type].svc = self
                rsets_by_type[rs_base_type] += rset
            elif rs_base_type in _types and not strict:
                # group match
                if rs_base_type not in rsets_by_type:
                    rsets_by_type[rs_base_type] = type(rset)(type=rs_base_type)
                    rsets_by_type[rs_base_type].svc = self
                rsets_by_type[rs_base_type] += rset
        rsets = list(rsets_by_type.values())
        rsets.sort()
        return rsets

    def has_resourceset(self, _type, strict=False):
        """
        Return True if the service has a resource set of the specified type.
        """
        return len(self.get_resourcesets(_type, strict=strict)) > 0

    def all_set_action(self, action=None, tags=None):
        """
        Execute an action on all resources all resource sets.
        """
        self.set_action(self.resourcesets, action=action, tags=tags)

    def sub_set_action(self, _type=None, action=None, tags=None, xtags=None,
                       strict=False):
        """
        Execute an action on all resources of the resource sets of the
        specified type.
        """
        rsets = self.get_resourcesets(_type, strict=strict)
        self.set_action(rsets, action=action, tags=tags, xtags=xtags)

    def need_snap_trigger(self, rsets, action):
        """
        Return True if the action is a sync action and at least one of the
        specified resource sets has a resource requiring a snapshot.
        """
        if action not in ACTIONS_NEED_SNAP_TRIGGER:
            return False
        for rset in rsets:
            for resource in rset.resources:
                # avoid to run pre/post snap triggers when there is no
                # resource flagged for snap and on drpnodes
                if hasattr(resource, "snap") and resource.snap is True and \
                   rcEnv.nodename in self.nodes:
                    return True
        return False

    def action_triggers(self, trigger, action, **kwargs):
        """
        Executes a resource trigger. Guess if the shell mode is needed from
        the trigger syntax.
        """
        action_triggers(self, trigger, action, **kwargs)

    def set_action(self, rsets=None, action=None, tags=None, xtags=None):
        """
        Call the action on all sets sorted resources.
        If the sets define a pre_snap trigger run that before the action.
        If the sets define a pre_<action> trigger run that before the action.
        If the sets define a post_<action> trigger run that after the action.
        """
        if rsets is None:
            rsets = []
        if tags is None:
            tags = set()
        if xtags is None:
            xtags = set()

        def do_trigger(when):
            """
            Excecute a <when> trigger on each resource of the set,
            if the action allows triggers.
            If a trigger raises,
            * excError, stop looping over the resources and propagate up
              to the caller.
            * excAbortAction, continue looping over the resources
            * any other exception, save the traceback in the debug log
              and stop looping over the resources and raise an excError
            """
            for rset in rsets:
                if action in ACTIONS_NO_TRIGGER or rset.all_skip(action):
                    break
                try:
                    rset.log.debug("start %s %s_action", rset.type, when)
                    getattr(rset, when + "_action")(action)
                except ex.excError:
                    raise
                except ex.excAbortAction:
                    continue
                except:
                    self.save_exc()
                    raise ex.excError

        def do_snap_trigger(when):
            """
            Execute the <when>snap trigger.
            """
            if not need_snap:
                return
            trigger = getattr(self, when + "snap_trigger")
            if trigger is None:
                return
            results = self.vcall(trigger)
            if results[0] != 0:
                raise ex.excError(results[2])

        need_snap = self.need_snap_trigger(rsets, action)

        # Multiple resourcesets of the same type need to be sorted
        # so that the start and stop action happen in a predictible order.
        # Sort alphanumerically on reseourceset type.
        #
        #  Example, on start:
        #   app
        #   app.1
        #   app.2
        #  on stop:
        #   app.2
        #   app.1
        #   app
        reverse = "stop" in action or action in ("rollback", "shutdown", "unprovision")
        rsets = sorted(rsets, key=lambda x: x.type, reverse=reverse)

        # snapshots are created in pre_action and destroyed in post_action
        # place presnap and postsnap triggers around pre_action
        do_snap_trigger("pre")
        do_trigger("pre")
        do_snap_trigger("post")

        for rset in rsets:
            self.log.debug('set_action: action=%s rset=%s', action, rset.type)
            rset.action(action, tags=tags, xtags=xtags)

        do_trigger("post")

    def __str__(self):
        """
        The Svc class print formatter.
        """
        output = "Service %s available resources:" % self.svcname
        for key in self.resourcesets_by_type:
            output += " %s" % key
        output += "\n"
        for rset in self.resourcesets:
            output += "  [%s]" % str(rset)
        return output

    def status(self):
        """
        Return the aggregate status a service.
        """
        group_status = self.group_status()
        return group_status["overall"].status

    @lazy
    def status_data_dump(self):
        return os.path.join(rcEnv.paths.pathvar, self.svcname, "status.json")

    def print_status_data(self, from_resource_status_cache=False):
        """
        Return a structure containing hierarchical status of
        the service.
        """
        if not from_resource_status_cache and not self.options.refresh and os.path.exists(self.status_data_dump):
            try:
                with open(self.status_data_dump, 'r') as filep:
                    return json.load(filep)
            except ValueError:
                pass

        now = time.time()

        data = {
            "updated": datetime.datetime.utcfromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "mtime": now,
            "resources": {},
            "frozen": self.frozen(),
            "constraints": self.constraints,
        }

        containers = self.get_resources('container')
        if len(containers) > 0:
            data['encap'] = {}
            for container in containers:
                if container.name is None or len(container.name) == 0:
                    # docker case
                    continue
                try:
                    data['encap'][container.rid] = self.encap_json_status(container)
                except:
                    data['encap'][container.rid] = {'resources': {}}
                if hasattr(container, "vm_hostname"):
                    data['encap'][container.rid]["hostname"] = container.vm_hostname

        for rset in self.get_resourcesets(STATUS_TYPES, strict=True):
            for resource in rset.resources:
                (
                    rid,
                    rtype,
                    status,
                    label,
                    log,
                    monitor,
                    disable,
                    optional,
                    encap
                ) = resource.status_quad(color=False)
                data['resources'][rid] = {
                    'status': str(status),
                    'type': rtype,
                    'label': label,
                    'log': log,
                    'tags': sorted(list(resource.tags)),
                    'monitor':monitor,
                    'disable': disable,
                    'optional': optional,
                    'encap': encap,
                }
        group_status = self.group_status()
        for group in group_status:
            data[group] = str(group_status[group])
        self.write_status_data(data)
        return data

    def write_status_data(self, data):
        try:
            with open(self.status_data_dump, "w") as filep:
                json.dump(data, filep)
            os.utime(self.status_data_dump, (-1, data["mtime"]))
        except Exception as exc:
            self.log.warning("failed to update %s: %s" % (self.status_data_dump, str(exc)))
        return data

    def update_status_data(self):
        if self.options.minimal:
            return
        data = self.print_status_data(from_resource_status_cache=True)
        self.write_status_data(data)

    def env_section_keys_evaluated(self):
        """
        Return the dict of key/val pairs in the [env] section of the
        service configuration, after dereferencing.
        """
        return self.env_section_keys(evaluate=True)

    def env_section_keys(self, evaluate=False):
        """
        Return the dict of key/val pairs in the [env] section of the
        service configuration, without dereferencing.
        """
        config = self.print_config_data()
        try:
            from collections import OrderedDict
            data = OrderedDict()
        except ImportError:
            data = {}
        for key in config.get("env", {}).keys():
            key = key.split("@")[0]
            if key in data:
                continue
            if evaluate:
                data[key] = self.conf_get_string_scope('env', key)
            else:
                data[key] = config["env"][key]
        return data

    def print_config_data(self):
        """
        Return a simple dict (OrderedDict if possible), fed with the
        service configuration sections and keys
        """
        try:
            from collections import OrderedDict
            best_dict = OrderedDict
        except ImportError:
            best_dict = dict
        svc_config = best_dict()
        tmp = best_dict()
        config = self.config

        defaults = config.defaults()
        for key in defaults.keys():
            tmp[key] = defaults[key]

        svc_config['DEFAULT'] = tmp
        config._defaults = {}

        sections = config.sections()
        for section in sections:
            options = config.options(section)
            tmpsection = best_dict()
            for option in options:
                if config.has_option(section, option):
                    tmpsection[option] = config.get(section, option)
            svc_config[section] = tmpsection
        unset_lazy(self, "config")
        return svc_config

    def logs(self):
        """
        Extract and display the service logs, honoring --color and --debug
        """
        if len(self.log.handlers) == 0:
            return

        if self.options.debug:
            logfile = os.path.join(rcEnv.paths.pathlog, self.svcname+".debug.log")
        else:
            logfile = os.path.join(rcEnv.paths.pathlog, self.svcname+".log")

        if not os.path.exists(logfile):
            return

        from rcColor import color, colorize
        def fmt(line):
            """
            Format a log line, colorizing the log level.
            Return the line as a string buffer.
            """
            line = line.rstrip("\n")
            elements = line.split(" - ")

            if len(elements) < 3 or elements[2] not in ("DEBUG", "INFO", "WARNING", "ERROR"):
                # this is a log line continuation (command output for ex.)
                return line

            if not rcLogger.include_svcname:
                elements[1] = elements[1].replace(self.svcname, "").lstrip(".")
            if len(elements[1]) > rcLogger.namelen:
                elements[1] = "*"+elements[1][-(rcLogger.namelen-1):]
            elements[1] = rcLogger.namefmt % elements[1]
            elements[1] = colorize(elements[1], color.BOLD)
            elements[2] = "%-7s" % elements[2]
            elements[2] = elements[2].replace("ERROR", colorize("ERROR", color.RED))
            elements[2] = elements[2].replace("WARNING", colorize("WARNING", color.BROWN))
            elements[2] = elements[2].replace("INFO", colorize("INFO", color.LIGHTBLUE))
            return " ".join(elements)

        try:
            pipe = os.popen('TERM=xterm less -R', 'w')
        except:
            pipe = sys.stdout

        try:
            for _logfile in [logfile+".1", logfile]:
                if not os.path.exists(_logfile):
                    continue
                with open(_logfile, "r") as ofile:
                    for line in ofile.readlines():
                        buff = fmt(line)
                        if buff:
                            pipe.write(buff+"\n")
        except BrokenPipeError:
            try:
                sys.stdout = os.fdopen(1)
            except (AttributeError, OSError, IOError):
                pass
        finally:
            if pipe != sys.stdout:
                pipe.close()

    def print_resource_status(self):
        """
        Print a single resource status string.
        """
        if len(self.action_rid) != 1:
            print("action 'print_resource_status' is not allowed on mutiple "
                  "resources", file=sys.stderr)
            return 1
        for rid in self.action_rid:
            if rid not in self.resources_by_id:
                print("resource not found")
                continue
            resource = self.resources_by_id[rid]
            print(rcStatus.colorize_status(str(resource.status())))
        return 0

    def print_status(self):
        """
        Display in human-readable format the hierarchical service status.
        """
        data = self.print_status_data()
        if self.options.format is not None:
            return data

        from textwrap import wrap
        from rcUtilities import term_width
        from rcColor import color, colorize
        import re

        width = term_width()

        def print_log(log, subpfx, width):
            lines = []
            for msg in log.split("\n"):
                if len(msg) == 0:
                    continue
                if subpfx and not subpfx.startswith(color.END):
                    subpfx = color.END + subpfx
                    lines += wrap(
                        msg,
                        initial_indent=subpfx,
                        subsequent_indent=subpfx,
                        width=width
                    )
            _color = None
            for line in lines:
                if " info: " in line:
                    _color = color.LIGHTBLUE
                elif " warn: " in line:
                    _color = color.BROWN
                elif " error: " in line:
                    _color = color.RED
                print(re.sub(r'([\s\|\`\-]+)(.*)', r'\1'+colorize(r'\2', _color), line))

        def print_res(squad, fmt, pfx, subpfx=None):
            """
            Print a resource line, with forest markers, rid, flags, label and
            resource log.
            """
            if subpfx is None:
                subpfx = pfx
            rid, status, label, log, monitor, disabled, optional, encap = squad
            flags = ''
            flags += 'M' if monitor else '.'
            flags += 'D' if disabled else '.'
            flags += 'O' if optional else '.'
            flags += 'E' if encap else '.'
            print(fmt % (rid, flags, rcStatus.colorize_status(status), label))
            print_log(log, subpfx, width)


        if self.options.show_disabled is not None:
            discard_disabled = not self.options.show_disabled
        else:
            discard_disabled = not self.show_disabled

        def dispatch_resources():
            """
            Dispatch resources in avail/accesory sets, and sort.
            """
            rbg = {
                    "ip": [],
                    "disk": [],
                    "fs": [],
                    "container": [],
                    "share": [],
                    "app": [],
                    "sync": [],
                    "stonith": [],
                    "task": [],
            }
            avail_resources = []
            accessory_resources = []

            for rid, resource in data["resources"].items():
                if discard_disabled and resource["disable"]:
                    continue
                group = resource["type"].split(".", 1)[0]
                rbg[group].append(rid)

            for group in ("ip", "disk", "fs", "container", "share", "app"):
                for rid in sorted(rbg[group]):
                    resource = data["resources"][rid]
                    resource["rid"] = rid
                    avail_resources.append(resource)
            for group in ("sync", "stonith", "task"):
                for rid in sorted(rbg[group]):
                    resource = data["resources"][rid]
                    resource["rid"] = rid
                    accessory_resources.append(resource)

            return (
                avail_resources,
                accessory_resources,
            )

        avail_resources, accessory_resources = dispatch_resources()
        n_accessory_resources = len(accessory_resources)

        print(colorize(self.svcname, color.BOLD))
        notice = []
        if self.frozen():
            notice.append("frozen")
        if not data.get("constraints", True):
            notice.append("constraints violation")
        notice = ", ".join(notice)
        fmt = "%-20s %4s %-10s %s"
        color_status = rcStatus.colorize_status(data['overall'])
        print(fmt % ("overall", '', color_status, notice))
        if n_accessory_resources == 0:
            fmt = "'- %-17s %4s %-10s %s"
            head_c = " "
        else:
            fmt = "|- %-17s %4s %-10s %s"
            head_c = "|"
        color_status = rcStatus.colorize_status(data['avail'])
        print(fmt % ("avail", '', color_status, ''))

        ers = {}
        for container in self.get_resources('container'):
            if container.type == "container.docker":
                continue
            try:
                ejs = data["encap"][container.rid]
                ers[container.rid] = ejs["resources"]
                if ejs.get("frozen", False):
                    container.status_log("frozen", "info")
            except ex.excNotAvailable:
                ers[container.rid] = {}
            except Exception as exc:
                print(exc)
                ers[container.rid] = {}

        lines = []
        encap_squad = {}
        for resource in avail_resources:
            rid = resource["rid"]
            lines.append((
                rid,
                resource["status"],
                resource["label"],
                resource["log"],
                resource["monitor"],
                resource["disable"],
                resource["optional"],
                resource["encap"]
            ))
            if rid.startswith("container") and rid in ers:
                squad = []
                for _rid, val in ers[rid].items():
                    squad.append((
                        _rid,
                        val['status'],
                        val['label'],
                        val['log'],
                        val['monitor'],
                        val['disable'],
                        val['optional'],
                        val['encap'],
                    ))
                encap_squad[rid] = squad

        last = len(lines) - 1
        if last >= 0:
            for idx, line in enumerate(lines):
                if idx == last:
                    fmt = head_c+"  '- %-14s %4s %-10s %s"
                    pfx = head_c+"     %-14s %4s %-10s " % ('', '', '')
                    subpfx = head_c+"        %-11s %4s %-10s " % ('', '', '')
                    print_res(line, fmt, pfx, subpfx=subpfx)
                    subresbar = " "
                else:
                    fmt = head_c+"  |- %-14s %4s %-10s %s"
                    pfx = head_c+"  |  %-14s %4s %-10s " % ('', '', '')
                    if line[0] in encap_squad and len(encap_squad[line[0]]) > 0:
                        subpfx = head_c+"  |  |  %-11s %4s %-10s " % ('', '', '')
                    else:
                        subpfx = None
                    print_res(line, fmt, pfx, subpfx=subpfx)
                    subresbar = "|"
                if line[0] in encap_squad:
                    _last = len(encap_squad[line[0]]) - 1
                    if _last >= 0:
                        for _idx, _line in enumerate(encap_squad[line[0]]):
                            if _idx == _last:
                                fmt = head_c+"  "+subresbar+"  '- %-11s %4s %-10s %s"
                                pfx = head_c+"  "+subresbar+"     %-11s %4s %-10s " % ('', '', '')
                                print_res(_line, fmt, pfx)
                            else:
                                fmt = head_c+"  "+subresbar+"  |- %-11s %4s %-10s %s"
                                pfx = head_c+"  "+subresbar+"  |  %-11s %4s %-10s " % ('', '', '')
                                print_res(_line, fmt, pfx)

        if n_accessory_resources > 0:
            fmt = "'- %-17s %4s %-10s %s"
            print(fmt%("accessory", '', '', ''))

        lines = []
        for resource in accessory_resources:
            rid = resource["rid"]
            lines.append((
                rid,
                resource["status"],
                resource["label"],
                resource["log"],
                resource["monitor"],
                resource["disable"],
                resource["optional"],
                resource["encap"]
            ))

        last = len(lines) - 1
        if last >= 0:
            for idx, line in enumerate(lines):
                if idx == last:
                    fmt = "   '- %-14s %4s %-10s %s"
                    pfx = "      %-14s %4s %-10s " % ('', '', '')
                    print_res(line, fmt, pfx)
                else:
                    fmt = "   |- %-14s %4s %-10s %s"
                    pfx = "   |  %-14s %4s %-10s " % ('', '', '')
                    print_res(line, fmt, pfx)

    def svcmon_push_lists(self):
        """
        Return the list of resource status in a format adequate for
        collector feeding.
        """
        data = self.print_status_data()

        if data["frozen"]:
            frozen = 1
        else:
            frozen = 0

        r_vars = [
            "svcname",
            "nodename",
            "vmname",
            "rid",
            "res_type",
            "res_desc",
            "res_status",
            "res_monitor",
            "res_optional",
            "res_disable",
            "updated",
            "res_log",
        ]
        r_vals = []
        now = datetime.datetime.now()

        for rid, resource in data["resources"].items():
            r_vals.append([
                self.svcname,
                rcEnv.nodename,
                "",
                rid,
                resource["type"],
                resource["label"],
                resource["status"],
                "1" if resource["monitor"] else "0",
                "1" if resource["optional"] else "0",
                "1" if resource["disable"] else "0",
                str(now),
                resource["log"]
            ])

        g_vars = [
            "mon_svcname",
            "mon_svctype",
            "mon_nodname",
            "mon_vmname",
            "mon_vmtype",
            "mon_nodtype",
            "mon_ipstatus",
            "mon_diskstatus",
            "mon_syncstatus",
            "mon_containerstatus",
            "mon_fsstatus",
            "mon_sharestatus",
            "mon_appstatus",
            "mon_availstatus",
            "mon_overallstatus",
            "mon_updated",
            "mon_prinodes",
            "mon_frozen",
        ]

        if "encap" not in data or not data["encap"]:
            g_vals = [
                self.svcname,
                self.svc_env,
                rcEnv.nodename,
                "",
                "hosted",
                rcEnv.node_env,
                data["ip"],
                data["disk"],
                data["sync"],
                data["container"],
                data["fs"],
                data["share"],
                data["app"],
                data["avail"],
                data["overall"],
                str(now),
                ' '.join(self.nodes),
                str(frozen)
            ]
        else:
            g_vals = []
            for rid, container in data["encap"].items():
                vhostname = container.get("hostname", self.resources_by_id[rid].name)
                container_type = self.resources_by_id[rid].type.replace("container.", "")
                for rid, resource in container['resources'].items():
                    r_vals.append([
                        self.svcname,
                        rcEnv.nodename,
                        vhostname,
                        rid,
                        resource["type"],
                        resource["label"],
                        resource["status"],
                        "1" if resource["monitor"] else "0",
                        "1" if resource["optional"] else "0",
                        "1" if resource["disable"] else "0",
                        str(now),
                        resource["log"],
                    ])

                #
                # 0: global thawed + encap thawed
                # 1: global frozen + encap thawed
                # 2: global thawed + encap frozen
                # 3: global frozen + encap frozen
                #
                if container["frozen"]:
                    e_frozen = frozen + 2
                else:
                    e_frozen = frozen

                g_vals.append([
                    self.svcname,
                    self.svc_env,
                    rcEnv.nodename,
                    vhostname,
                    container_type,
                    rcEnv.node_env,
                    str(rcStatus.Status(data["ip"])+rcStatus.Status(container['ip'])),
                    str(rcStatus.Status(data["disk"])+rcStatus.Status(container['disk'])),
                    str(rcStatus.Status(data["sync"])+rcStatus.Status(container['sync'])),
                    str(rcStatus.Status(data["container"])+rcStatus.Status(container['container'])),
                    str(rcStatus.Status(data["fs"])+rcStatus.Status(container['fs'])),
                    str(rcStatus.Status(data["share"])+rcStatus.Status(container['share'] if 'share' in container else 'n/a')),
                    str(rcStatus.Status(data["app"])+rcStatus.Status(container['app'])),
                    str(rcStatus.Status(data["avail"])+rcStatus.Status(container['avail'])),
                    str(rcStatus.Status(data["overall"])+rcStatus.Status(container['overall'])),
                    str(now),
                    ' '.join(self.nodes),
                    e_frozen,
                ])

        return g_vars, g_vals, r_vars, r_vals

    def get_rset_status(self, groups):
        """
        Return the aggregated status of all resources of the specified resource
        sets, as a dict of status indexed by resourceset type.
        """
        self.setup_environ()
        rsets_status = {}
        for status_type in STATUS_TYPES:
            group = status_type.split('.')[0]
            if group not in groups:
                continue
            for rset in self.get_resourcesets(status_type, strict=True):
                if rset.type not in rsets_status:
                    rsets_status[rset.type] = rset.status()
                else:
                    rsets_status[rset.type] += rset.status()
        return rsets_status

    def resource_monitor(self):
        """
        The resource monitor action entrypoint
        """
        if self.sched.skip_action("resource_monitor"):
            return
        self.task_resource_monitor()

    def task_resource_monitor(self):
        """
        The resource monitor action. Refresh important resources at a different
        schedule.
        """
        for resource in self.get_resources():
            if resource.monitor:
                resource.status(refresh=True)
        self.update_status_data()

    def reboot(self):
        """
        A method wrapper the node reboot method.
        """
        self.node.system._reboot()

    def crash(self):
        """
        A method wrapper the node crash method.
        """
        self.node.system.crash()

    def _pg_freeze(self):
        """
        Wrapper function for the process group freeze method.
        """
        return self._pg_freezer("freeze")

    def _pg_thaw(self):
        """
        Wrapper function for the process group thaw method.
        """
        return self._pg_freezer("thaw")

    def _pg_kill(self):
        """
        Wrapper function for the process group kill method.
        """
        return self._pg_freezer("kill")

    def _pg_freezer(self, action):
        """
        Wrapper function for the process group methods.
        """
        if not self.create_pg:
            return
        if self.pg is None:
            return
        if action == "freeze":
            self.pg.freeze(self)
        elif action == "thaw":
            self.pg.thaw(self)
        elif action == "kill":
            self.pg.kill(self)

    @lazy
    def pg(self):
        """
        A lazy property to import the system-specific process group module
        on-demand and expose it as self.pg
        """
        try:
            mod = __import__('rcPg'+rcEnv.sysname)
        except ImportError:
            self.log.info("process group are not supported on this platform")
            return
        except Exception as exc:
            print(exc)
            raise
        return mod

    def pg_freeze(self):
        """
        Freeze all process of the process groups of the service.
        """
        if self.command_is_scoped():
            self.sub_set_action('app', '_pg_freeze')
            self.sub_set_action('container', '_pg_freeze')
        else:
            self._pg_freeze()
            for resource in self.get_resources(["app", "container"]):
                resource.status(refresh=True)

    def pg_thaw(self):
        """
        Thaw all process of the process groups of the service.
        """
        if self.command_is_scoped():
            self.sub_set_action('app', '_pg_thaw')
            self.sub_set_action('container', '_pg_thaw')
        else:
            self._pg_thaw()
            for resource in self.get_resources(["app", "container"]):
                resource.status(refresh=True)

    def pg_kill(self):
        """
        Kill all process of the process groups of the service.
        """
        if self.command_is_scoped():
            self.sub_set_action('app', '_pg_kill')
            self.sub_set_action('container', '_pg_kill')
        else:
            self._pg_kill()
            for resource in self.get_resources(["app", "container"]):
                resource.status(refresh=True)

    def stonith(self):
        """
        The 'stonith' action entrypoint.
        Call the stonith method of resources implementing it.
        """
        self.sub_set_action('stonith.ilo', 'start')
        self.sub_set_action('stonith.callout', 'start')

    def do_pre_monitor_action(self):
        if self.pre_monitor_action is None:
            return
        kwargs = {}
        if "|" in self.pre_monitor_action or \
           "&&" in self.pre_monitor_action or \
           ";" in self.pre_monitor_action:
            kwargs["shell"] = True

        import shlex
        if not kwargs.get("shell", False):
            if sys.version_info[0] < 3:
                cmdv = shlex.split(self.pre_monitor_action.encode('utf8'))
                cmdv = [elem.decode('utf8') for elem in cmdv]
            else:
                cmdv = shlex.split(self.pre_monitor_action)
        else:
            cmdv = self.pre_monitor_action

        try:
            result = self.vcall(cmdv, **kwargs)
            ret = result[0]
        except OSError as exc:
            ret = 1
            if exc.errno == 8:
                self.log.error("%s exec format error: check the script shebang", self.pre_monitor_action)
            else:
                self.log.error("%s error: %s", self.pre_monitor_action, str(exc))
        except Exception as exc:
            ret = 1
            self.log.error("%s error: %s", self.pre_monitor_action, str(exc))

    def toc(self):
        """
        Call the resource monitor action.
        """
        self.do_pre_monitor_action()
        if self.monitor_action is None:
            return
        if not hasattr(self, self.monitor_action):
            self.log.error("invalid monitor action '%s'", self.monitor_action)
            return
        self.log.info("start monitor action '%s'", self.monitor_action)
        getattr(self, self.monitor_action)()

    def encap_cmd(self, cmd, verbose=False, error="raise"):
        """
        Execute a command in all service containers.
        If error is set to "raise", stop iterating at first error.
        If error is set to "continue", log errors and proceed to the next
        container.
        """
        for container in self.get_resources('container'):
            try:
                self._encap_cmd(cmd, container, verbose=verbose)
            except ex.excEncapUnjoignable:
                if error != "continue":
                    self.log.error("container %s is not joinable to execute "
                                   "action '%s'", container.name, ' '.join(cmd))
                    raise
                elif verbose:
                    self.log.warning("container %s is not joinable to execute "
                                     "action '%s'", container.name, ' '.join(cmd))

    def _encap_cmd(self, cmd, container, verbose=False):
        """
        Execute a command in a service container.
        """
        if container.pg_frozen():
            raise ex.excError("can't join a frozen container. abort encap "
                              "command.")
        vmhostname = container.vm_hostname
        if cmd == ["start"] and container.booted:
            return '', '', 0
        if not self.has_encap_resources:
            self.log.debug("skip encap %s: no encap resource", ' '.join(cmd))
            return '', '', 0
        if not container.is_up():
            msg = "skip encap %s: the container is not running here" % ' '.join(cmd)
            if verbose:
                self.log.info(msg)
            else:
                self.log.debug(msg)
            return '', '', 0

        if self.options.slave is not None and not \
           (container.name in self.options.slave or \
            container.rid in self.options.slave):
            # no need to run encap cmd (container not specified in --slave)
            return '', '', 0

        if cmd == ['start'] and not self.command_is_scoped():
            return '', '', 0

        # now we known we'll execute a command in the slave, so purge the
        # encap cache
        self.purge_cache_encap_json_status(container.rid)

        # wait for the container multi-user state
        if cmd[0] in ["start", "boot"] and hasattr(container, "wait_multi_user"):
            container.wait_multi_user()

        options = ['--daemon']
        if self.options.dry_run:
            options.append('--dry-run')
        if self.options.disable_rollback:
            options.append('--disable-rollback')
        if self.options.rid:
            options.append('--rid')
            options.append(self.options.rid)
        if self.options.tags:
            options.append('--tags')
            options.append(self.options.tags)
        if self.options.subsets:
            options.append('--subsets')
            options.append(self.options.subsets)

        paths = get_osvc_paths(osvc_root_path=container.osvc_root_path,
                               sysname=container.guestos)
        cmd = [paths.svcmgr, '-s', self.svcname] + options + cmd

        if container is not None and hasattr(container, "rcmd"):
            out, err, ret = container.rcmd(cmd)
        elif hasattr(container, "runmethod"):
            cmd = container.runmethod + cmd
            out, err, ret = justcall(cmd)
        else:
            raise ex.excEncapUnjoignable("undefined rcmd/runmethod in "
                                         "resource %s"%container.rid)

        if verbose:
            self.log.info('logs from %s child service:', container.name)
            print(out)
            if len(err) > 0:
                print(err)
        if ret != 0:
            raise ex.excError("error from encap service command '%s': "
                              "%d\n%s\n%s"%(' '.join(cmd), ret, out, err))
        return out, err, ret

    def get_encap_json_status_path(self, rid):
        """
        Return the path of the file where the status data of the service
        encapsulated in the container identified by <rid> will be written
        for caching.
        """
        return os.path.join(rcEnv.paths.pathvar, self.svcname, "encap.status."+rid)

    def purge_cache_encap_json_status(self, rid):
        """
        Delete the on-disk cache of status of the service encapsulated in
        the container identified by <rid>.
        """
        if rid in self.encap_json_status_cache:
            del self.encap_json_status_cache[rid]
        path = self.get_encap_json_status_path(rid)
        if os.path.exists(path):
            os.unlink(path)

    def put_cache_encap_json_status(self, rid, data):
        """
        Write the on-disk cache of status of the service encapsulated in
        the container identified by <rid>.
        """
        self.encap_json_status_cache[rid] = data
        path = self.get_encap_json_status_path(rid)
        directory = os.path.dirname(path)
        if not os.path.exists(directory):
            os.makedirs(directory)
        try:
            with open(path, 'w') as ofile:
                ofile.write(json.dumps(data))
        except (IOError, OSError, ValueError):
            os.unlink(path)

    def get_cache_encap_json_status(self, rid):
        """
        Fetch the on-disk cache of status of the service encapsulated in
        the container identified by <rid>.
        """
        if rid in self.encap_json_status_cache:
            return self.encap_json_status_cache[rid]
        path = self.get_encap_json_status_path(rid)
        try:
            with open(path, 'r') as ofile:
                group_status = json.loads(ofile.read())
        except (IOError, OSError, ValueError):
            group_status = None
        return group_status

    def encap_json_status(self, container, refresh=False):
        """
        Return the status data from the agent runnning the encapsulated part
        of the service.
        """
        if container.guestos == 'windows':
            raise ex.excNotAvailable

        if container.status(ignore_nostatus=True) == rcStatus.DOWN:
            #
            #  passive node for the vservice => forge encap resource status
            #    - encap sync are n/a
            #    - other encap res are down
            #
            group_status = {
                "avail": "down",
                "overall": "down",
                "type": container.type,
                "frozen": False,
                "resources": {},
            }
            groups = set(["container", "ip", "disk", "fs", "share"])
            for group in groups:
                group_status[group] = 'down'
            for rset in self.get_resourcesets(STATUS_TYPES, strict=True):
                group = rset.type.split('.')[0]
                if group not in groups:
                    continue
                for resource in rset.resources:
                    if not self.encap and 'encap' in resource.tags:
                        group_status['resources'][resource.rid] = {'status': 'down'}

            groups = set(["app", "sync"])
            for group in groups:
                group_status[group] = 'n/a'
            for rset in self.get_resourcesets(groups):
                group = rset.type.split('.')[0]
                if group not in groups:
                    continue
                for resource in rset.resources:
                    if not self.encap and 'encap' in resource.tags:
                        group_status['resources'][resource.rid] = {'status': 'n/a'}

            return group_status

        if not refresh and not self.options.refresh:
            group_status = self.get_cache_encap_json_status(container.rid)
            if group_status:
                return group_status

        group_status = {
            "avail": "n/a",
            "overall": "n/a",
            "type": container.type,
            "frozen": False,
            "resources": {},
        }
        groups = set([
            "container",
            "ip",
            "disk",
            "fs",
            "share",
            "stonith",
            "task",
            "app",
            "sync"
        ])

        for group in groups:
            group_status[group] = 'n/a'

        cmd = ['print', 'status', '--format', 'json']
        if self.options.refresh:
            cmd.append('--refresh')
        try:
            results = self._encap_cmd(cmd, container)
        except ex.excError:
            return group_status
        except Exception as exc:
            print(exc)
            return group_status

        try:
            group_status = json.loads(results[0])
        except:
            pass

        self.put_cache_encap_json_status(container.rid, group_status)

        return group_status

    def group_status(self, groups=None, excluded_groups=None):
        """
        Return the status data of the service.
        """
        if excluded_groups is None:
            excluded_groups = set()
        if groups is None:
            groups = set(DEFAULT_STATUS_GROUPS)

        status = {}
        moregroups = groups | set(["overall", "avail"])
        groups = groups - excluded_groups
        self.get_rset_status(groups)

        # initialise status of each group
        for group in moregroups:
            status[group] = rcStatus.Status(rcStatus.NA)

        for driver in [_driver for _driver in STATUS_TYPES if \
                  not _driver.startswith('sync') and \
                  not _driver.startswith('task') and \
                  not _driver.startswith('stonith')]:
            if driver in excluded_groups:
                continue
            group = driver.split('.')[0]
            if group not in groups:
                continue
            for resource in self.get_resources(driver):
                rstatus = resource.status()
                status[group] += rstatus
                status["avail"] += rstatus

        if status["avail"].status == rcStatus.STDBY_UP_WITH_UP:
            status["avail"].status = rcStatus.UP
            # now that we now the avail status we can promote
            # stdbyup to up
            for group in status:
                if status[group] == rcStatus.STDBY_UP:
                    status[group] = rcStatus.UP
        elif status["avail"].status == rcStatus.STDBY_UP_WITH_DOWN:
            status["avail"].status = rcStatus.STDBY_UP

        # overall status is avail + accessory resources status
        # seed overall with avail
        status["overall"] = rcStatus.Status(status["avail"])

        for resource in self.get_resources():
            group = resource.type.split(".")[0]
            if group not in groups:
                continue
            if resource.status_logs_count(levels=["warn", "error"]) > 0:
                status["overall"] += rcStatus.WARN
                break

        for driver in [_driver for _driver in STATUS_TYPES if \
                       _driver.startswith('stonith')]:
            if 'stonith' not in groups:
                continue
            if driver in excluded_groups:
                continue
            for resource in self.get_resources(driver):
                rstatus = resource.status()
                status['stonith'] += rstatus
                status["overall"] += rstatus

        for driver in [_driver for _driver in STATUS_TYPES if \
                       _driver.startswith('sync')]:
            if 'sync' not in groups:
                continue
            if driver in excluded_groups:
                continue
            for resource in self.get_resources(driver):
                #" sync are expected to be up
                rstatus = resource.status()
                status['sync'] += rstatus
                if rstatus == rcStatus.UP:
                    status["overall"] += rcStatus.UNDEF
                elif rstatus in [rcStatus.NA, rcStatus.UNDEF]:
                    status["overall"] += rstatus
                else:
                    status["overall"] += rcStatus.WARN

        for driver in [_driver for _driver in STATUS_TYPES if \
                       _driver.startswith('task')]:
            if 'task' not in groups:
                continue
            if driver in excluded_groups:
                continue
            for resource in self.get_resources(driver):
                rstatus = resource.status()
                status['task'] += rstatus
                status["overall"] += rstatus

        self.group_status_cache = status
        return status

    def print_disklist(self):
        """
        Print the list of disks the service handles.
        """
        if self.options.format is not None:
            return self.print_disklist_data()
        disks = self.disklist()
        if len(disks) > 0:
            print('\n'.join(disks))

    def print_devlist(self):
        """
        Print the list of devices the service handles.
        """
        if self.options.format is not None:
            return self.print_devlist_data()
        devs = self.devlist()
        if len(devs) > 0:
            print('\n'.join(devs))

    def print_disklist_data(self):
        """
        Return the list of disks the service handles.
        """
        return list(self.disklist())

    def print_devlist_data(self):
        """
        Return the list of devices the service handles.
        """
        return list(self.devlist())

    def disklist(self):
        """
        Return the set of disks the service handles, from cache if possible.
        """
        if len(self.disks) == 0:
            self.disks = self._disklist()
        return self.disks

    def _disklist(self):
        """
        Return the set of disks the service handles.
        """
        disks = set()
        for resource in self.get_resources():
            if resource.skip:
                continue
            disks |= resource.disklist()
        self.log.debug("found disks %s held by service", disks)
        return disks

    def devlist(self, filtered=True):
        """
        Return the set of devices the service handles, from cache if possible.
        """
        if len(self.devs) == 0:
            self.devs = self._devlist(filtered=filtered)
        return self.devs

    def _devlist(self, filtered=True):
        """
        Return the set of devices the service handles.
        """
        devs = set()
        for resource in self.get_resources():
            if filtered and resource.skip:
                continue
            devs |= resource.devlist()
        self.log.debug("found devs %s held by service", devs)
        return devs

    def print_config_mtime(self):
        """
        Print the service configuration file last modified timestamp. Used by
        remote agents to determine which agent holds the most recent version.
        """
        mtime = os.stat(self.paths.cf).st_mtime
        print(mtime)

    def async_action(self, action):
        states = {
            "start": "started",
            "stop": "stopped",
            "freeze": "frozen",
            "thaw": "thawed",
        }
        if self.options.crm:
            return
        if action not in states:
            return
        if self.command_is_scoped():
            return
        self.set_service_monitor(global_expect=states[action])
        self.log.info("%s action requested", action)
        if not self.options.wait:
            raise ex.excAbortAction()

        # poll global service status
        prev_global_expect_set = set()
        for _ in range(self.options.time):
            data = self.node._daemon_status()
            global_expect_set = []
            for nodename in data["monitor"]["nodes"]:
                try:
                    _data = data["monitor"]["nodes"][nodename]["services"]["status"][self.svcname]
                except (KeyError, TypeError) as exc:
                    continue
                if _data["monitor"].get("global_expect") == states[action]:
                    global_expect_set.append(nodename)
            if prev_global_expect_set != global_expect_set:
                for nodename in set(global_expect_set) - prev_global_expect_set:
                    self.log.info("work starting on %s", nodename)
                for nodename in prev_global_expect_set - set(global_expect_set):
                    self.log.info("work over on %s", nodename)
            if not global_expect_set:
                self.log.info("final status: avail=%s overall=%s frozen=%s",
                              data["monitor"]["services"][self.svcname]["avail"],
                              data["monitor"]["services"][self.svcname]["overall"],
                              data["monitor"]["services"][self.svcname]["frozen"])
                raise ex.excAbortAction()
            prev_global_expect_set = set(global_expect_set)
            time.sleep(1)
        raise ex.excError("wait timeout exceeded")

    def boot(self):
        """
        The 'boot' action entrypoint.
        Start errors cause a fallback to startstandby as a best effort.
        """
        self.startstandby()

    def shutdown(self):
        self.options.force = True
        self.slave_shutdown()
        try:
            self.master_shutdownapp()
        except ex.excError:
            pass
        self.shutdowncontainer()
        self.master_shutdownshare()
        self.master_shutdownfs()
        self.master_shutdownip()

    def command_is_scoped(self, options=None):
        """
        Return True if a resource filter has been setup through
        --rid, --subsets or --tags
        """
        if options is None:
            options = self.options
        if (options.rid is not None and options.rid != "") or \
           (options.tags is not None and options.tags != "") or \
           (options.subsets is not None and options.subsets != ""):
            return True
        return False

    def run_task(self, rid):
        if self.sched.skip_action(rid):
            return
        options = Storage(self.options)
        options.rid = [rid]
        self.action("run", options)

    def run(self):
        self.master_run()
        self.slave_run()

    @_master_action
    def master_run(self):
        self.sub_set_action("task", "run")

    @_slave_action
    def slave_run(self):
        self.encap_cmd(['run'], verbose=True)

    def start(self):
        self.abort_start()
        self.master_startip()
        self.master_startfs()
        self.master_startshare()
        self.master_startcontainer()
        self.master_startapp()
        self.slave_start()

    @_slave_action
    def slave_start(self):
        self.encap_cmd(['start'], verbose=True)

    def rollback(self):
        self.encap_cmd(['rollback'], verbose=True)
        try:
            self.rollbackapp()
        except ex.excError:
            pass
        self.rollbackcontainer()
        self.rollbackshare()
        self.rollbackfs()
        self.rollbackip()

    def stop(self):
        self.slave_stop()
        try:
            self.master_stopapp()
        except ex.excError:
            pass
        self.stopcontainer()
        self.master_stopshare()
        self.master_stopfs()
        self.master_stopip()

    @_slave_action
    def slave_shutdown(self):
        self.encap_cmd(['shutdown'], verbose=True, error="continue")

    @_slave_action
    def slave_stop(self):
        self.encap_cmd(['stop'], verbose=True, error="continue")

    def startdisk(self):
        self.master_startdisk()
        self.slave_startdisk()

    @_slave_action
    def slave_startdisk(self):
        self.encap_cmd(['startdisk'], verbose=True)

    @_master_action
    def master_startstandbydisk(self):
        self.sub_set_action("sync.netapp", "startstandby")
        self.sub_set_action("sync.dcsckpt", "startstandby")
        self.sub_set_action("sync.nexenta", "startstandby")
        self.sub_set_action("sync.symclone", "startstandby")
        self.sub_set_action("sync.symsnap", "startstandby")
        self.sub_set_action("sync.ibmdssnap", "startstandby")
        self.sub_set_action("disk.scsireserv", "startstandby", xtags=set(['zone']))
        self.sub_set_action(DISK_TYPES, "startstandby", xtags=set(['zone']))

    @_master_action
    def master_startdisk(self):
        self.sub_set_action("sync.netapp", "start")
        self.sub_set_action("sync.dcsckpt", "start")
        self.sub_set_action("sync.nexenta", "start")
        self.sub_set_action("sync.symclone", "start")
        self.sub_set_action("sync.symsnap", "start")
        self.sub_set_action("sync.symsrdfs", "start")
        self.sub_set_action("sync.hp3par", "start")
        self.sub_set_action("sync.ibmdssnap", "start")
        self.sub_set_action("disk.scsireserv", "start", xtags=set(['zone']))
        self.sub_set_action(DISK_TYPES, "start", xtags=set(['zone']))

    def stopdisk(self):
        self.slave_stopdisk()
        self.master_stopdisk()

    @_slave_action
    def slave_stopdisk(self):
        self.encap_cmd(['stopdisk'], verbose=True)

    @_master_action
    def master_stopdisk(self):
        self.sub_set_action("sync.btrfssnap", "stop")
        self.sub_set_action(DISK_TYPES, "stop", xtags=set(['zone']))
        self.sub_set_action("disk.scsireserv", "stop", xtags=set(['zone']))

    @_master_action
    def master_shutdowndisk(self):
        self.sub_set_action("sync.btrfssnap", "shutdown")
        self.sub_set_action(DISK_TYPES, "shutdown", xtags=set(['zone']))
        self.sub_set_action("disk.scsireserv", "shutdown", xtags=set(['zone']))

    def rollbackdisk(self):
        self.sub_set_action(DISK_TYPES, "rollback", xtags=set(['zone']))
        self.sub_set_action("disk.scsireserv", "rollback", xtags=set(['zone']))

    def abort_start(self):
        """
        Give a chance to all resources concerned by the action to voice up
        their rebutal of the action before it begins.
        """
        self.abort_start_done = True
        if rcEnv.sysname == "Windows":
            parallel = False
        else:
            try:
                from multiprocessing import Process
                parallel = True
                def wrapper(func):
                    if func():
                        sys.exit(1)
            except ImportError:
                parallel = False

        procs = {}
        for resource in self.get_resources():
            if resource.skip or resource.disabled:
                continue
            if not hasattr(resource, 'abort_start'):
                continue
            if not parallel:
                if resource.abort_start():
                    raise ex.excError("start aborted due to resource %s "
                                      "conflict" % resource.rid)
            else:
                proc = Process(target=wrapper, args=[resource.abort_start])
                proc.start()
                procs[resource.rid] = proc

        if parallel:
            err = []
            for rid, proc in procs.items():
                proc.join()
                if proc.exitcode > 0:
                    err.append(rid)
            if len(err) > 0:
                raise ex.excError("start aborted due to resource %s "
                                  "conflict" % ",".join(err))

    def startip(self):
        self.master_startip()
        self.slave_startip()

    @_slave_action
    def slave_startip(self):
        self.encap_cmd(['startip'], verbose=True)

    @_master_action
    def master_startstandbyip(self):
        self.sub_set_action("ip", "startstandby", xtags=set(['zone', 'docker']))

    @_master_action
    def master_startip(self):
        self.sub_set_action("ip", "start", xtags=set(['zone', 'docker']))

    def stopip(self):
        self.slave_stopip()
        self.master_stopip()

    @_slave_action
    def slave_stopip(self):
        self.encap_cmd(['stopip'], verbose=True)

    @_master_action
    def master_stopip(self):
        self.sub_set_action("ip", "stop", xtags=set(['zone', 'docker']))

    @_master_action
    def master_shutdownip(self):
        self.sub_set_action("ip", "shutdown", xtags=set(['zone', 'docker']))

    def rollbackip(self):
        self.sub_set_action("ip", "rollback", xtags=set(['zone', 'docker']))

    def startshare(self):
        self.master_startshare()
        self.slave_startshare()

    @_master_action
    def master_startshare(self):
        self.sub_set_action("share.nfs", "start")

    @_master_action
    def master_startstandbyshare(self):
        self.sub_set_action("share", "startstandby")

    @_slave_action
    def slave_startshare(self):
        self.encap_cmd(['startshare'], verbose=True)

    def stopshare(self):
        self.slave_stopshare()
        self.master_stopshare()

    @_master_action
    def master_stopshare(self):
        self.sub_set_action("share", "stop")

    @_master_action
    def master_shutdownshare(self):
        self.sub_set_action("share", "shutdown")

    @_slave_action
    def slave_stopshare(self):
        self.encap_cmd(['stopshare'], verbose=True)

    def rollbackshare(self):
        self.sub_set_action("share", "rollback")

    def startfs(self):
        self.master_startfs()
        self.slave_startfs()

    @_master_action
    def master_startfs(self):
        self.master_startdisk()
        self.sub_set_action("fs", "start", xtags=set(['zone']))

    @_master_action
    def master_startstandbyfs(self):
        self.master_startstandbydisk()
        self.sub_set_action("fs", "startstandby", xtags=set(['zone']))

    @_slave_action
    def slave_startfs(self):
        self.encap_cmd(['startfs'], verbose=True)

    def stopfs(self):
        self.slave_stopfs()
        self.master_stopfs()

    @_master_action
    def master_stopfs(self):
        self.sub_set_action("fs", "stop", xtags=set(['zone']))
        self.master_stopdisk()

    @_master_action
    def master_shutdownfs(self):
        self.sub_set_action("fs", "shutdown", xtags=set(['zone']))
        self.master_shutdowndisk()

    @_slave_action
    def slave_stopfs(self):
        self.encap_cmd(['stopfs'], verbose=True)

    def rollbackfs(self):
        self.sub_set_action("fs", "rollback", xtags=set(['zone']))
        self.rollbackdisk()

    def startcontainer(self):
        self.abort_start()
        self.master_startcontainer()

    @_master_action
    def master_startstandbycontainer(self):
        self.sub_set_action("container", "startstandby")
        self.refresh_ip_status()

    @_master_action
    def master_startcontainer(self):
        self.sub_set_action("container", "start")
        self.refresh_ip_status()

    def refresh_ip_status(self):
        """ Used after start/stop container because the ip resource
            status change after its own start/stop
        """
        for resource in self.get_resources("ip"):
            resource.status(refresh=True)

    @_master_action
    def shutdowncontainer(self):
        self.sub_set_action("container", "shutdown")
        self.refresh_ip_status()

    @_master_action
    def stopcontainer(self):
        self.sub_set_action("container", "stop")
        self.refresh_ip_status()

    def rollbackcontainer(self):
        self.sub_set_action("container", "rollback")
        self.refresh_ip_status()

    def unprovision(self):
        self.sub_set_action("app", "unprovision")
        self.sub_set_action("container", "unprovision")
        self.sub_set_action("fs", "unprovision", xtags=set(['zone']))
        self.sub_set_action("disk", "unprovision", xtags=set(['zone']))
        self.sub_set_action("ip", "unprovision", xtags=set(['zone', 'docker']))

    def provision(self):
        self.sub_set_action("ip", "provision", xtags=set(['zone', 'docker']))
        self.sub_set_action("disk", "provision", xtags=set(['zone']))
        self.sub_set_action("fs", "provision", xtags=set(['zone']))
        self.sub_set_action("container", "provision")
        self.sub_set_action("app", "provision")
        self.push()

    def startapp(self):
        self.master_startapp()
        self.slave_startapp()

    @_slave_action
    def slave_startapp(self):
        self.encap_cmd(['startapp'], verbose=True)

    @_master_action
    def master_startstandbyapp(self):
        self.sub_set_action("app", "startstandby")

    @_master_action
    def master_startapp(self):
        self.sub_set_action("app", "start")

    def stopapp(self):
        self.slave_stopapp()
        self.master_stopapp()

    @_slave_action
    def slave_stopapp(self):
        self.encap_cmd(['stopapp'], verbose=True)

    @_master_action
    def master_stopapp(self):
        self.sub_set_action("app", "stop")

    @_master_action
    def master_shutdownapp(self):
        self.sub_set_action("app", "shutdown")

    def rollbackapp(self):
        self.sub_set_action("app", "rollback")

    def prstop(self):
        self.slave_prstop()
        self.master_prstop()

    @_slave_action
    def slave_prstop(self):
        self.encap_cmd(['prstop'], verbose=True)

    @_master_action
    def master_prstop(self):
        self.sub_set_action("disk.scsireserv", "scsirelease")

    def prstart(self):
        self.master_prstart()
        self.slave_prstart()

    @_slave_action
    def slave_prstart(self):
        self.encap_cmd(['prstart'], verbose=True)

    @_master_action
    def master_prstart(self):
        self.sub_set_action("disk.scsireserv", "scsireserv")

    def prstatus(self):
        self.sub_set_action("disk.scsireserv", "scsicheckreserv")

    def startstandby(self):
        self.master_startstandby()
        self.slave_startstandby()

    @_master_action
    def master_startstandby(self):
        self.master_startstandbyip()
        self.master_startstandbyfs()
        self.master_startstandbyshare()
        self.master_startstandbycontainer()
        self.master_startstandbyapp()

    @_slave_action
    def slave_startstandby(self):
        cmd = ['startstandby']
        for container in self.get_resources('container'):
            if not container.is_up() and \
               rcEnv.nodename not in container.always_on:
                # no need to try to startstandby the encap service on a
                # container we not activated
                continue
            try:
                self._encap_cmd(cmd, container, verbose=True)
            except ex.excError:
                self.log.error("container %s is not joinable to execute "
                               "action '%s'", container.name, ' '.join(cmd))
                raise

    def dns_update(self):
        """
        Call the dns update method of each resource.
        """
        self.all_set_action("dns_update")

    def postsync(self):
        """ action triggered by a remote master node after
            sync_nodes and sync_drp. Typically make use of files
            received in var/
        """
        self.all_set_action("postsync")

    def remote_postsync(self):
        """ Release the svc lock at this point because the
            waitlock timeout is long and we are done touching
            local data.

            Action triggered by a remote master node after
            sync_nodes and sync_drp. Typically make use of files
            received in var/.
            use a long waitlock timeout to give a chance to
            remote syncs to finish
        """
        self.svcunlock()
        for nodename in self.need_postsync:
            self.remote_action(nodename, 'postsync', waitlock=3600)

        self.need_postsync = set()

    def remote_action(self, nodename, action, waitlock=DEFAULT_WAITLOCK,
                      sync=False, verbose=True, action_mode=True):
        if self.options.cron:
            # the scheduler action runs forked. don't use the cmdworker
            # in this context as it may hang
            sync = True

        rcmd = [os.path.join(rcEnv.paths.pathetc, self.svcname)]
        if self.options.debug:
            rcmd += ['--debug']
        if self.options.dry_run:
            rcmd += ['--dry-run']
        if self.options.crm and action_mode:
            rcmd += ['--crm']
        if self.options.cron:
            rcmd += ['--cron']
        if self.options.waitlock != DEFAULT_WAITLOCK:
            rcmd += ['--waitlock', str(waitlock)]
        rcmd += action.split()
        cmd = rcEnv.rsh.split() + [nodename] + rcmd
        if verbose:
            self.log.info("exec '%s' on node %s", ' '.join(rcmd), nodename)
        if sync:
            out, err, ret = justcall(cmd)
            return out, err, ret
        else:
            self.node.cmdworker.enqueue(cmd)

    def presync(self):
        """ prepare files to send to slave nodes in var/.
            Each resource can prepare its own set of files.
        """
        self.need_postsync = set()
        if self.presync_done:
            return
        self.all_set_action("presync")
        self.presync_done = True

    def sync_nodes(self):
        rtypes = [
            "sync.rsync",
            "sync.zfs",
            "sync.btrfs",
            "sync.docker",
            "sync.dds",
        ]
        if not self.can_sync(rtypes, 'nodes'):
            return
        self.presync()
        for rtype in rtypes:
            self.sub_set_action(rtype, "sync_nodes")
        self.remote_postsync()

    def sync_drp(self):
        rtypes = [
            "sync.rsync",
            "sync.zfs",
            "sync.btrfs",
            "sync.docker",
            "sync.dds",
        ]
        if not self.can_sync(rtypes, 'drpnodes'):
            return
        self.presync()
        for rtype in rtypes:
            self.sub_set_action(rtype, "sync_drp")
        self.remote_postsync()

    def syncswap(self):
        self.sub_set_action("sync.netapp", "syncswap")
        self.sub_set_action("sync.symsrdfs", "syncswap")
        self.sub_set_action("sync.hp3par", "syncswap")
        self.sub_set_action("sync.nexenta", "syncswap")

    def sync_revert(self):
        self.sub_set_action("sync.hp3par", "sync_revert")

    def sync_resume(self):
        self.sub_set_action("sync.netapp", "sync_resume")
        self.sub_set_action("sync.symsrdfs", "sync_resume")
        self.sub_set_action("sync.hp3par", "sync_resume")
        self.sub_set_action("sync.dcsckpt", "sync_resume")
        self.sub_set_action("sync.nexenta", "sync_resume")

    def sync_quiesce(self):
        self.sub_set_action("sync.netapp", "sync_quiesce")
        self.sub_set_action("sync.nexenta", "sync_quiesce")

    def resync(self):
        self.stop()
        self.sync_resync()
        self.start()

    def sync_resync(self):
        self.sub_set_action("sync.netapp", "sync_resync")
        self.sub_set_action("sync.nexenta", "sync_resync")
        self.sub_set_action("sync.rados", "sync_resync")
        self.sub_set_action("sync.dds", "sync_resync")
        self.sub_set_action("sync.symclone", "sync_resync")
        self.sub_set_action("sync.symsnap", "sync_resync")
        self.sub_set_action("sync.ibmdssnap", "sync_resync")
        self.sub_set_action("sync.evasnap", "sync_resync")
        self.sub_set_action("sync.necismsnap", "sync_resync")
        self.sub_set_action("sync.dcssnap", "sync_resync")

    def sync_break(self):
        self.sub_set_action("sync.netapp", "sync_break")
        self.sub_set_action("sync.nexenta", "sync_break")
        self.sub_set_action("sync.hp3par", "sync_break")
        self.sub_set_action("sync.dcsckpt", "sync_break")
        self.sub_set_action("sync.symclone", "sync_break")
        self.sub_set_action("sync.symsnap", "sync_break")

    def sync_update(self):
        self.sub_set_action("sync.netapp", "sync_update")
        self.sub_set_action("sync.hp3par", "sync_update")
        self.sub_set_action("sync.hp3parsnap", "sync_update")
        self.sub_set_action("sync.nexenta", "sync_update")
        self.sub_set_action("sync.dcsckpt", "sync_update")
        self.sub_set_action("sync.dds", "sync_update")
        self.sub_set_action("sync.btrfssnap", "sync_update")
        self.sub_set_action("sync.zfssnap", "sync_update")
        self.sub_set_action("sync.s3", "sync_update")
        self.sub_set_action("sync.symclone", "sync_update")
        self.sub_set_action("sync.symsnap", "sync_update")
        self.sub_set_action("sync.ibmdssnap", "sync_update")

    def sync_full(self):
        self.sub_set_action("sync.dds", "sync_full")
        self.sub_set_action("sync.zfs", "sync_full")
        self.sub_set_action("sync.btrfs", "sync_full")
        self.sub_set_action("sync.s3", "sync_full")

    def sync_restore(self):
        self.sub_set_action("sync.s3", "sync_restore")
        self.sub_set_action("sync.symclone", "sync_restore")
        self.sub_set_action("sync.symsnap", "sync_restore")

    def sync_split(self):
        self.sub_set_action("sync.symsrdfs", "sync_split")

    def sync_establish(self):
        self.sub_set_action("sync.symsrdfs", "sync_establish")

    def sync_verify(self):
        self.sub_set_action("sync.dds", "sync_verify")

    def print_config(self):
        """
        The 'print config' action entry point.
        Print the service configuration in the format specified by --format.
        """
        if self.options.format is not None:
            return self.print_config_data()
        from rcColor import print_color_config
        print_color_config(self.paths.cf)

    def make_temp_config(self):
        """
        Copy the current service configuration file to a temporary
        location for edition.
        If the temp file already exists, propose the --discard
        or --recover options.
        """
        import shutil
        path = os.path.join(rcEnv.paths.pathtmp, self.svcname+".conf.tmp")
        if os.path.exists(path):
            if self.options.recover:
                pass
            elif self.options.discard:
                shutil.copy(self.paths.cf, path)
            else:
                raise ex.excError("%s exists: service is already being edited. "
                                  "Set --discard to edit from the current "
                                  "configuration, or --recover to open the "
                                  "unapplied config" % path)
        else:
            shutil.copy(self.paths.cf, path)
        return path

    def edit_config(self):
        """
        Execute an editor on the service configuration file.
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
        from rcUtilities import which
        if not which(editor):
            print("%s not found" % editor, file=sys.stderr)
            return 1
        path = self.make_temp_config()
        os.environ["LANG"] = "en_US.UTF-8"
        os.system(' '.join((editor, path)))
        results = self._validate_config(path=path)
        if results["errors"] == 0:
            import shutil
            shutil.copy(path, self.paths.cf)
            os.unlink(path)
        else:
            print("your changes were not applied because of the errors "
                  "reported above. you can use the edit config command "
                  "with --recover to try to fix your changes or with "
                  "--discard to restart from the live config")
        return results["errors"] + results["warnings"]

    def can_sync(self, rtypes=None, target=None):
        """
        Return True if any resource of type in <rtypes> yields it can sync.
        """
        if rtypes is None:
            rtypes = []
        ret = False
        for rtype in rtypes:
            for resource in self.get_resources(rtype):
                try:
                    ret |= resource.can_sync(target)
                except ex.excError:
                    return False
                if ret:
                    return True
        self.log.debug("nothing to sync for the service for now")
        return False

    def sched_sync_all(self):
        """
        The 'sync_all' scheduler task entrypoint.
        """
        data = self.sched.skip_action("sync_all", deferred_write_timestamp=True)
        if len(data["keep"]) == 0:
            return
        self._sched_sync_all(data["keep"])

    @scheduler_fork
    def _sched_sync_all(self, sched_options):
        """
        Call the sync_all method of each sync resources that passed the
        scheduler constraints.
        """
        options = Storage(self.options)
        options.rid = [option.section for option in sched_options]
        self.action("sync_all", options)
        self.sched.sched_write_timestamp(sched_options)

    def sync_all(self):
        """
        The 'sync all' action entrypoint.
        """
        if not self.can_sync(["sync"]):
            return
        if self.options.cron:
            self.sched.sched_delay()
        self.presync()
        self.sub_set_action("sync.rsync", "sync_nodes")
        self.sub_set_action("sync.zfs", "sync_nodes")
        self.sub_set_action("sync.btrfs", "sync_nodes")
        self.sub_set_action("sync.docker", "sync_nodes")
        self.sub_set_action("sync.dds", "sync_nodes")
        self.sub_set_action("sync.rsync", "sync_drp")
        self.sub_set_action("sync.zfs", "sync_drp")
        self.sub_set_action("sync.btrfs", "sync_drp")
        self.sub_set_action("sync.docker", "sync_drp")
        self.sub_set_action("sync.dds", "sync_drp")
        self.sync_update()
        self.remote_postsync()

    def push_service_status(self):
        """
        The 'push_service_status' scheduler task and action entrypoint.

        This method returns early if called from an encapsulated agent, as
        the master agent is responsible for pushing the encapsulated
        status.
        """
        if self.encap:
            if not self.options.cron:
                self.log.info("push service status is disabled for encapsulated services")
            return
        if self.sched.skip_action("push_service_status"):
            return
        self.task_push_service_status()

    @scheduler_fork
    def task_push_service_status(self):
        """
        Refresh and push the service status to the collector.
        """
        if self.options.cron:
            self.sched.sched_delay()
        self.options.refresh = True
        data = self.svcmon_push_lists()
        self.node.collector.call('svcmon_update_combo', *data, sync=True)

    def push_resinfo(self):
        """
        The 'push_resinfo' scheduler task and action entrypoint.
        """
        if self.sched.skip_action("push_resinfo"):
            return
        self.task_push_resinfo()

    @scheduler_fork
    def task_push_resinfo(self):
        """
        Push the per-resource key/value pairs to the collector.
        """
        if self.options.cron:
            self.sched.sched_delay()
        self.node.collector.call('push_resinfo', [self])

    def push_config(self):
        """
        The 'push_config' scheduler task entrypoint.
        """
        if self.sched.skip_action("push_config"):
            return
        self.push()

    def create_var_subdir(self):
        """
        Create the service-dedicated subdir in <pathvar>.
        """
        var_d = os.path.join(rcEnv.paths.pathvar, self.svcname)
        if not os.path.exists(var_d):
            os.makedirs(var_d)

    def autopush(self):
        """
        If the configuration file has been modified since the last push
        to the collector, call the push method.
        """
        if not self.collector_outdated():
            return
        if len(self.log.handlers) > 1:
            self.log.handlers[1].setLevel(logging.CRITICAL)
        try:
            self.push()
        finally:
            if len(self.log.handlers) > 1:
                self.log.handlers[1].setLevel(rcEnv.loglevel)

    @scheduler_fork
    def push(self):
        """
        The 'push' action entrypoint.
        Synchronize the configuration file between encap and master agent,
        then send the configuration to the collector.
        Finally update the last push on-disk timestamp.
        This action is skipped when run by an encapsulated agent.
        """
        if self.encap:
            return
        if self.options.cron:
            self.sched.sched_delay()
        self.push_encap_config()
        self.node.collector.call('push_all', [self])
        self.log.info("send %s to collector", self.paths.cf)
        try:
            self.create_var_subdir()
            import time
            with open(self.paths.push_flag, 'w') as ofile:
                ofile.write(str(time.time()))
            self.log.info("update %s timestamp", self.paths.push_flag)
        except (OSError, IOError):
            self.log.error("failed to update %s timestamp", self.paths.push_flag)

    def push_encap_config(self):
        """
        Verify the service has an encapsulated part, and if so, for each
        container in up state running an encapsulated part, synchronize the
        service configuration file.
        """
        if self.encap or not self.has_encap_resources:
            return

        for resource in self.get_resources('container'):
            if resource.status(ignore_nostatus=True) not in (rcStatus.STDBY_UP, rcStatus.UP):
                continue
            self._push_encap_config(resource)

    def _push_encap_config(self, container):
        """
        Compare last modification time of the master and slave service
        configuration file, and copy the most recent version over the least
        recent.
        """
        cmd = ['print', 'config', 'mtime']
        try:
            cmd_results = self._encap_cmd(cmd, container)
            out = cmd_results[0]
            ret = cmd_results[2]
        except ex.excError:
            out = None
            ret = 1

        paths = get_osvc_paths(osvc_root_path=container.osvc_root_path,
                               sysname=container.guestos)
        encap_cf = os.path.join(paths.pathetc, os.path.basename(self.paths.cf))

        if out == "":
            # this is what happens when the container is down
            return

        if ret == 0:
            encap_mtime = int(float(out.strip()))
            local_mtime = int(os.stat(self.paths.cf).st_mtime)
            if encap_mtime > local_mtime:
                if hasattr(container, 'rcp_from'):
                    cmd_results = container.rcp_from(encap_cf, rcEnv.paths.pathetc+'/')
                else:
                    cmd = rcEnv.rcp.split() + [container.name+':'+encap_cf, rcEnv.paths.pathetc+'/']
                    cmd_results = justcall(cmd)
                os.utime(self.paths.cf, (encap_mtime, encap_mtime))
                self.log.info("fetch %s from %s", encap_cf, container.name)
                if cmd_results[2] != 0:
                    raise ex.excError()
                return
            elif encap_mtime == local_mtime:
                return

        if hasattr(container, 'rcp'):
            cmd_results = container.rcp(self.paths.cf, encap_cf)
        else:
            cmd = rcEnv.rcp.split() + [self.paths.cf, container.name+':'+encap_cf]
            cmd_results = justcall(cmd)
        if cmd_results[2] != 0:
            raise ex.excError("failed to send %s to %s" % (self.paths.cf, container.name))
        self.log.info("send %s to %s", self.paths.cf, container.name)

        cmd = ['create', '--config', encap_cf]
        cmd_results = self._encap_cmd(cmd, container=container)
        if cmd_results[2] != 0:
            raise ex.excError("failed to create %s slave service" % container.name)
        self.log.info("create %s slave service", container.name)

    @staticmethod
    def _tag_match(rtags, keeptags):
        """
        Return True if any tag of <rtags> is in <keeptags>.
        """
        for tag in rtags:
            if tag in keeptags:
                return True
        return False

    def set_skip_resources(self, keeprid=None, xtags=None):
        """
        Set the 'skip' flag of all resources.
        * set to False if keeprid is empty and xtags is empty
        * set to False if rid is in keeprid and not in xtags
        * else set to True
        """
        if keeprid is None:
            keeprid = []

        if xtags is None:
            xtags = set()

        ridfilter = len(keeprid) > 0
        tagsfilter = len(xtags) > 0

        if not tagsfilter and not ridfilter:
            return

        for resource in self.get_resources():
            if self._tag_match(resource.tags, xtags):
                resource.skip = True
            if ridfilter and resource.rid in keeprid:
                continue
            resource.skip = True

    def setup_environ(self, action=None):
        """
        Setup envionment variables.
        Startup scripts and triggers can use them, so their code can be
        more generic.
        All resources can contribute a set of env variables through their
        own setup_environ() method.
        """
        os.environ['OPENSVC_SVCNAME'] = self.svcname
        if action:
            os.environ['OPENSVC_ACTION'] = action
        for resource in self.get_resources():
            resource.setup_environ()

    def all_rids(self):
        return [rid for rid in self.resources_by_id if rid is not None] + \
               list(self.encap_resources.keys())

    def expand_rid(self, rid):
        """
        Given a rid return a set containing either the rid itself if it is
        a known rid, or containing the rid of all resources whose prefix
        matches the name given as rid.
        """
        retained_rids = set()
        for _rid in self.all_rids():
            if '#' in rid:
                if _rid == rid:
                    retained_rids.add(_rid)
                else:
                    continue
            elif _rid[:_rid.index('#')] == rid:
                retained_rids.add(_rid)
        return retained_rids

    def expand_rids(self, rids):
        """
        Parse the --rid value and return the retained corresponding resource
        ids.
        Filter out non existing resource ids.
        If a rid has no "#", expand to the set of rids of resources whose
        prefix matches the name given as a rid.

        Example:
        --rid disk: return all rids of disk resources.
        --rid disk#0: return disk#0 if such a resource exists
        """
        if len(rids) == 0:
            return
        retained_rids = set()
        for rid in set(rids):
            retained_rids |= self.expand_rid(rid)
        if len(retained_rids) > 0:
            self.log.debug("rids added from --rid %s: %s", ",".join(rids),
                           ",".join(retained_rids))
        return retained_rids

    def expand_subsets(self, subsets):
        """
        Parse the --subsets value and return the retained corresponding resource
        ids.
        """
        if subsets is None or self.options.subsets is None:
            return
        retained_rids = set()
        for resource in self.resources_by_id.values() + self.encap_resources.values():
            if resource.subset in subsets:
                retained_rids.add(resource.rid)
        if len(retained_rids) > 0:
            self.log.debug("rids added from --subsets %s: %s",
                           ",".join(subsets), ",".join(retained_rids))
        return retained_rids

    def expand_tags(self, tags):
        """
        Parse the --tags value and return the retained corresponding resource
        ids.
        ',' is interpreted as OR
        '+' is interpreted as AND
        '+' are evaluated before ','

        Example:
        --tags A,B : return rids of resource with either tag A or B
        --tags A+B : return rids of resource with both tags A and B
        --tags A+B,B+C : return rids of resource with either tags A and B
                         or tags B and C
        """
        if len(tags) == 0 or tags is None:
            return
        retained_rids = set()
        unions = []
        intersection = []
        for idx, tag in enumerate(tags):
            if tag[0] == "+":
                tag = tag[1:]
                intersection.append(tag)
                if idx == len(tags) - 1:
                    unions.append(intersection)
            else:
                if len(intersection) > 0:
                    # new intersection, store the current
                    unions.append(intersection)
                # open a new intersection
                intersection = [tag]
                if idx == len(tags) - 1:
                    unions.append(intersection)

        for intersection in unions:
            for resource in self.resources_by_id.values() + self.encap_resources.values():
                if set(intersection) & resource.tags == set(intersection):
                    retained_rids.add(resource.rid)
        if len(retained_rids) > 0:
            self.log.debug("rids added from --tags %s: %s", ",".join(tags),
                           ",".join(retained_rids))
        return retained_rids

    def always_on_resources(self):
        """
        Return the list of resources flagged always on on this node
        """
        return [resource for resource in self.resources_by_id.values()
                if rcEnv.nodename in resource.always_on]

    def prepare_options(self, options):
        """
        Return a Storage() from command line options or dict passed as
        <options>, sanitized, merge with default values in self.options.
        """
        if options is None:
            options = Storage()
        elif isinstance(options, dict):
            options = Storage(options)

        if is_string(options.slave):
            options.slave = options.slave.split(',')

        if isinstance(options.resource, list):
            for idx, resource in enumerate(options.resource):
                if not is_string(resource):
                    continue
                try:
                    options.resource[idx] = json.loads(resource)
                except ValueError:
                    raise ex.excError("invalid json in resource definition: "
                                      "%s" % options.resource[idx])

        self.options.update(options)
        options = self.options

        return options

    def options_to_rids(self, options):
        """
        Return the list of rids to apply an action to, from the command
        line options passed as <options>.
        """
        rid = options.get("rid", None)
        tags = options.get("tags", None)
        subsets = options.get("subsets", None)
        xtags = options.get("xtags", None)

        if rid is None:
            rid = []
        elif is_string(rid):
            rid = rid.split(',')

        if tags is None:
            tags = []
        elif is_string(tags):
            tags = tags.replace("+", ",+").split(',')

        if subsets is None:
            subsets = []
        elif is_string(subsets):
            subsets = subsets.split(',')

        if xtags is None:
            xtags = set()
        elif is_string(xtags):
            xtags = xtags.split(',')

        if len(self.resources_by_id.keys()) > 0:
            rids = set(self.all_rids())

            # --rid
            retained_rids = self.expand_rids(rid)
            if retained_rids is not None:
                rids &= retained_rids

            # --subsets
            retained_rids = self.expand_subsets(subsets)
            if retained_rids is not None:
                rids &= retained_rids

            # --tags
            retained_rids = self.expand_tags(tags)
            if retained_rids is not None:
                rids &= retained_rids

            rids = list(rids)
            self.log.debug("rids retained after expansions intersection: %s",
                           ",".join(rids))

            if self.command_is_scoped(options) and len(rids) == 0:
                raise ex.excAbortAction("no resource match the given --rid, --subset "
                                        "and --tags specifiers")
        else:
            # no resources certainly mean the build was done with minimal=True
            # let the action go on. 'delete', for one, takes a --rid but does
            # not need resource initialization
            rids = rid

        return rids

    def action(self, action, options=None):
        self.allow_on_this_node(action)
        options = self.prepare_options(options)
        try:
            self.async_action(action)
        except ex.excAbortAction:
            return
        return self._action(action, options=options)

    def _action(self, action, options=None):
        """
        Filter resources on which the service action must act.
        Abort if the service is frozen, or if --cluster is not set on a HA
        service.
        Set up the environment variables.
        Finally do the service action either in logged or unlogged mode.
        """

        try:
            self.action_rid_before_depends = self.options_to_rids(options)
        except ex.excAbortAction as exc:
            self.log.info(exc)
            return

        depends = set()
        for rid in self.action_rid_before_depends:
            depends |= self.action_rid_dependencies(action, rid) - set(self.action_rid_before_depends)

        if len(depends) > 0:
            self.log.info("add rid %s to satisfy dependencies" % ", ".join(depends))
            self.action_rid = list(set(self.action_rid_before_depends) | depends)
        else:
            self.action_rid = list(self.action_rid_before_depends)

        self.action_rid_depends = list(depends)

        self.action_start_date = datetime.datetime.now()

        if self.node is None:
            self.node = node.Node()

        if self.svc_env != 'PRD' and rcEnv.node_env == 'PRD':
            self.log.error("Abort action for non PRD service on PRD node")
            return 1

        if action not in ACTIONS_NO_STATUS_CHANGE and \
           'compliance' not in action and \
           'collector' not in action:
            #
            # here we know we will run a resource state-changing action
            # purge the resource status file cache, so that we don't take
            # decision on outdated information
            #
            if not options.dry_run and action != "resource_monitor" and \
               not action.startswith("docker"):
                self.log.debug("purge all resource status file caches")
                self.purge_status_last()

        self.setup_environ(action=action)
        self.setup_signal_handlers()
        self.set_skip_resources(keeprid=self.action_rid, xtags=options.xtags)
        if action.startswith("print_") or \
           action.startswith("collector") or \
           action.startswith("json_"):
            return self.do_print_action(action, options)
        if action in ACTIONS_NO_LOG or \
           action.startswith("compliance") or \
           action.startswith("docker") or \
           options.dry_run:
            err = self.do_action(action, options)
        else:
            err = self.do_logged_action(action, options)
        return err

    def do_print_action(self, action, options):
        """
        Call the service method associated with action. This method produces
        data the caller will print.
        If --cluster is set, execute the action on remote nodes and
        aggregate the results.
        """
        _action = action + ""
        if action.startswith("json_"):
            action = "print_"+action[5:]
            self.node.options.format = "json"
            self.options.format = "json"
            options.format = "json"

        if "_json_" in action:
            action = action.replace("_json_", "_")
            self.node.options.format = "json"
            self.options.format = "json"
            options.format = "json"

        if options.cluster and options.format != "json":
            raise ex.excError("only the json output format is allowed with --cluster")

        if action.startswith("collector_"):
            from collector import Collector
            collector = Collector(options, self.node, self.svcname)
            func = getattr(collector, action)
        else:
            func = getattr(self, action)

        if not hasattr(func, "__call__"):
            raise ex.excError("%s is not callable" % action)

        psinfo = self.do_cluster_action(_action, collect=True, action_mode=False)

        try:
            data = func()
        except Exception as exc:
            data = {"error": str(exc)}

        if psinfo:
            # --cluster is set and we have remote responses
            results = self.join_cluster_action(**psinfo)
            for nodename in results:
                results[nodename] = results[nodename][0]
                if options.format == "json":
                    try:
                        results[nodename] = json.loads(results[nodename])
                    except ValueError as exc:
                        results[nodename] = {"error": str(exc)}
            results[rcEnv.nodename] = data
            return results
        elif options.cluster:
            # no remote though --cluster is set
            results = {}
            results[rcEnv.nodename] = data
            return results

        return data

    def do_cluster_action(self, action, waitlock=60, collect=False, action_mode=True):
        """
        Execute an action on remote nodes if --cluster is set and the
        service is a flex, and this node is flex primary.

        edit config, validate config, and sync* are never executed through
        this method.

        If possible execute in parallel running subprocess. Aggregate and
        return results.
        """
        if not self.options.cluster:
            return

        if action in ("edit_config", "validate_config") or "sync" in action:
            return

        if action_mode and "flex" not in self.clustertype:
            return

        if "flex" in self.clustertype:
            if rcEnv.nodename == self.drp_flex_primary:
                peers = set(self.drpnodes) - set([rcEnv.nodename])
            elif rcEnv.nodename == self.flex_primary:
                peers = set(self.nodes) - set([rcEnv.nodename])
            else:
                return
        elif not action_mode:
            if rcEnv.nodename in self.nodes:
                peers = set(self.nodes) | set(self.drpnodes)
            else:
                peers = set(self.drpnodes)
            peers -= set([rcEnv.nodename])

        args = [arg for arg in sys.argv[1:] if arg not in ("-c", "--cluster")]
        if self.options.docker_argv and len(self.options.docker_argv) > 0:
            args += self.options.docker_argv

        def wrapper(queue, **kwargs):
            """
            Execute the remote action and enqueue or print results.
            """
            collect = kwargs["collect"]
            del kwargs["collect"]
            out, err, ret = self.remote_action(**kwargs)
            if collect:
                queue.put([out, err, ret])
            else:
                if len(out):
                    print(out)
                if len(err):
                    print(err)
            return out, err, ret

        if rcEnv.sysname == "Windows":
            parallel = False
        else:
            try:
                from multiprocessing import Process, Queue
                parallel = True
                results = None
                procs = {}
                queues = {}
            except ImportError:
                parallel = False
                results = {}
                procs = None
                queues = None

        for nodename in peers:
            kwargs = {
                "nodename": nodename,
                "action": " ".join(args),
                "waitlock": waitlock,
                "verbose": False,
                "sync": True,
                "action_mode": action_mode,
                "collect": collect,
            }
            if parallel:
                queues[nodename] = Queue()
                proc = Process(target=wrapper, args=(queues[nodename],), kwargs=kwargs)
                proc.start()
                procs[nodename] = proc
            else:
                results[nodename] = wrapper(**kwargs)
        return {"procs": procs, "queues": queues, "results": results}

    @staticmethod
    def join_cluster_action(procs=None, queues=None, results=None):
        """
        Wait for subprocess to finish, aggregate and return results.
        """
        if procs is None or queues is None:
            return results
        results = {}
        joined = []
        while len(joined) < len(procs):
            for nodename, proc in procs.items():
                proc.join(0.1)
                if not proc.is_alive():
                    joined.append(nodename)
                queue = queues[nodename]
                if not queue.empty():
                    results[nodename] = queue.get()
        return results

    def do_action(self, action, options):
        """
        Acquire the service action lock, call the service action method,
        handles its errors, and finally release the lock.

        If --cluster is set, and the service is a flex, and we are
        flex_primary run the action on all remote nodes.
        """

        if action not in ACTIONS_NO_LOCK and self.clustertype not in CLUSTER_TYPES:
            raise ex.excError("invalid cluster type '%s'. allowed: %s" % (
                self.clustertype,
                ', '.join(CLUSTER_TYPES),
            ))

        err = 0
        if options.waitlock >= 0:
            waitlock = options.waitlock
        else:
            waitlock = self.lock_timeout

        try:
            self.svclock(action, timeout=waitlock)
        except ex.excError as exc:
            self.log.error(str(exc))
            return 1

        psinfo = self.do_cluster_action(action, options)

        def call_action(action):
            self.action_triggers("pre", action)
            self.action_triggers("blocking_pre", action, blocking=True)
            err = getattr(self, action)()
            self.action_triggers("post", action)
            self.action_triggers("blocking_post", action, blocking=True)
            return err

        try:
            if action.startswith("compliance_"):
                err = getattr(self.compliance, action)()
            elif hasattr(self, action):
                self.running_action = action
                err = call_action(action)
                if err is None:
                    err = 0
            else:
                self.log.error("unsupported action %s", action)
                err = 1
        except ex.excEndAction as exc:
            msg = "'%s' action ended by last resource" % action
            if len(str(exc)) > 0:
                msg += ": %s" % str(exc)
            self.log.info(msg)
            err = 0
        except ex.excAbortAction as exc:
            msg = "'%s' action aborted by last resource" % action
            if len(str(exc)) > 0:
                msg += ": %s" % str(exc)
            self.log.info(msg)
            err = 0
        except ex.excError as exc:
            msg = "'%s' action stopped on execution error" % action
            self.log.debug(msg)
            msg = str(exc)
            if len(msg) > 0:
                self.log.error(msg)
            err = 1
            self.rollback_handler(action)
        except ex.excSignal:
            self.log.error("interrupted by signal")
            err = 1
        except ex.MonitorAction:
            self.svcunlock()
            raise
        except:
            err = 1
            self.save_exc()
        finally:
            self.running_action = None

        self.svcunlock()

        if not (action == "delete" and not self.command_is_scoped()):
            self.update_status_data()

        if psinfo:
            self.join_cluster_action(**psinfo)

        return err

    def rollback_handler(self, action):
        """
        Call the rollback method if
        * the action triggering this handler is a start*
        * service is not configured to not disable rollback
        * --disable-rollback is not set
        * at least one resource has been flagged rollbackable during the
          start* action
        """
        if 'start' not in action:
            return
        if self.options.disable_rollback:
            self.log.info("skip rollback %s: as instructed by --disable-rollback", action)
            return
        if self.disable_rollback:
            self.log.info("skip rollback %s: as instructed by DEFAULT.rollback=false", action)
            return
        rids = [r.rid for r in self.get_resources() if r.can_rollback and not r.always_on]
        if len(rids) == 0:
            self.log.info("skip rollback %s: no resource activated", action)
            return
        self.log.info("trying to rollback %s on %s", action, ', '.join(rids))
        try:
            self.rollback()
        except ex.excError:
            self.log.error("rollback %s failed", action)

    def do_logged_action(self, action, options):
        """
        Setup action logging to a machine-readable temp logfile, in preparation
        to the collector feeding.
        Do the action.
        Finally, feed the log to the collector.
        """
        import tempfile
        begin = datetime.datetime.now()

        # Provision a database entry to store action log later
        if action in ('postsync', 'shutdown'):
            # don't loose the action log on node shutdown
            # no background dblogger for remotely triggered postsync
            self.sync_dblogger = True
        self.node.collector.call('begin_action', self, action, begin,
                                 sync=self.sync_dblogger)

        # Per action logfile to push to database at the end of the action
        tmpfile = tempfile.NamedTemporaryFile(delete=False, dir=rcEnv.paths.pathtmp,
                                              prefix=self.svcname+'.'+action)
        actionlogfile = tmpfile.name
        tmpfile.close()
        log = logging.getLogger()
        fmt = "%(asctime)s;;%(name)s;;%(levelname)s;;%(message)s;;%(process)d;;EOL"
        actionlogformatter = logging.Formatter(fmt)
        actionlogfilehandler = logging.FileHandler(actionlogfile)
        actionlogfilehandler.setFormatter(actionlogformatter)
        actionlogfilehandler.setLevel(logging.INFO)
        log.addHandler(actionlogfilehandler)
        if "/svcmgr.py" in sys.argv:
            self.log.info(" ".join(sys.argv))

        err = self.do_action(action, options)

        # Push result and logs to database
        actionlogfilehandler.close()
        log.removeHandler(actionlogfilehandler)
        end = datetime.datetime.now()
        self.dblogger(action, begin, end, actionlogfile)
        return err

    def restart(self):
        """
        The 'restart' action entrypoint.
        This action translates into 'stop' followed by 'start'
        """
        self.stop()
        self.start()

    def _migrate(self):
        """
        Call the migrate action on all relevant resources.
        """
        self.sub_set_action("container.ovm", "_migrate")
        self.sub_set_action("container.hpvm", "_migrate")
        self.sub_set_action("container.esx", "_migrate")

    def destination_node_sanity_checks(self):
        """
        Raise an excError if
        * the destination node --to arg not set
        * the specified destination is the current node
        * the specified destination is not a service candidate node
        """
        if self.options.destination_node is None:
            raise ex.excError("a destination node must be provided this action")
        if self.options.destination_node == rcEnv.nodename:
            raise ex.excError("the destination is the source node")
        if self.options.destination_node not in self.nodes:
            raise ex.excError("the destination node %s is not in the service "
                              "nodes list" % self.options.destination_node)

    @_master_action
    def migrate(self):
        """
        Service online migration.
        """
        self.destination_node_sanity_checks()
        self.master_prstop()
        try:
            self.remote_action(nodename=self.options.destination_node, action='startfs --master')
            self._migrate()
        except:
            if self.has_resourceset(['disk.scsireserv']):
                self.log.error("scsi reservations were dropped. you have to "
                               "acquire them now using the 'prstart' action "
                               "either on source node or destination node, "
                               "depending on your problem analysis.")
            raise
        self.master_stopfs()
        self.remote_action(nodename=self.options.destination_node, action='prstart --master')

    def switch(self):
        """
        Service move to another node.
        """
        self.destination_node_sanity_checks()
        self.stop()
        self.remote_action(nodename=self.options.destination_node, action='start')

    def collector_rest_get(self, *args, **kwargs):
        kwargs["svcname"] = self.svcname
        return self.node.collector_rest_get(*args, **kwargs)

    def collector_rest_post(self, *args, **kwargs):
        kwargs["svcname"] = self.svcname
        return self.node.collector_rest_post(*args, **kwargs)

    def collector_rest_put(self, *args, **kwargs):
        kwargs["svcname"] = self.svcname
        return self.node.collector_rest_put(*args, **kwargs)

    def collector_rest_delete(self, *args, **kwargs):
        kwargs["svcname"] = self.svcname
        return self.node.collector_rest_delete(*args, **kwargs)

    def collector_outdated(self):
        """
        Return True if the configuration file has changed since last push.
        """
        if self.encap:
            return False

        if not os.path.exists(self.paths.push_flag):
            self.log.debug("no last push timestamp found")
            return True

        if not os.path.exists(self.paths.cf):
            # happens in 'pull' action codepath
            self.log.debug("no config file found")
            return False

        try:
            mtime = os.stat(self.paths.cf).st_mtime
            with open(self.paths.push_flag) as flag:
                last_push = float(flag.read())
        except (ValueError, IOError, OSError):
            self.log.error("can not read timestamp from %s or %s",
                           self.paths.cf, self.paths.push_flag)
            return True
        if mtime > last_push:
            self.log.debug("configuration file changed since last push")
            return True
        return False

    def write_config(self):
        """
        Rewrite the service configuration file, using the current parser
        object in self.config write method.
        Also reset the file mode to 644.
        """
        import tempfile
        import shutil
        try:
            tmpfile = tempfile.NamedTemporaryFile()
            fname = tmpfile.name
            tmpfile.close()
            with open(fname, "w") as tmpfile:
                self.config.write(tmpfile)
            shutil.move(fname, self.paths.cf)
        except (OSError, IOError) as exc:
            print("failed to write new %s (%s)" % (self.paths.cf, str(exc)),
                  file=sys.stderr)
            raise ex.excError()
        try:
            os.chmod(self.paths.cf, 0o0644)
        except (OSError, IOError) as exc:
            self.log.debug("failed to set %s mode: %s", self.paths.cf, str(exc))

    @lazy
    def config(self):
        """
        Initialize the service configuration parser object. Using an
        OrderDict type to preserve the options and sections ordering,
        if possible.

        The parser object is a opensvc-specified class derived from
        optparse.RawConfigParser.
        """
        return read_cf(self.paths.cf)

    def unset(self):
        """
        The 'unset' action entrypoint.
        Verifies the --param and --value are set, set DEFAULT as section
        if no section was specified, and finally call the _unset internal
        method.
        """
        if self.options.param is None:
            print("no parameter. set --param", file=sys.stderr)
            return 1
        elements = self.options.param.split('.')
        if len(elements) == 1:
            elements.insert(0, "DEFAULT")
        elif len(elements) != 2:
            print("malformed parameter. format as 'section.key'", file=sys.stderr)
            return 1
        section, option = elements
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
        section = "[%s]" % section
        lines = self._read_cf().splitlines()

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

        buff = "\n".join(lines) + "\n"

        try:
            self._write_cf(buff)
        except (IOError, OSError) as exc:
            raise ex.excError(str(exc))

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
        except ex.excError as exc:
            print(exc, file=sys.stderr)
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
        if len(elements) == 1:
            elements.insert(0, "DEFAULT")
        elif len(elements) != 2:
            raise ex.excError("malformed parameter. format as 'section.key'")
        section, option = elements
        if section != 'DEFAULT' and not self.config.has_section(section):
            raise ex.excError("section [%s] not found" % section)
        if not self.config.has_option(section, option):
            raise ex.excError("option '%s' not found in section [%s]" % (option, section))
        if self.options.eval:
            return self.conf_get(section, option, "string", scope=True)
        else:
            return self.config.get(section, option)

    def set(self):
        """
        The 'set' action entrypoint.
        Verifies the --param and --value are set, set DEFAULT as section
        if no section was specified, and set the value using the internal
        _set() method.
        """
        if self.options.param is None:
            print("no parameter. set --param", file=sys.stderr)
            return 1
        if self.options.value is not None:
            value = self.options.value
        elif self.options.remove is not None:
            value = self._get(self.options.param, self.options.eval).split()
            if self.options.remove not in value:
                return 0
            value.remove(self.options.remove)
            value = " ".join(value)
        elif self.options.add is not None:
            value = self._get(self.options.param, self.options.eval).split()
            if self.options.add in value:
                return 0
            index = self.options.index if self.options.index is not None else len(value)
            value.insert(index, self.options.add)
            value = " ".join(value)
        else:
            print("no value. set --value or --remove", file=sys.stderr)
            return 1
        elements = self.options.param.split('.')
        if len(elements) == 1:
            elements.insert(0, "DEFAULT")
        elif len(elements) != 2:
            print("malformed parameter. format as 'section.key'", file=sys.stderr)
            return 1
        try:
            self._set(elements[0], elements[1], value)
        except ex.excError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0

    def setenv(self, args, interactive=False):
        """
        For each option in the 'env' section of the configuration file,
        * rewrite the value using the value specified in a corresponding
          --env <option>=<value> commandline arg
        * or prompt for the value if --interactive is set, and rewrite
        * or leave the value as is, considering the default is accepted
        """
        explicit_options = []

        for arg in args:
            idx = arg.index("=")
            option = arg[:idx]
            value = arg[idx+1:]
            self._set("env", option, value)
            explicit_options.append(option)

        if not interactive:
            return

        if not os.isatty(0):
            raise ex.excError("--interactive is set but input fd is not a tty")

        def get_href(ref):
            ref = ref.strip("[]")
            try:
                response = node.urlopen(ref)
                return response.read()
            except:
                return ""

        def print_comment(comment):
            """
            Print a env keyword comment. For use in the interactive service
            create codepath.
            """
            import re
            comment = re.sub("(\[.+://.+])", lambda m: get_href(m.group(1)), comment)
            print(comment)

        for key, default_val in self.env_section_keys().items():
            if key.endswith(".comment"):
                continue
            if key in explicit_options:
                continue
            if self.config.has_option("env", key+".comment"):
                print_comment(self.config.get("env", key+".comment"))
            newval = raw_input("%s [%s] > " % (key, str(default_val)))
            if newval != "":
                self._set("env", key, newval)

    def _set(self, section, option, value):
        """
        Set <option> to <value> in <section> of the configuration file.
        """
        section = "[%s]" % section
        lines = self._read_cf().splitlines()
        done = False
        in_section = False
        value = try_decode(value)

        for idx, line in enumerate(lines):
            sline = line.strip()
            if sline == section:
                in_section = True
            elif in_section:
                if sline.startswith("[") and not done:
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
                        return

                    lines[idx] = "%s = %s" % (option, value)
                    section_idx = idx

                    while section_idx < len(lines)-1 and \
                          "=" not in lines[section_idx+1] and \
                          not lines[section_idx+1].strip().startswith("[") and \
                          lines[section_idx+1].strip() != "":
                        del lines[section_idx+1]

                    done = True

        if not done:
            while lines[-1].strip() == "":
                lines.pop()
            if not in_section:
                # section in last position and no option => add section
                lines.append("")
                lines.append(section)
            lines.append("%s = %s" % (option, value))

        buff = "\n".join(lines) + "\n"

        try:
            self._write_cf(buff)
        except (IOError, OSError) as exc:
            raise ex.excError(str(exc))

    def set_disable(self, rids=None, disable=True):
        """
        Set the disable to <disable> (True|False) in the configuration file,
        * at DEFAULT level if no resources were specified
        * in each resource section if resources were specified
        """
        if rids is None:
            rids = []

        if not self.command_is_scoped() and \
           (len(rids) == 0 or len(rids) == len(self.resources_by_id)):
            rids = ['DEFAULT']

        for rid in rids:
            if rid != 'DEFAULT' and not self.config.has_section(rid):
                self.log.error("service %s has no resource %s", self.svcname, rid)
                continue
            self.log.info("set %s.disable = %s", rid, str(disable))
            self.config.set(rid, "disable", str(disable).lower())

        #
        # if we set DEFAULT.disable = True,
        # we don't want res#n.disable = False
        #
        if rids == ["DEFAULT"] and disable:
            for section in self.config.sections():
                if self.config.has_option(section, "disable") and \
                   not self.config.getboolean(section, "disable"):
                    self.log.info("remove %s.disable = false", section)
                    self.config.remove_option(section, "disable")

        try:
            self.write_config()
        except (IOError, OSError) as exc:
            self.log.error(str(exc))
            return 1

        return 0

    def enable(self):
        """
        The 'enable' action entrypoint.
        """
        return self.set_disable(self.action_rid, False)

    def disable(self):
        """
        The 'disable' action entrypoint.
        """
        return self.set_disable(self.action_rid, True)

    def delete_service_logs(self):
        """
        Delete the service configuration logs
        """
        import glob
        patterns = [
            os.path.join(rcEnv.paths.pathlog, self.svcname+".log*"),
            os.path.join(rcEnv.paths.pathlog, self.svcname+".debug.log*"),
            os.path.join(rcEnv.paths.pathvar, "FROZEN."+self.svcname),
        ]
        for pattern in patterns:
            for fpath in glob.glob(pattern):
                self.log.info("remove %s", fpath)
                os.unlink(fpath)

    def delete_service_conf(self):
        """
        Delete the service configuration files
        """
        import shutil
        dpaths = [
            os.path.join(rcEnv.paths.pathetc, self.svcname+".dir"),
            os.path.join(rcEnv.paths.pathetc, self.svcname+".d"),
            os.path.join(rcEnv.paths.pathvar, self.svcname),
        ]
        fpaths = [
            self.paths.cf,
            os.path.join(rcEnv.paths.pathetc, self.svcname),
            os.path.join(rcEnv.paths.pathetc, self.svcname+".d"),
            os.path.join(rcEnv.paths.pathetc, self.svcname+".cluster"),
            os.path.join(rcEnv.paths.pathetc, self.svcname+".stonith"),
        ]
        for fpath in fpaths:
            if os.path.exists(fpath) and \
               (os.path.islink(fpath) or os.path.isfile(fpath)):
                self.log.info("remove %s", fpath)
                os.unlink(fpath)
        for dpath in dpaths:
            if os.path.exists(dpath):
                self.log.info("remove %s", dpath)
                shutil.rmtree(dpath)

    def delete_resources(self):
        """
        Delete service resources from its configuration file
        """
        lines = self._read_cf().splitlines()
        need_write = False

        for rid in self.action_rid:
            section = "[%s]" % rid
            in_section = False
            for i, line in enumerate(lines):
                sline = line.strip()
                if sline == section:
                    in_section = True
                    need_write = True
                    del lines[i]
                    while i < len(lines) and not lines[i].strip().startswith("["):
                        del lines[i]

            if not in_section:
                print("service", self.svcname, "has no resource", rid, file=sys.stderr)

        if not need_write:
            return

        buff = "\n".join(lines)

        try:
            self._write_cf(buff)
        except (IOError, OSError):
            raise ex.excError("failed to rewrite %s" % self.paths.cf)

    def delete(self):
        """
        The 'delete' action entrypoint.
        If --unprovision is set, call the unprovision method.
        Then if no resource specifier is set, remove all service files in
        <pathetc>.
        If a resource specifier is set, only delete the corresponding
        sections in the configuration file.
        """
        if self.options.unprovision:
            self.unprovision()

        if not self.command_is_scoped() or \
           len(self.action_rid) == len(self.resources_by_id.keys()):
            self.delete_service_conf()
            self.delete_service_logs()
        else:
            self.delete_resources()

    def docker(self):
        """
        The 'docker' action entry point.
        Parse the docker argv and substitute known patterns before relaying
        the argv to the docker command.
        Set the socket to point the service-private docker daemon if
        the service has such a daemon.
        """
        import subprocess
        containers = self.get_resources('container.docker')
        if self.options.docker_argv is None:
            print("no docker command arguments supplied", file=sys.stderr)
            return 1

        def subst(argv):
            """
            Parse the docker argv and substitute known patterns.
            """
            import re
            for idx, arg in enumerate(argv):
                if arg in ("%instances%", "{instances}"):
                    del argv[idx]
                    instances = [resource.container_name for resource in containers
                                 if not resource.skip and not resource.disabled]
                    for instance in instances:
                        argv.insert(idx, instance)
            for idx, arg in enumerate(argv):
                if arg in ("%images%", "{images}"):
                    del argv[idx]
                    images = list(set([resource.run_image for resource in containers
                                       if not resource.skip and not resource.disabled]))
                    for image in images:
                        argv.insert(idx, image)
            for idx, arg in enumerate(argv):
                if arg in ("%as_service%", "{as_service}"):
                    del argv[idx]
                    argv[idx:idx] = ["-u", self.svcname+"@"+rcEnv.nodename]
                    argv[idx:idx] = ["-p", self.node.config.get("node", "uuid")]
                    if self.dockerlib.docker_min_version("1.12"):
                        pass
                    elif self.dockerlib.docker_min_version("1.10"):
                        argv[idx:idx] = ["--email", self.svcname+"@"+rcEnv.nodename]
            for idx, arg in enumerate(argv):
                if re.match(r'\{container#\w+\}', arg):
                    container_name = self.svcname + "." + arg.strip("{}").replace("#", ".")
                    del argv[idx]
                    argv.insert(idx, container_name)
            return argv

        if len(containers) == 0:
            print("this service has no docker resource", file=sys.stderr)
            return 1

        self.dockerlib.docker_start(verbose=False)
        cmd = self.dockerlib.docker_cmd + subst(self.options.docker_argv)
        proc = subprocess.Popen(cmd)
        proc.communicate()
        return proc.returncode

    def freezestop(self):
        """
        The freezestop monitor action.
        """
        self.freeze()
        self.options.force = True
        self.stop()

    def freeze(self):
        """
        Set the frozen flag.
        """
        self.freezer.freeze()

    def thaw(self):
        """
        Unset the frozen flag.
        """
        self.freezer.thaw()

    def frozen(self):
        """
        Return True if the service is frozen.
        """
        return self.freezer.frozen()

    def pull(self):
        """
        Pull a service configuration from the collector, installs it and
        create the svcmgr link.
        """
        data = self.node.collector_rest_get("/services/"+self.svcname+"?props=svc_config&meta=0")
        if "error" in data:
            raise ex.excError(data["error"])
        if len(data["data"]) == 0:
            raise ex.excError("service not found on the collector")
        if len(data["data"][0]["svc_config"]) == 0:
            raise ex.excError("service has an empty configuration")
        buff = data["data"][0]["svc_config"].replace("\\n", "\n").replace("\\t", "\t")
        import codecs
        with codecs.open(self.paths.cf, "w", "utf8") as ofile:
            ofile.write(buff)
        self.log.info("%s pulled", self.paths.cf)
        self.node.install_service_files(self.svcname)

        if self.options.provision:
            self.action("provision")

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
        from svcDict import KeyDict, deprecated_sections
        from svcBuilder import build
        from rcUtilities import convert_size
        try:
            import ConfigParser
        except ImportError:
            import configparser as ConfigParser

        data = KeyDict(provision=True)
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
            Fetch the value and convert it to expected type.
            """
            _option = option.split("@")[0]
            value = self.conf_get_string_scope(section, _option, config=config)
            if isinstance(key.default, bool):
                return bool(value)
            elif isinstance(key.default, int):
                try:
                    return int(value)
                except ValueError:
                    # might be a size string like 11mib
                    return convert_size(value)
            return value

        def check_candidates(key, section, option, value):
            """
            Verify the specified option value is in allowed candidates.
            """
            if key.strict_candidates and key.candidates and value not in key.candidates:
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
            except ex.OptNotFound:
                return 0
            err += check_candidates(key, section, option, value)
            return err

        def validate_default_options(config, data, ret):
            """
            Validate DEFAULT section options.
            """
            for option in config.defaults():
                key = data.sections["DEFAULT"].getkey(option)
                if key is None:
                    found = False
                    # the option can be set in the DEFAULT section for the
                    # benefit of a resource section
                    for section in config.sections():
                        family = section.split("#")[0]
                        if family not in list(data.sections.keys()) + \
                           list(deprecated_sections.keys()):
                            continue
                        if family in deprecated_sections:
                            results = deprecated_sections[family]
                            family = results[0]
                        if data.sections[family].getkey(option) is not None:
                            found = True
                            break
                    if not found:
                        self.log.warning("ignored option DEFAULT.%s", option)
                        ret["warnings"] += 1
                else:
                    # here we know its a native DEFAULT option
                    ret["errors"] += check_known_option(key, "DEFAULT", option)
            return ret

        def validate_resources_options(config, data, ret):
            """
            Validate resource sections options.
            """
            for section in config.sections():
                if section == "env":
                    # the "env" section is not handled by a resource driver, and is
                    # unknown to the svcDict. Just ignore it.
                    continue
                family = section.split("#")[0]
                if config.has_option(section, "type"):
                    rtype = config.get(section, "type")
                else:
                    rtype = None
                if family not in list(data.sections.keys()) + list(deprecated_sections.keys()):
                    self.log.warning("ignored section %s", section)
                    ret["warnings"] += 1
                    continue
                if family in deprecated_sections:
                    self.log.warning("deprecated section prefix %s", family)
                    ret["warnings"] += 1
                    family, rtype = deprecated_sections[family]
                for option in config.options(section):
                    if option in config.defaults():
                        continue
                    key = data.sections[family].getkey(option, rtype=rtype)
                    if key is None:
                        key = data.sections[family].getkey(option)
                    if key is None:
                        self.log.warning("ignored option %s.%s, driver %s", section,
                                         option, rtype if rtype else "generic")
                        ret["warnings"] += 1
                    else:
                        ret["errors"] += check_known_option(key, section, option)
            return ret

        def validate_build(path, ret):
            """
            Try a service build to catch errors missed in other tests.
            """
            try:
                build(self.svcname, svcconf=path)
            except Exception as exc:
                self.log.error("the new configuration causes the following "
                               "build error: %s", str(exc))
                ret["errors"] += 1
            return ret

        ret = validate_default_options(config, data, ret)
        ret = validate_resources_options(config, data, ret)
        ret = validate_build(path, ret)

        return ret

    def save_exc(self):
        """
        A helper method to save stacks in the service log.
        """
        self.log.error("unexpected error. stack saved in the service debug log")
        self.log.debug("", exc_info=True)

    def vcall(self, *args, **kwargs):
        """
        Wrap vcall, setting the service logger
        """
        kwargs["log"] = self.log
        return vcall(*args, **kwargs)

    def lcall(self, *args, **kwargs):
        """
        Wrap lcall, setting the service logger
        """
        kwargs["logger"] = self.log
        return lcall(*args, **kwargs)

    def _read_cf(self):
        """
        Return the service config file content.
        """
        import codecs
        with codecs.open(self.paths.cf, "r", "utf8") as ofile:
            buff = ofile.read()
        return buff

    def _write_cf(self, buff):
        """
        Truncate the service config file and write buff.
        """
        import codecs
        import tempfile
        import shutil
        ofile = tempfile.NamedTemporaryFile(delete=False, dir=rcEnv.paths.pathtmp, prefix=self.svcname)
        fpath = ofile.name
        os.chmod(fpath, 0o0644)
        ofile.close()
        with codecs.open(fpath, "w", "utf8") as ofile:
            ofile.write(buff)
        shutil.move(fpath, self.paths.cf)

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

    def update(self):
        """
        The 'update' action entry point.
        Add resources to the service configuration, and provision them if
        instructed to do so.
        """
        sections = {}
        rtypes = {}
        defaults = self.config.defaults()
        for section in self.config.sections():
            sections[section] = {}
            elements = section.split('#')
            if len(elements) == 2:
                rtype = elements[0]
                ridx = elements[1]
                if rtype not in rtypes:
                    rtypes[rtype] = set([])
                rtypes[rtype].add(ridx)
            for option, value in self.config.items(section):
                if option in list(defaults.keys()) + ['rtype']:
                    continue
                sections[section][option] = value

        import svcBuilder
        from svcDict import KeyDict, MissKeyNoDefault, KeyInvalidValue

        keys = KeyDict(provision=self.options.provision)
        rid = []

        for data in self.options.resource:
            is_resource = False
            if 'rid' in data:
                section = data['rid']
                if '#' not in section:
                    raise ex.excError("%s must be formatted as 'rtype#n'" % section)
                elements = section.split('#')
                if len(elements) != 2:
                    raise ex.excError("%s must be formatted as 'rtype#n'" % section)
                del data['rid']
                if section in sections:
                    sections[section].update(data)
                else:
                    sections[section] = data
                is_resource = True
            elif 'rtype' in data and data["rtype"] == "env":
                del data["rtype"]
                if "env" in sections:
                    sections["env"].update(data)
                else:
                    sections["env"] = data
            elif 'rtype' in data and data["rtype"] != "DEFAULT":
                section = self.allocate_rid(data['rtype'], sections)
                self.log.info("allocated rid %s" % section)
                del data['rtype']
                sections[section] = data
                is_resource = True
            else:
                if "rtype" in data:
                    del data["rtype"]
                defaults.update(data)

            if is_resource:
                try:
                    sections[section].update(keys.update(section, data))
                except (MissKeyNoDefault, KeyInvalidValue) as exc:
                    if not self.options.interactive:
                        raise ex.excError(str(exc))
                rid.append(section)

        for section, data in sections.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
            for key, val in data.items():
                self.config.set(section, key, val)

        self.write_config()

        for section in rid:
            group = section.split("#")[0]
            svcBuilder.add_resource(self, group, section)

        if self.options.provision and len(rid) > 0:
            options = Storage(self.options)
            options.rid = rid
            self.action("provision", options)

    def allow_on_this_node(self, action):
        """
        Raise excError if the service is not allowed to run on this node.
        In other words, the nodename is not a service node or drpnode, nor the
        service mode is cloud proxy.
        """
        if action in ACTIONS_ALLOW_ON_INVALID_NODE:
            return
        if self.type in rcEnv.vt_cloud:
            return
        if rcEnv.nodename in self.nodes:
            return
        if rcEnv.nodename in self.drpnodes:
            return
        raise ex.excError("action '%s' aborted because this node's hostname "
                          "'%s' is not a member of DEFAULT.nodes, "
                          "DEFAULT.drpnode nor DEFAULT.drpnodes" % \
                          (action, rcEnv.nodename))

    def compliance_auto(self):
        if self.sched.skip_action("compliance_auto"):
            return
        self.action("compliance_auto")

    #########################################################################
    #
    # daemon communications
    #
    #########################################################################
    def clear(self):
        options = {
            "svcname": self.svcname,
        }
        data = self.daemon_send(
            {"action": "clear", "options": options},
            nodename=self.options.node,
        )
        print(json.dumps(data, indent=4, sort_keys=True))

    def set_service_monitor(self, status=None, local_expect=None, global_expect=None):
        options = {
            "svcname": self.svcname,
            "status": status,
            "local_expect": local_expect,
            "global_expect": global_expect,
        }
        try:
            data = self.daemon_send(
                {"action": "set_service_monitor", "options": options},
                nodename=self.options.node,
                silent=True,
            )
            if data and data["status"] != 0:
                self.log.warning("set monitor status failed")
        except Exception as exc:
            self.log.warning("set monitor status failed: %s", str(exc))


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
            if ref == "svcname":
                return self.svcname
            if ref == "short_svcname":
                return self.svcname.split(".")[0]
            if ref == "svcmgr":
                return rcEnv.paths.svcmgr
            if ref == "nodemgr":
                return rcEnv.paths.nodemgr

            if "[" in ref and ref.endswith("]"):
                i = ref.index("[")
                index = ref[i+1:-1]
                ref = ref[:i]
                index = int(self.handle_references(index, scope=scope, impersonate=impersonate))
            else:
                index = None

            # use DEFAULT as the implicit section
            n_dots = ref.count(".")
            if n_dots == 0:
                _section = "DEFAULT"
                _v = ref
            elif n_dots == 1:
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

            val = self._handle_reference(ref, _section, _v, scope=scope, impersonate=impersonate, config=config)

            if return_length:
                return str(len(val.split()))

            if not index is None:
                return val.split()[index]

            return val

    def _handle_reference(self, ref, _section, _v, scope=False, impersonate=None, config=None):
        if config is None:
            config = self.config
        # give os env precedence over the env cf section
        if _section == "env" and _v.upper() in os.environ:
            return os.environ[_v.upper()]

        if _section == "node":
            # go fetch the reference in the node.conf [node] section
            if self.node is None:
                from node import Node
                self.node = Node()
            try:
                return self.node.config.get("node", _v)
            except Exception as exc:
                raise ex.excError("%s: unresolved reference (%s)" % (ref, str(exc)))

        if _section != "DEFAULT" and not config.has_section(_section):
            raise ex.excError("%s: section %s does not exist" % (ref, _section))

        try:
            return self.conf_get(_section, _v, "string", scope=scope, impersonate=impersonate, config=config)
        except ex.OptNotFound as exc:
            raise ex.excError("%s: unresolved reference (%s)" % (ref, str(exc)))

        raise ex.excError("%s: unknown reference" % ref)

    def _handle_references(self, s, scope=False, impersonate=None, config=None):
        if not is_string(s):
            return s
        while True:
            m = re.search(r'{\w*[\w#][\w\.\[\]]*}', s)
            if m is None:
                return s
            ref = m.group(0).strip("{}")
            val = self.handle_reference(ref, scope=scope, impersonate=impersonate, config=config)
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
            val = eval_expr(expr)
            s = s[:m.start()] + str(val) + s[m.end():]

    def handle_references(self, s, scope=False, impersonate=None, config=None):
        key = (s, scope, impersonate)
        if hasattr(self, "ref_cache") and self.ref_cache is not None and key in self.ref_cache:
            return self.ref_cache[key]
        try:
            val = self._handle_references(s, scope=scope, impersonate=impersonate, config=config)
            val = self._handle_expressions(val)
            val = self._handle_references(val, scope=scope, impersonate=impersonate, config=config)
        except Exception as e:
            raise ex.excError("%s: reference evaluation failed: %s" %(s, str(e)))
        if hasattr(self, "ref_cache") and self.ref_cache is not None:
            self.ref_cache[key] = val
        return val

    def conf_get(self, s, o, t, scope=False, impersonate=None, use_default=True, config=None):
        if not scope:
            val = self.conf_get_val_unscoped(s, o, use_default=use_default, config=config)
        else:
            val = self.conf_get_val_scoped(s, o, impersonate=impersonate, use_default=use_default, config=config)

        try:
            val = self.handle_references(val, scope=scope, impersonate=impersonate)
        except ex.excError as exc:
            if o.startswith("pre_") or o.startswith("post_") or o.startswith("blocking_"):
                pass
            else:
                raise

        if t == 'string':
            pass
        elif t == 'boolean':
            val = convert_bool(val)
        elif t == 'integer':
            try:
                val = int(val)
            except:
                val = convert_size(val)
        else:
            raise Exception("unknown keyword type: %s" % t)

        return val

    def conf_get_val_unscoped(self, s, o, use_default=True, config=None):
        if config is None:
            config = self.config
        if config.has_option(s, o):
            return config.get(s, o)
        raise ex.OptNotFound("unscoped keyword %s.%s not found" % (s, o))

    def conf_has_option_scoped(self, s, o, impersonate=None, config=None):
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

        if o+"@"+nodename in options:
            return o+"@"+nodename
        elif o+"@nodes" in options and \
             nodename in self.nodes:
            return s, o+"@nodes"
        elif o+"@drpnodes" in options and \
             nodename in self.drpnodes:
            return o+"@drpnodes"
        elif o+"@encapnodes" in options and \
             nodename in self.encapnodes:
            return o+"@encapnodes"
        elif o+"@flex_primary" in options and \
             nodename == self.flex_primary:
            return o+"@flex_primary"
        elif o+"@drp_flex_primary" in options and \
             nodename == self.drp_flex_primary:
            return o+"@drp_flex_primary"
        elif o in options:
            return o

    def conf_get_val_scoped(self, s, o, impersonate=None, use_default=True, config=None):
        if config is None:
            config = self.config
        if impersonate is None:
            nodename = rcEnv.nodename
        else:
            nodename = impersonate

        option = self.conf_has_option_scoped(s, o, impersonate=impersonate, config=config)
        if option is None and not use_default:
            raise ex.OptNotFound("scoped keyword %s.%s not found" % (s, o))

        if option is None and use_default:
            if s != "DEFAULT":
                # fallback to default
                return self.conf_get_val_scoped("DEFAULT", o, impersonate=impersonate, config=config)
            else:
                raise ex.OptNotFound("scoped keyword %s.%s not found" % (s, o))

        try:
            val = config.get(s, option)
        except Exception as e:
            raise ex.excError("param %s.%s: %s"%(s, o, str(e)))

        return val

    def conf_get_string(self, s, o, use_default=True, config=None):
        return self.conf_get(s, o, 'string', scope=False, use_default=use_default, config=config)

    def conf_get_string_scope(self, s, o, impersonate=None, use_default=True, config=None):
        return self.conf_get(s, o, 'string', scope=True, impersonate=impersonate, use_default=use_default, config=config)

    def conf_get_boolean(self, s, o, use_default=True, config=None):
        return self.conf_get(s, o, 'boolean', scope=False, use_default=use_default, config=config)

    def conf_get_boolean_scope(self, s, o, impersonate=None, use_default=True, config=None):
        return self.conf_get(s, o, 'boolean', scope=True, impersonate=impersonate, use_default=use_default, config=config)

    def conf_get_int(self, s, o, use_default=True, config=None):
        return self.conf_get(s, o, 'integer', scope=False, use_default=use_default, config=config)

    def conf_get_int_scope(self, s, o, impersonate=None, use_default=True, config=None):
        return self.conf_get(s, o, 'integer', scope=True, impersonate=impersonate, use_default=use_default, config=config)

    def get_pg_settings(self, s):
        d = {}
        options = (
            "cpus",
            "cpu_shares",
            "cpu_quota",
            "mems",
            "mem_oom_control",
            "mem_limit",
            "mem_swappiness",
            "vmem_limit",
            "blkio_weight",
        )

        # don't use DEFAULT for resources and subsets
        for option in options:
            _option = self.conf_has_option_scoped(s, option)
            if _option:
                d[option] = self.conf_get_string_scope(s, option)

        return d

    @lazy
    def pg_settings(self):
        return self.get_pg_settings("DEFAULT")

