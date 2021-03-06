"""
Defines the resource class, which is the parent class of every
resource driver.
"""
from __future__ import print_function

import os
import logging
import sys
import time
import json

import lock
import rcExceptions as ex
import rcStatus
from rcUtilities import lazy, clear_cache, call, vcall, lcall, set_lazy, \
                        action_triggers, mimport, unset_lazy, factory
from rcGlobalEnv import rcEnv
import rcColor

ALLOW_ACTION_WITH_NOACTION = [
    "presync",
    "set_provisioned",
    "set_unprovisioned",
]

LOCKER_TYPES = [
    "disk.scsireserv",
    "disk.lock",
]

class Resource(object):
    """
    Resource drivers parent class
    """
    default_optional = False
    refresh_provisioned_on_provision = False
    refresh_provisioned_on_unprovision = False

    def __init__(self,
                 rid=None,
                 type=None,
                 subset=None,
                 optional=False,
                 disabled=False,
                 monitor=False,
                 restart=0,
                 tags=None,
                 standby=False,
                 skip_provision=False,
                 skip_unprovision=False,
                 shared=False,
                 promote_rw=False,
                 encap=False,
                 **ignored):
        if tags is None:
            tags = set()
        self.svc = None
        self.rset = None
        self.rid = rid
        self.tags = tags
        self.type = type
        self.subset = subset
        self.optional = optional if optional is not None else self.default_optional
        self.standby = standby
        self.disabled = disabled
        self.skip = False
        self.monitor = monitor
        self.nb_restart = restart
        self.rstatus = None
        self.skip_provision = skip_provision
        self.skip_unprovision = skip_unprovision
        self.shared = shared
        self.need_promote_rw = promote_rw
        self.encap = encap or "encap" in self.tags
        self.sort_key = rid
        self.info_in_status = []
        self.lockfd = None
        self.always_pg = False
        try:
            self.label = type
        except AttributeError:
            # label is a lazy prop of the child class
            pass
        self.status_logs = []
        self.can_rollback = False
        self.rollback_even_if_standby = False
        self.skip_triggers = set()

    @lazy
    def log(self):
        """
        Lazy init for the resource logger.
        """
        extra = {
            "path": self.svc.path,
            "node": rcEnv.nodename,
            "sid": rcEnv.session_uuid,
            "cron": self.svc.options.cron,
            "rid": self.rid,
            "subset": self.subset,
        }
        return logging.LoggerAdapter(logging.getLogger(self.log_label()), extra)

    @lazy
    def var_d(self):
        var_d = os.path.join(self.svc.var_d, self.rid)
        if not os.path.exists(var_d):
            os.makedirs(var_d)
        return var_d

    def set_logger(self, log):
        """
        Set the <log> logger as the resource logger, in place of the default
        lazy-initialized one.
        """
        set_lazy(self, "log", log)

    def fmt_info(self, keys=None):
        """
        Returns the resource generic keys sent to the collector upon push
        resinfo.
        """
        if keys is None:
            return []
        for idx, key in enumerate(keys):
            count = len(key)
            if count and key[-1] is None:
                key[-1] = ""
            if count == 2:
                keys[idx] = [
                    self.svc.path,
                    self.svc.node.nodename,
                    self.svc.topology,
                    self.rid
                ] + key
            elif count == 3:
                keys[idx] = [
                    self.svc.path,
                    self.svc.node.nodename,
                    self.svc.topology
                ] + key
        return keys

    def log_label(self):
        """
        Return the resource label used in logs entries.
        """
        if hasattr(self, "svc"):
            label = self.svc.loggerpath + '.'
        else:
            label = rcEnv.nodename + "."

        if self.rid is None:
            label += self.type
            return label

        if self.subset is None:
            label += self.rid
            return label

        elements = self.rid.split('#')
        if len(elements) != 2:
            label += self.rid
            return label

        ridx = elements[1]
        label += "%s:%s#%s" % (self.type.split(".")[0], self.subset, ridx)
        return label

    def __str__(self):
        output = "object=%s rid=%s type=%s" % (
            self.__class__.__name__,
            self.rid,
            self.type
        )
        if self.optional:
            output += " opt=" + str(self.optional)
        if self.disabled:
            output += " disa=" + str(self.disabled)
        return output

    def __lt__(self, other):
        """
        Resources needing to be started or stopped in a specific order
        should redefine that.
        """
        if self.type in LOCKER_TYPES and other.type not in LOCKER_TYPES:
            if other.type == "disk.disk" and self.rid == other.rid + "pr":
                ret = False
            else:
                ret = True
        elif self.type not in LOCKER_TYPES and other.type in LOCKER_TYPES:
            if self.type == "disk.disk" and self.rid + "pr" == other.rid:
                ret = True
            else:
                ret = False
        elif self.type == "sync.zfssnap" and other.type == "sync.zfs":
            ret = True
        elif self.type == "sync.zfs" and other.type == "sync.zfssnap":
            ret = False
        else:
            ret = self.sort_key < other.sort_key
        return ret

    def save_exc(self):
        """
        A helper method to save stacks in the service log.
        """
        self.log.error("", exc_info=True)

    def setup_environ(self):
        """
        Setup environement variables for use by triggers and startup
        scripts. This method needs defining in each class with their
        class variable.
        Env vars names should, by convention, be prefixed by OPENSVC_
        """
        pass

    def is_optional(self):
        """
        Accessor for the optional resource property.
        """
        return self.optional

    def is_disabled(self):
        """
        Accessor for the disabled resource property.
        """
        if self.svc.disabled:
            return True
        return self.disabled

    def set_optional(self):
        """
        Set the optional resource property to True.
        """
        self.optional = True

    def unset_optional(self):
        """
        Set the optional resource property to False.
        """
        self.optional = False

    def disable(self):
        """
        Set the disabled resource property to True.
        """
        self.disabled = True

    def enable(self):
        """
        Set the disabled resource property to False.
        """
        self.disabled = False

    def clear_cache(self, sig):
        """
        Wraps the rcUtilities clear_cache function, setting the resource
        as object keyword argument.
        """
        clear_cache(sig, o=self)

    def action_triggers(self, trigger, action, **kwargs):
        """
        Executes a resource trigger. Guess if the shell mode is needed from
        the trigger syntax.
        """
        action_triggers(self, trigger, action, **kwargs)

    def handle_confirm(self, action):
        """
        Tasks can require a run confirmation. We want the confirmation checked
        before executing triggers.
        """
        if not hasattr(self, "confirm"):
            return
        if action != "run":
            return
        getattr(self, "confirm")()

    def action_main(self, action):
        """
        Shortcut the resource action if in dry-run mode.
        """
        if self.svc.options.dry_run:
            if self.rset.parallel:
                header = "+ "
            else:
                header = ""
            self.log.info("%s%s %s", header, action, self.label)
            return
        getattr(self, action)()

    def do_action(self, action):
        """
        Call the resource action method if implemented.

        If the action is a stopping action and the resource is flagged
        standby on this node, skip.

        Call the defined pre and post triggers.

        """
        if not hasattr(self, action):
            self.log.debug("%s action is not implemented", action)

        self.log.debug('do action %s', action)

        if action == "stop" and self.standby and not self.svc.options.force:
            standby_action = action+'standby'
            if hasattr(self, standby_action):
                self.action_main(standby_action)
                return
            else:
                self.log.info("skip '%s' on standby resource (--force to override)", action)
                return

        self.check_requires(action)
        self.handle_confirm(action)
        self.setup_environ()
        self.action_triggers("pre", action)
        self.action_triggers("blocking_pre", action, blocking=True)
        self.action_main(action)
        self.action_triggers("post", action)
        self.action_triggers("blocking_post", action, blocking=True)
        if self.need_refresh_status(action):
            self.status(refresh=True)
        return

    def need_refresh_status(self, action):
        """
        Return True for action known to be causing a resource status change.
        """
        actions = (
            "boot",
            "shutdown",
            "start",
            "startstandby",
            "stop",
            "rollback",
            "unprovision",
            "provision",
            "install",
            "create",
            "switch",
            "migrate"
        )
        if self.svc.options.dry_run:
            return False
        if "sync" in action and self.type.startswith("sync"):
            return True
        if action in actions:
            return True
        return False

    def skip_resource_action(self, action):
        """
        Return True if the action should be skipped.
        """
        actions = (
            "shutdown",
            "start",
            "startstandby",
            "stop",
            "provision",
            "run",
            "set_unprovisioned",
            "set_provisioned",
            "unprovision",
        )
        if not self.skip:
            return False
        if action.startswith("sync"):
            return True
        if action.startswith("_pg_"):
            return True
        if action in actions:
            return True
        return False

    def action(self, action):
        """
        Try to call the resource do_action() if:
        * action is not None
        * the resource is not skipped by resource selectors

        If the resource is disabled, the return status depends on the optional
        property:
        * if optional, return True
        * else return do_action() return value
        """
        if self.rid != "app" and not self.svc.encap and self.encap:
            self.log.debug('skip encap resource action: action=%s res=%s', action, self.rid)
            return

        if 'noaction' in self.tags and \
           not hasattr(self, "delayed_noaction") and \
           action not in ALLOW_ACTION_WITH_NOACTION:
            self.log.debug('skip resource action %s (noaction tag)', action)
            return

        self.log.debug('action: %s', action)

        if action is None:
            self.log.debug('action: action cannot be None')
            return True
        if self.skip_resource_action(action):
            self.log.debug('action: skip action on filtered-out resource')
            return True
        if self.is_disabled():
            self.log.debug('action: skip action on disabled resource')
            return True
        if not hasattr(self, action):
            self.log.debug('action: not applicable (not implemented)')
            return True

        try:
            self.progress()
            self.do_action(action)
        except ex.excUndefined as exc:
            print(exc)
            return False
        except ex.excContinueAction as exc:
            if self.svc.options.cron:
                # no need to flood the logs for scheduled tasks
                self.log.debug(str(exc))
            else:
                self.log.info(str(exc))
        except ex.excError as exc:
            if self.optional:
                if len(str(exc)) > 0:
                    self.log.error(str(exc))
                self.log.info("ignore %s error on optional resource", action)
            else:
                raise

    def status_stdby(self, status):
        """
        This method modifies the passed status according to the standby
        property.
        """
        if not self.standby:
            return status
        if status == rcStatus.UP:
            return rcStatus.STDBY_UP
        elif status == rcStatus.DOWN:
            return rcStatus.STDBY_DOWN
        return status

    def try_status(self, verbose=False):
        """
        Catch status methods errors and push them to the resource log buffer
        so they will be display in print status.
        """
        if hasattr(self, "is_up_clear_cache"):
            getattr(self, "is_up_clear_cache")()
        try:
            return self._status(verbose=verbose)
        except Exception as exc:
            self.status_log(str(exc), "error")
            return rcStatus.UNDEF

    def _status(self, verbose=False):
        """
        The resource status evaluation method.
        To be implemented by drivers.
        """
        if verbose:
            self.log.debug("default resource status: undef")
        return rcStatus.UNDEF

    def force_status(self, status):
        """
        Force a resource status, bypassing the evaluation method.
        """
        self.rstatus = status
        self.status_logs = [("info", "forced")]
        self.write_status()

    def status(self, **kwargs):
        """
        Resource status evaluation method wrapper.
        Handles caching, nostatus tag and disabled flag.
        """
        verbose = kwargs.get("verbose", False)
        refresh = kwargs.get("refresh", False)
        ignore_nostatus = kwargs.get("ignore_nostatus", False)

        if self.is_disabled():
            return rcStatus.NA

        if not ignore_nostatus and "nostatus" in self.tags:
            self.status_log("nostatus tag", "info")
            return rcStatus.NA

        if self.rstatus is not None and not refresh:
            return self.rstatus

        last_status = self.load_status_last(refresh)

        if refresh:
            self.purge_status_last()
        else:
            self.rstatus = last_status

        # now the rstatus can no longer be None
        if self.rstatus == rcStatus.UNDEF or refresh:
            self.status_logs = []
            self.rstatus = self.try_status(verbose)
            self.rstatus = self.status_stdby(self.rstatus)
            self.log.debug("refresh status: %s => %s",
                           rcStatus.Status(last_status),
                           rcStatus.Status(self.rstatus))
            self.write_status()

        if self.rstatus in (rcStatus.UP, rcStatus.STDBY_UP) and \
           self.is_provisioned_flag() is False:
            self.write_is_provisioned_flag(True)

        return self.rstatus

    def write_status(self):
        """
        Helper method to janitor resource status cache and history in files.
        """
        self.write_status_last()
        self.write_status_history()

    @lazy
    def fpath_status_last(self):
        """
        Return the file path for the resource status cache.
        """
        return os.path.join(self.var_d, "status.last")

    @lazy
    def fpath_status_history(self):
        """
        Return the file path for the resource status history.
        """
        return os.path.join(self.var_d, "status.history")

    def purge_status_last(self):
        """
        Purge the on-disk resource status cache.
        """
        try:
            os.unlink(self.fpath_status_last)
        except:
            pass

    def purge_var_d(self, keep_provisioned=True):
        import glob
        import shutil
        paths = glob.glob(os.path.join(self.var_d, "*"))
        for path in paths:
            if keep_provisioned and path == os.path.join(self.var_d, "provisioned"):
                # Keep the provisioned flag to remember that the
                # resource was unprovisioned, even if the driver
                # says it is always provisioned.
                # This is necessary because the orchestrated
                # unprovision would retry the CRM action if a
                # resource reports it is still provisioned after
                # the first unprovision.
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
            except OSError as exc:
                # errno 39: not empty (racing with a writer)
                pass

    def has_status_last(self):
        return os.path.exists(self.fpath_status_last)

    def load_status_last(self, refresh=False):
        """
        Fetch the resource status from the on-disk cache.
        """
        try:
            with open(self.fpath_status_last, 'r') as ofile:
                data = json.load(ofile)
        except ValueError:
            return rcStatus.UNDEF
        except (OSError, IOError) as exc:
            if exc.errno != 2:
                # not EEXISTS
                self.log.debug(exc)
            return rcStatus.UNDEF

        try:
            status = rcStatus.Status(data["status"])
        except (IndexError, AttributeError, ValueError) as exc:
            self.log.debug(exc)
            return rcStatus.UNDEF

        if not refresh and hasattr(self, "set_label"):
            if hasattr(self, "_lazy_label"):
                attr = "_lazy_label"
            else:
                attr = "label"
            try:
                setattr(self, attr, data["label"])
            except (IndexError, AttributeError, ValueError) as exc:
                pass

        self.status_logs = data.get("log", [])

        return status

    def write_status_last(self):
        """
        Write the in-memory resource status to the on-disk cache.
        """
        data = {
            "status": str(rcStatus.Status(self.rstatus)),
            "label": self.label,
            "log": self.status_logs,
        }
        dpath = os.path.dirname(self.fpath_status_last)
        if not os.path.exists(dpath):
            os.makedirs(dpath, 0o0755)
        with open(self.fpath_status_last, 'w') as ofile:
            json.dump(data, ofile)
            ofile.flush()

    def write_status_history(self):
        """
        Log a change to the resource status history file.
        """
        fpath = self.fpath_status_history
        try:
            with open(fpath, 'r') as ofile:
                lines = ofile.readlines()
                last = lines[-1].split(" | ")[-1].strip("\n")
        except:
            last = None
        current = rcStatus.Status(self.rstatus)
        if current == last:
            return
        log = logging.getLogger("status_history")
        logformatter = logging.Formatter("%(asctime)s | %(message)s")
        logfilehandler = logging.handlers.RotatingFileHandler(
            fpath,
            maxBytes=512000,
            backupCount=1,
        )
        logfilehandler.setFormatter(logformatter)
        log.addHandler(logfilehandler)
        log.error(current)
        logfilehandler.close()
        log.removeHandler(logfilehandler)

    def status_log(self, text, level="warn"):
        """
        Add a message to the resource status log buffer, for
        display in the print status output.
        """
        if len(text) == 0:
            return
        if (level, text) in self.status_logs:
            return
        for line in text.splitlines():
            self.status_logs.append((level, line))

    def status_logs_get(self, levels=None):
        """
        Return filtered messages from the the resource status log buffer.
        """
        if levels is None:
            levels = ["info", "warn", "error"]
        return [entry[1] for entry in self.status_logs if \
                entry[0] in levels and entry[1] != ""]

    def status_logs_count(self, levels=None):
        """
        Return the number of status log buffer entries matching the
        specified levels.
        """
        if levels is None:
            levels = ["info", "warn", "error"]
        return len(self.status_logs_get(levels=levels))

    def status_logs_strlist(self):
        return ["%s: %s" % (lvl, msg) for (lvl, msg) in self.status_logs]

    def status_logs_str(self, color=False):
        """
        Returns the formatted resource status log buffer entries.
        """
        status_str = ""
        for level, text in self.status_logs:
            if len(text) == 0:
                continue
            entry = level + ": " + text + "\n"
            if color:
                if level == "warn":
                    color = rcColor.color.BROWN
                elif level == "error":
                    color = rcColor.color.RED
                else:
                    color = rcColor.color.LIGHTBLUE
                status_str += rcColor.colorize(entry, color)
            else:
                status_str += entry
        return status_str

    def call(self, *args, **kwargs):
        """
        Wrap rcUtilities call, setting the resource logger
        """
        kwargs["log"] = self.log
        return call(*args, **kwargs)

    def vcall(self, *args, **kwargs):
        """
        Wrap vcall, setting the resource logger
        """
        kwargs["log"] = self.log
        return vcall(*args, **kwargs)

    def lcall(self, *args, **kwargs):
        """
        Wrap lcall, setting the resource logger
        """
        kwargs["logger"] = self.log
        return lcall(*args, **kwargs)

    @staticmethod
    def wait_for_fn(func, tmo, delay, errmsg="waited too long for startup"):
        """
        A helper function to execute a test function until it returns True
        or the number of retries is exhausted.
        """
        for tick in range(tmo//delay):
            if func():
                return
            time.sleep(delay)
        raise ex.excError(errmsg)

    def base_devs(self):
        """
        List devices the resource holds at the base of the dev tree.
        """
        devps = self.sub_devs() | self.exposed_devs()
        devs = self.svc.node.devtree.get_devs_by_devpaths(devps)
        base_devs = set()
        for dev in devs:
            top_devs = dev.get_top_devs()
            for dev in top_devs:
                base_devs.add(os.path.realpath(dev.devpath[0]))
        return base_devs

    def sub_devs(self):
        """
        List devices the resource holds.
        """
        return set()

    def exposed_devs(self):
        """
        List devices the resource exposes.
        """
        return set()

    def base_disks(self):
        """
        List disks the resource holds at the base of the dev tree. Some
        resource have none, and can leave this function as is.
        """
        devs = self.base_devs()
        try:
            u = __import__('rcUtilities'+rcEnv.sysname)
            disks = u.devs_to_disks(self, devs)
        except:
            disks = devs
        return disks

    def sub_disks(self):
        """
        List disks the resource holds. Some resource have none, and can leave
        this function as is.
        """
        devs = self.sub_devs()
        try:
            u = __import__('rcUtilities'+rcEnv.sysname)
            disks = u.devs_to_disks(self, devs)
        except:
            disks = devs
        return disks

    def exposed_disks(self):
        """
        List disks the resource exposes. Some resource have none, and can leave
        this function as is.
        """
        devs = self.exposed_devs()
        try:
            u = __import__('rcUtilities'+rcEnv.sysname)
            disks = u.devs_to_disks(self, devs)
        except:
            disks = devs
        return disks

    def presync(self):
        """
        A method called before a sync action is executed.
        """
        pass

    def postsync(self):
        """
        A method called after a sync action is executed.
        """
        pass

    @staticmethod
    def default_files_to_sync():
        """
        If files_to_sync() is not superceded, return an empty list as the
        default resource files to sync.
        """
        return []

    def files_to_sync(self):
        """
        Returns a list of files to contribute to sync#i0
        """
        return self.default_files_to_sync()

    def rollback(self):
        """
        Executes a resource stop if the resource start has marked the resource
        as rollbackable.
        """
        if self.can_rollback and not self.standby:
            self.stop()

    def stop(self):
        """
        The resource stop action entrypoint.
        """
        pass

    def startstandby(self):
        """
        Promote the action to start if the resource is flagged standby
        """
        if self.standby:
            self.start()

    def start(self):
        """
        The resource start action entrypoint.
        """
        pass

    def boot(self):
        """
        Clean up actions to do on node boot before the daemon starts.
        """
        pass

    def shutdown(self):
        """
        Always promote to the stop action
        """
        self.stop()

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
        if not self.svc.create_pg and not self.always_pg:
            return
        if self.svc.pg is None:
            return
        if action == "freeze":
            self.svc.pg.freeze(self)
        elif action == "thaw":
            self.svc.pg.thaw(self)
        elif action == "kill":
            self.svc.pg.kill(self)

    def pg_frozen(self):
        """
        Return True if the resource has its process group frozen
        """
        if not self.svc.create_pg and not self.always_pg:
            return False
        if self.svc.pg is None:
            return False
        return self.svc.pg.frozen(self)

    def create_pg(self):
        """
        Create a process group if this service asks for it and if possible.
        """
        if not self.svc.create_pg and not self.always_pg:
            return
        if self.svc.pg is None:
            return
        self.svc.pg.create_pg(self)

    def check_requires(self, action, cluster_data=None):
        """
        Iterate the resource 'requires' definition, and validate each
        requirement.
        """
        param = action + "_requires"
        if not hasattr(self, param):
            return
        requires = getattr(self, param)
        if len(requires) == 0:
            return
        for element in requires:
            self._check_requires(element, cluster_data=cluster_data)

    def _check_requires(self, element, cluster_data=None):
        """
        Validate a requires element, raising excError if the requirement is
        not met.
        """
        if element is None:
            return
        if element == "impossible":
            raise ex.excContinueAction("skip impossible requirement")
        if element.count("(") == 1:
            rid, states = element.rstrip(")").split("(")
            states = states.split(",")
        else:
            rid = element
            states = ["up", "stdby up"]
        if rid not in self.svc.resources_by_id:
            self.log.warning("ignore requires on %s: resource not found", rid)
            return
        if cluster_data:
            try:
                current_state = cluster_data[rcEnv.nodename]["services"]["status"][self.svc.path]["resources"][rid]["status"]
            except KeyError:
                current_state = "undef"
        else:
            resource = self.svc.resources_by_id[rid]
            current_state = rcStatus.Status(resource.status())
        if current_state not in states:
            msg = "requires on resource %s in state %s, current state %s" % \
                  (rid, " or ".join(states), current_state)
            if self.svc.options.cron:
                raise ex.excContinueAction(msg)
            else:
                raise ex.excError(msg)

    def dns_update(self):
        """
        Placeholder for resource specific implementation of the dns update.
        """
        pass

    def oget(self, o, **kwargs):
        return self.svc.oget(self.rid, o, **kwargs)

    def conf_get(self, o, **kwargs):
        """
        Relay for the Svc::conf_get() method, setting the resource rid as the
        config section.
        """
        return self.svc.conf_get(self.rid, o, **kwargs)

    def _status_info(self):
        """
        Placeholder for driver implementation
        """
        return {}

    def status_info(self):
        data = self._status_info()
        if not self.shared:
            return data
        sopts = self.schedule_options()
        if not sopts:
            return data
        data["sched"] = {}
        for saction, sopt in sopts.items():
            data["sched"][saction] = self.schedule_info(sopt)
        return data

    def info(self):
        data = [
          ["driver", self.type],
          ["standby", str(self.standby).lower()],
          ["optional", str(self.optional).lower()],
          ["disabled", str(self.disabled).lower()],
          ["monitor", str(self.monitor).lower()],
          ["shared", str(self.shared).lower()],
          ["encap", str(self.encap).lower()],
          ["restart", str(self.nb_restart)],
        ]
        if self.subset:
            data.append(["subset", self.subset])
        if len(self.tags) > 0:
            data.append(["tags", " ".join(self.tags)])
        if hasattr(self, "_info"):
            try:
                data += getattr(self, "_info")()
            except AttributeError:
                pass
            except Exception as e:
                print(e, file=sys.stderr)
        return self.fmt_info(data)

    ##########################################################################
    #
    # provisioning
    #
    ##########################################################################
    @lazy
    def provisioned_flag(self):
        """
        The full path to the provisioned state cache file.
        """
        return os.path.join(self.var_d, "provisioned")

    def provisioned_flag_mtime(self):
        """
        Return the provisioned state cache file modification time.
        """
        try:
            return os.path.getmtime(self.provisioned_flag)
        except Exception:
            return

    def provisioned_data(self):
        """
        Return the resource provisioned state from the on-disk cache and its
        state change time as a dictionnary.
        """
        try:
            isprov = self.is_provisioned()
        except Exception as exc:
            self.status_log("provisioned: %s"%str(exc), "error")
            isprov = False
        data = {}
        if isprov is not None:
            data["state"] = isprov
        mtime = self.provisioned_flag_mtime()
        if mtime is not None:
            data["mtime"] = mtime
        return data

    def is_provisioned_flag(self):
        """
        Return the boolean provisioned state cached on disk.
        Return None if the file does not exist or is corrupted.
        """
        try:
            with open(self.provisioned_flag, 'r') as filep:
                return json.load(filep)
        except Exception:
            return

    def write_is_provisioned_flag(self, value, mtime=None):
        """
        Write a resource-private file containing the boolean provisioned
        state and state change time.
        """
        if value is None:
            return
        try:
            with open(self.provisioned_flag, 'w') as filep:
                try:
                    json.dump(value, filep)
                    filep.flush()
                except ValueError:
                    return
        except Exception:
            # can happen in instance delete codepath
            return
        if mtime:
            os.utime(self.provisioned_flag, (mtime, mtime))

    def remove_is_provisioned_flag(self):
        """
        Remove the provisioned state cache file. Used in the Svc::delete_resource()
        code path.
        """
        if not os.path.exists(self.provisioned_flag):
            return
        os.unlink(self.provisioned_flag)

    def set_unprovisioned(self):
        """
        Exposed resource action to force the provisioned state to False in the cache file.
        """
        self.log.info("set unprovisioned")
        self.write_is_provisioned_flag(False)

    def set_provisioned(self):
        """
        Exposed resource action to force the provisioned state to True in the cache file.
        """
        self.log.info("set provisioned")
        self.write_is_provisioned_flag(True)

    @lazy
    def prov(self):
        """
        Find the provisioning module, import it and instanciate a Prov object.
        """
        driver_translations = {
            "disk.veritas": "disk.vxdg",
        }
        if self.type in driver_translations:
            translated_type = driver_translations[self.type]
        else:
            translated_type = self.type
        try:
            driver_group, driver = translated_type.split(".", 1)
        except ValueError:
            driver_group = translated_type
            driver = ""
        try:
            mod = mimport("prov", driver_group, driver, fallback=True)
        except ImportError as exc:
            mod = __import__("provisioning")
        if not hasattr(mod, "Prov"):
            raise ex.excError("missing Prov class in module %s" % str(mod))
        return getattr(mod, "Prov")(self)

    def provision_shared_non_leader(self):
        self.log.info("non leader shared resource provisioning")
        self.write_is_provisioned_flag(True, mtime=1)

        # do not execute post_provision triggers
        self.skip_triggers.add("post_provision")
        self.skip_triggers.add("blocking_post_provision")

        if self.skip_provision:
            self.log.info("provision skipped (configuration directive)")
            return
        if self.prov is None:
            return
        if hasattr(self.prov, "provisioner_shared_non_leader"):
            self.prov.provisioner_shared_non_leader()

    def provision(self):
        if self.shared and not self.svc.options.leader:
            self.provision_shared_non_leader()
            return
        self._provision()
        try:
            self.prov.start()
        except Exception as exc:
            if self.skip_provision:
                # best effort
                pass
            else:
                raise

    def _provision(self):
        """
        Unimplemented is_provisioned() trusts provisioner() to do the right
        thing.
        """
        if self.skip_provision:
            self.log.info("provision skipped (configuration directive)")
            self.write_is_provisioned_flag(True)
            return
        if self.prov is None:
            return
        if self.is_provisioned(refresh=self.refresh_provisioned_on_provision) is True:
            self.log.info("%s already provisioned", self.label)
        else:
            self.prov.provisioner()
        self.write_is_provisioned_flag(True)

    def unprovision(self):
        try:
            self.prov.stop()
        except Exception:
            if self.skip_unprovision:
                # best effort
                pass
            else:
                raise
        if self.shared and not self.svc.options.leader:
            self.unprovision_shared_non_leader()
            return
        self._unprovision()

    def unprovision_shared_non_leader(self):
        self.log.info("non leader shared resource unprovisioning")

        # do not execute post_unprovision triggers
        self.skip_triggers.add("post_unprovision")
        self.skip_triggers.add("blocking_post_unprovision")

        if self.skip_unprovision:
            self.log.info("unprovision skipped (configuration directive)")
            return
        if self.prov is None:
            return
        if hasattr(self.prov, "unprovisioner_shared_non_leader"):
            self.prov.unprovisioner_shared_non_leader()
        self.write_is_provisioned_flag(False)

    def _unprovision(self):
        """
        Unimplemented is_provisioned() trusts unprovisioner() to do the right
        thing.
        """
        if self.skip_provision or self.skip_unprovision:
            self.log.info("unprovision skipped (configuration directive)")
            return
        if self.prov is None:
            return
        if self.is_provisioned(refresh=self.refresh_provisioned_on_unprovision) is False:
            self.log.info("%s already unprovisioned", self.label)
        else:
            self.prov.unprovisioner()
        self.write_is_provisioned_flag(False)

    def is_provisioned(self, refresh=False):
        if self.prov is None:
            return True
        if not refresh:
            flag = self.is_provisioned_flag()
            if flag is not None:
                return flag
        if not hasattr(self.prov, "is_provisioned"):
            return
        value = self.prov.is_provisioned()
        if not self.shared or self.svc.options.leader or \
           (self.shared and not refresh and value):
            self.write_is_provisioned_flag(value)
        return value

    def promote_rw(self):
        if not self.need_promote_rw:
            return
        mod = __import__("rcUtilities"+rcEnv.sysname)
        if not hasattr(mod, "promote_dev_rw"):
            self.log.warning("promote_rw is not supported on this operating system")
            return
        for dev in self.base_devs():
            getattr(mod, "promote_dev_rw")(dev, log=self.log)

    def progress(self):
        lock.progress(self.svc.lockfd, {"rid": self.rid})

    def unset_lazy(self, prop):
        """
        Expose the self.unset_lazy(...) utility function as a method,
        so Node() users don't have to import it from rcUtilities.
        """
        unset_lazy(self, prop)

    def reslock(self, action=None, timeout=30, delay=1, suffix=None):
        """
        Acquire the resource action lock.
        """
        if self.lockfd is not None:
            # already acquired
            return

        lockfile = os.path.join(self.var_d, "lock")
        if suffix is not None:
            lockfile = ".".join((lockfile, suffix))

        details = "(timeout %d, delay %d, action %s, lockfile %s)" % \
                  (timeout, delay, action, lockfile)
        self.log.debug("acquire resource lock %s", details)

        try:
            lockfd = lock.lock(
                timeout=timeout,
                delay=delay,
                lockfile=lockfile,
                intent=action
            )
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

    def resunlock(self):
        """
        Release the service action lock.
        """
        lock.unlock(self.lockfd)
        self.lockfd = None

    def section_kwargs(self):
        rtype = self.type.split(".")[-1]
        return self.svc.section_kwargs(self.rid, rtype)

    def replace_volname(self, *args, **kwargs):
        path, vol = self.svc.replace_volname(*args, **kwargs)
        if not vol:
            return path, vol
        volrid = self.svc.get_volume_rid(vol.name)
        if volrid:
            self.svc.register_dependency("stop", volrid, self.rid)
            self.svc.register_dependency("start", self.rid, volrid)
        return path, vol

    def kind_environment_env(self, kind, mappings):
        env = {}
        if mappings is None:
            return env
        for mapping in mappings:
            try:
                var, val = mapping.split("=", 1)
            except Exception as exc:
                self.log.info("ignored %s environment mapping %s: %s", kind, mapping, exc)
                continue
            try:
                name, key = val.split("/", 1)
            except Exception as exc:
                self.log.info("ignored %s environment mapping %s: %s", kind, mapping, exc)
                continue
            var = var.upper()
            obj = factory(kind)(name, namespace=self.svc.namespace, volatile=True, node=self.svc.node)
            if not obj.exists():
                self.log.info("ignored %s environment mapping %s: config %s does not exist", kind, mapping, name)
                continue
            if key not in obj.data_keys():
                self.log.info("ignored %s environment mapping %s: key %s does not exist", kind, mapping, key)
                continue
            val = obj.decode_key(key)
            env[var] = val
        return env

    def schedule_info(self, sopt):
        try:
            last = float(self.svc.sched.get_last(sopt.fname).strftime("%s.%f"))
        except Exception as exc:
            return {}
        data = {
            "last": last,
        }
        return data

    def schedule_options(self):
        """
        Placeholder for driver implementation.
        Must return a dict of scheduler options indexed by scheduler action.
        Used by Svc::configure_scheduler() and Resource::status_info().
        """
        return {}
