"""
The module defining the App resource class.
"""
from subprocess import Popen
from datetime import datetime
import os
import time
import stat
import shlex
import six

from rcUtilities import which, lazy, is_string, lcall
from converters import convert_boolean
from rcGlobalEnv import rcEnv
from resources import Resource
import rcStatus
import rcExceptions as ex
import lock

def run_as_popen_kwargs(fpath, limits={}, user=None, group=None, cwd=None):
    """
    Setup the Popen keyword args to execute <fpath> with the
    privileges demoted to those of the owner of <fpath>.
    """
    if rcEnv.sysname == "Windows":
        return {}

    import pwd
    if cwd is None:
        cwd = rcEnv.paths.pathtmp

    pwd_user = None
    user_name = "unknown"

    if fpath:
        fpath = os.path.realpath(fpath)

        try:
            fstat = os.stat(fpath)
        except Exception as exc:
            raise ex.excError(str(exc))
        user_uid = fstat[stat.ST_UID]
        user_gid = fstat[stat.ST_GID]
        try:
            pwd_user = pwd.getpwuid(user_uid)
            user_name = pwd_user.pw_name
        except KeyError:
            pwd_user = None
            user_name = "unknown"
    elif user is None:
        user = "root"

    if user_name in ("root", "unknown") and user is not None:
        try:
            user_uid = int(user)
        except ValueError:
            try:
                pwd_user = pwd.getpwnam(user)
            except KeyError:
                raise ex.excError("user %s does not exist" % user)
            user_uid = pwd_user.pw_uid
            user_gid = pwd_user.pw_gid
            user_name = user
        else:
            try:
                pwd_user = pwd.getpwuid(user_uid)
            except KeyError:
                raise ex.excError("user %d does not exist" % user_uid)
            user_name = pwd_user.pw_name
            user_gid = pwd_user.pw_gid

        if group is not None:
            import grp
            try:
                user_gid = int(group)
            except ValueError:
                try:
                    grp_group = grp.getgrnam(group)
                except KeyError:
                    raise ex.excError("group %s does not exist" % group)
                user_gid = grp_group.gr_gid
            else:
                try:
                    grp_group = grp.getgrgid(user_gid)
                except KeyError:
                    raise ex.excError("group %d does not exist" % user_gid)

    try:
        pw_record = pwd.getpwnam(user_name)
        user_home_dir = pw_record.pw_dir
    except KeyError:
        user_home_dir = rcEnv.paths.pathtmp

    env = os.environ.copy()
    env['HOME'] = user_home_dir
    env['LOGNAME'] = user_name
    env['PWD'] = cwd
    env['USER'] = user_name
    return {'preexec_fn': preexec(user_uid, user_gid, limits), 'cwd': cwd, 'env': env}

def preexec(user_uid, user_gid, limits):
    def result():
        if rcEnv.sysname != "Windows":
            os.setsid()
        set_rlimits(limits)
        demote(user_uid, user_gid)
    return result

def set_rlimits(limits):
    """
    Set the resource limits for the executed command.
    """
    try:
        import resource
    except ImportError:
        return
    for res, val in limits.items():
        rlim = getattr(resource, "RLIMIT_"+res.upper())
        _vs, _vg = resource.getrlimit(rlim)
        resource.setrlimit(rlim, (val, val))

def demote(user_uid, user_gid):
    """
    Return a privilege demotion function to plug as Popen() preexec_fn keyword
    argument, customized for <user_uid> and <user_gid>.
    """
    os.setgid(user_gid)
    os.setuid(user_uid)

class StatusWARN(Exception):
    """
    A class to raise to signal status() to return a "warn" state.
    """
    pass

class StatusNA(Exception):
    """
    A class to raise to signal status() to return a "n/a" state.
    """
    pass

class App(Resource):
    """
    The App resource driver class.
    """

    def __init__(self, rid=None,
                 script=None,
                 start=None,
                 stop=None,
                 check=None,
                 info=None,
                 timeout=None,
                 start_timeout=None,
                 stop_timeout=None,
                 check_timeout=None,
                 info_timeout=None,
                 status_log=False,
                 user=None,
                 group=None,
                 cwd=None,
                 environment=None,
                 configs_environment=None,
                 secrets_environment=None,
                 **kwargs):

        Resource.__init__(self, rid, **kwargs)
        self.script = script
        self.start_seq = start
        self.stop_seq = stop
        self.check_seq = check
        self.info_seq = info
        self.timeout = timeout
        self.user = user
        self.group = group
        self.cwd = cwd
        self.status_log_flag = status_log
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.check_timeout = check_timeout
        self.info_timeout = info_timeout
        self.label = self.type.split(".")[-1]
        self.environment = environment
        self.configs_environment = configs_environment
        self.secrets_environment = secrets_environment
        if script:
            self.label += ": " + os.path.basename(script)
        elif start:
            self.label += ": " + os.path.basename(start.split()[0])
        self.lockfd = None
        try:
            # compat
            self.sort_key = "app#%d" % int(self.start_seq)
        except (TypeError, ValueError):
            pass

    @lazy
    def lockfile(self):
        """
        Lazy init for the resource lock file path property.
        """
        lockfile = os.path.join(self.var_d, "lock")
        return lockfile

    def validate_on_action(self, cmd):
        """
        Do sanity checks on the resource parameters before running an action.
        """
        cmd = self.validate_script_path(cmd)
        self.validate_script_exec(cmd)
        return cmd

    def validate_script_exec(self, cmd):
        """
        Invalidate the script if the file is not executable or not found.
        """
        if cmd is None:
            return
        if which(cmd[0]) is None:
            fpath = os.path.realpath(cmd[0])
            self.status_log("%s is not executable" % fpath)
            self.set_executable(fpath)

    def validate_script_path(self, cmd):
        """
        Converts the script path to a realpath.
        Invalidate the script if not found.
        If the script is specified as a basename, consider it is to be found
        in the <pathetc>/<name>.d directory.
        """
        if cmd is None:
            return
        if not cmd[0].startswith('/'):
            cmd[0] = os.path.join(self.svc.paths.initd, cmd[0])
        if os.path.exists(cmd[0]):
            os.path.realpath(cmd[0])
            return cmd
        raise ex.excError("%s does not exist" % cmd[0])

    def is_up(self):
        """
        Return 0 if the app resource is up.
        """
        if self.pg_frozen():
            raise StatusNA
        return self._check()

    def _check(self, verbose=True):
        if self.check_seq is False:
            if verbose:
                self.status_log("check is not set", "info")
            raise StatusNA
        try:
            cmd = self.get_cmd("check", "status")
        except ex.excAbortAction:
            raise StatusNA
        except ex.excError as exc:
            self.status_log(str(exc), "warn")
            raise StatusNA
        ret = self.run("status", cmd, dedicated_log=False)
        return ret

    def _info(self):
        """
        Contribute app resource standard and script-provided key/val pairs
        to the service's resinfo.
        """
        import re
        keyvals = [
            ["script", self.script if self.script else ""],
            ["start", str(self.start_seq) if self.start_seq else ""],
            ["stop", str(self.stop_seq) if self.stop_seq else ""],
            ["check", str(self.check_seq) if self.check_seq else ""],
            ["info", str(self.info_seq) if self.info_seq else ""],
            ["timeout", str(self.timeout) if self.timeout else ""],
            ["start_timeout", str(self.start_timeout) if self.start_timeout else ""],
            ["stop_timeout", str(self.stop_timeout) if self.stop_timeout else ""],
            ["check_timeout", str(self.check_timeout) if self.check_timeout else ""],
            ["info_timeout", str(self.info_timeout) if self.info_timeout else ""],
        ]
        try:
            cmd = self.get_cmd("info")
        except (ex.excAbortAction, AttributeError):
            return keyvals

        buff = self.run('info', cmd, dedicated_log=False, return_out=True)
        if not is_string(buff) or len(buff) == 0:
            keyvals.append(["Error", "info not implemented in launcher"])
            return keyvals
        for line in buff.splitlines():
            try:
                idx = line.find(':')
            except ValueError:
                continue
            elements = line.split(":", 1)
            keyvals.append([elements[0].strip(), elements[1].strip()])
        return keyvals

    def get_cmd(self, action, script_arg=None, validate=True):
        key = action + "_seq"
        val = getattr(self, key)
        if val in (None, False):
            raise ex.excAbortAction()
        try:
            int(val)
            cmd = [self.script, script_arg if script_arg else action]
        except (TypeError, ValueError):
            try:
                val = convert_boolean(val)
                if val is False:
                    raise ex.excAbortAction()
                cmd = [self.script, script_arg if script_arg else action]
            except ex.excAbortAction:
                raise
            except:
                if six.PY2:
                    cmd = map(lambda s: s.decode('utf8'), shlex.split(val.encode('utf8')))
                else:
                    cmd = shlex.split(val)
                if "|" in cmd or "||" in cmd or "&&" in cmd or any([True for w in cmd if w.endswith(";")]):
                    return val
        if validate:
            cmd = self.validate_on_action(cmd)
        return cmd

    def start(self):
        """
        Start the resource.
        """
        self.create_pg()

        try:
            cmd = self.get_cmd("start")
        except ex.excAbortAction:
            return

        try:
            status = self.is_up()
        except:
            status = 1

        if status == 0:
            self.log.info("%s is already started", self.label)
            return

        ret = self.run("start", cmd)
        if ret != 0:
            raise ex.excError("exit code %d" % ret)
        self.can_rollback = True

    def stop(self):
        """
        Stop the resource.
        """
        try:
            cmd = self.get_cmd("stop")
        except ex.excAbortAction:
            return
        except ex.excError as exc:
            if "does not exist" in str(exc):
                return
            raise

        status = self.status()
        if status == rcStatus.DOWN:
            self.log.info("%s is already stopped", self.label)
            return

        try:
            self.run("stop", cmd)
        except Exception as exc:
            self.log.warning(exc)

    def unlock(self):
        """
        Release the app action lock.
        """
        if not self.lockfd:
            return
        self.log.debug("release app lock")
        lock.unlock(self.lockfd)
        try:
            os.unlink(self.lockfile)
        except OSError:
            pass
        self.lockfd = None

    def lock(self, action=None, timeout=0, delay=1):
        """
        Acquire the app action lock.
        """
        if self.lockfd is not None:
            return

        details = "(timeout %d, delay %d, action %s, lockfile %s)" % \
                  (timeout, delay, action, self.lockfile)
        self.log.debug("acquire app lock %s", details)
        lockfd = None
        try:
            lockfd = lock.lock(
                timeout=timeout,
                delay=delay,
                lockfile=self.lockfile,
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
            self.log.info("interrupted by signal %s" % details)
        except Exception as exc:
            self.save_exc()
            raise ex.excError("unexpected locking error %s: %s" % (details, str(exc)))
        finally:
            if lockfd is not None:
                self.lockfd = lockfd

    def _status(self, verbose=False):
        """
        Return the resource status.
        """
        n_ref_res = len(self.svc.get_resources(['fs', 'ip', 'container', 'share', 'disk']))
        status = self.svc.group_status(excluded_groups=set([
            "sync",
            "app",
            "disk.scsireserv",
            "disk.drbd",
            "task"
        ]))
        if n_ref_res > 0 and str(status["avail"]) not in ("up", "n/a"):
            self.log.debug("abort resApp status because needed resources avail status is %s", status["avail"])
            self.status_log("not evaluated (instance not up)", "info")
            return rcStatus.NA

        try:
            ret = self.is_up()
        except StatusWARN:
            return rcStatus.WARN
        except StatusNA:
            return rcStatus.NA
        except ex.excError as exc:
            msg = str(exc)
            if "intent '" in msg:
                action = msg.split("intent '")[-1].split("'")[0]
                self.status_log("%s in progress" % action, "info")
            self.log.debug("resource status forced to n/a: an action is running")
            return rcStatus.NA

        if ret == 0:
            return rcStatus.UP
        elif ret == 1:
            return rcStatus.DOWN

        self.status_log("check reports errors (%d)" % ret)
        return rcStatus.WARN

    def set_executable(self, fpath):
        """
        Switch the script file execution bit to on.
        """
        if not os.path.exists(fpath):
            return
        self.vcall(['chmod', '+x', fpath])

    def run(self, action, cmd, dedicated_log=True, return_out=False):
        """
        Acquire the app resource lock, run the action and release for info, start
        and stop actions.
        Or acquire-release the app resource lock and run status.
        """
        self.lock(action)
        if action == "status":
            self.unlock()
        try:
            return self._run(action, cmd, dedicated_log=dedicated_log, return_out=return_out)
        finally:
            self.unlock()

    def _run(self, action, cmd, dedicated_log=True, return_out=False):
        """
        Do script validations, run the command associated with the action and
        catch errors.
        """
        try:
            return self._run_cmd(action, cmd, dedicated_log=dedicated_log, return_out=return_out)
        except OSError as exc:
            if exc.errno == 8:
                if not return_out and not dedicated_log:
                    self.status_log("exec format error")
                    raise StatusWARN()
                else:
                    self.log.error("execution error (Exec format error)")
            elif exc.errno == 13:
                if not return_out and not dedicated_log:
                    self.status_log("permission denied")
                    raise StatusWARN()
                else:
                    self.log.error("execution error (Permission Denied)")
            else:
                self.svc.save_exc()
            return 1
        except ex.excError as exc:
            self.log.error(exc)
            return 1
        except:
            self.svc.save_exc()
            return 1

    def netns_formatter(self, cmd):
        if rcEnv.sysname != "Linux":
            return cmd
        if which("ip") is None:
            return cmd
        resources = [res for res in self.svc.get_resources("ip.cni") if res.container_rid is None]
        if not resources:
            return cmd
        return ["/sbin/ip", "netns", "exec", resources[0].nspid] + cmd

    def _run_cmd(self, action, cmd, dedicated_log=True, return_out=False):
        """
        Switch between buffered outputs or polled execution.
        Return stdout if <return_out>, else return the returncode.
        """
        if dedicated_log:
            if action == "start":
                cmd = self.netns_formatter(cmd)
            return self._run_cmd_dedicated_log(action, cmd)
        else:
            try:
                kwargs = self.common_popen_kwargs(cmd)
            except ValueError as exc:
                if return_out:
                    return "error: %s" % str(exc)
                else:
                    self.log.error("%s", exc)
                    return 1
            if return_out:
                ret, out, err = self.call(cmd, **kwargs)
                if ret != 0:
                    return "Error: info not implemented in launcher"
                return out
            else:
                kwargs["outlog"] = False
                kwargs["errlog"] = False
                ret, out, err = self.call(cmd, **kwargs)
                if self.status_log_flag:
                    out = out.strip()
                    for line in out.splitlines():
                        self.status_log(line, "info")
                    err = err.strip()
                    for line in err.splitlines():
                        self.status_log(line, "warn")
                self.log.debug("%s returned out=[%s], err=[%s], ret=[%d]", cmd, out, err, ret)
                return ret

    @lazy
    def limits(self):
        data = {}
        try:
            import resource
        except ImportError:
            return data
        for key in ("as", "cpu", "core", "data", "fsize", "memlock", "nofile", "nproc", "rss", "stack", "vmem"):
            try:
                data[key] = self.conf_get("limit_"+key)
            except ex.OptNotFound:
                continue
            rlim = getattr(resource, "RLIMIT_"+key.upper())
            _vs, _vg = resource.getrlimit(rlim)
            if data[key] > _vs:
                if data[key] > _vg:
                    _vg = data[key]
                resource.setrlimit(rlim, (data[key], _vg))
        return data

    def get_timeout(self, action):
        action_timeout = getattr(self, action+"_timeout")
        if action_timeout is not None:
            return action_timeout
        return self.timeout

    def common_popen_kwargs(self, cmd):
        kwargs = {
            'stdin': self.svc.node.devnull,
        }
        if isinstance(cmd, list):
            kwargs.update(run_as_popen_kwargs(cmd[0], self.limits, self.user, self.group, self.cwd))
        else:
            kwargs.update(run_as_popen_kwargs(None, self.limits, self.user, self.group, self.cwd))
            kwargs["shell"] = True
        if "env" in kwargs:
            kwargs["env"]["OPENSVC_RID"] = self.rid
            kwargs["env"]["OPENSVC_NAME"] = self.svc.name
            kwargs["env"]["OPENSVC_KIND"] = self.svc.kind
            kwargs["env"]["OPENSVC_ID"] = self.svc.name
            kwargs["env"]["OPENSVC_SVCNAME"] = self.svc.name # deprecated
            kwargs["env"]["OPENSVC_SVC_ID"] = self.svc.id # deprecated
            if self.svc.namespace:
                kwargs["env"]["OPENSVC_NAMESPACE"] = self.svc.namespace
        if self.configs_environment or self.secrets_environment:
            if "env" not in kwargs:
                kwargs["env"] = {}
            kwargs["env"].update(self.kind_environment_env("cfg", self.configs_environment))
            kwargs["env"].update(self.kind_environment_env("cfg", self.secrets_environment))
        if self.environment:
            for mapping in self.environment:
                try:
                    var, val = mapping.split("=", 1)
                except Exception as exc:
                    self.log.info("ignored environment mapping %s: %s", mapping, exc)
                    continue
                var = var.upper()
                kwargs["env"][var] = val

        return kwargs

    def _run_cmd_dedicated_log(self, action, cmd):
        """
        Poll stdout and stderr to log as soon as new lines are available.
        """
        now = datetime.now()
        for lim, val in self.limits.items():
            self.log.info("set limit %s = %s", lim, val)
        kwargs = {
            'timeout': self.get_timeout(action),
            'logger': self.log,
        }
        try:
            kwargs.update(self.common_popen_kwargs(cmd))
        except ValueError as exc:
            self.log.error("%s", exc)
            return 1
        user = kwargs.get("env").get("LOGNAME")
        if isinstance(cmd, list):
            cmd_s = ' '.join(cmd)
        else:
            cmd_s = cmd
        self.log.info('exec %s as user %s', cmd_s, user)
        try:
            ret = lcall(cmd, **kwargs)
        except (KeyboardInterrupt, ex.excSignal):
            _len = datetime.now() - now
            self.log.info('%s interrupted after %s - ret %d',
                           action, _len, 1)
            return 0
        _len = datetime.now() - now
        msg = '%s done in %s - ret %d' % (action, _len, ret)
        if ret == 0:
            self.log.info(msg)
        else:
            self.log.error(msg)
        return ret

