import resTask
import rcExceptions as ex
import os

from rcGlobalEnv import rcEnv

def run_as_popen_kwargs(user):
    if rcEnv.sysname == "Windows":
        return {}
    if user is None:
        return {}
    cwd = rcEnv.paths.pathtmp
    import pwd
    try:
        pw_record = pwd.getpwnam(user)
    except Exception as exc:
        raise ex.excError("user lookup failure: %s" % str(exc))
    user_name      = pw_record.pw_name
    user_home_dir  = pw_record.pw_dir
    user_uid  = pw_record.pw_uid
    user_gid  = pw_record.pw_gid
    env = os.environ.copy()
    env['HOME']  = user_home_dir
    env['LOGNAME']  = user_name
    env['PWD']  = cwd
    env['USER']  = user_name
    return {'preexec_fn': demote(user_uid, user_gid), 'cwd': cwd, 'env': env}

def demote(user_uid, user_gid):
    def result():
        os.setgid(user_gid)
        os.setuid(user_uid)
    return result

class Task(resTask.Task):
    def __init__(self, *args, **kwargs):
        kwargs["type"] = "task.host"
        resTask.Task.__init__(self, *args, **kwargs)

    def _run_call(self):
        kwargs = {
            'timeout': self.timeout,
            'blocking': True,
        }
        kwargs.update(run_as_popen_kwargs(self.user))
        if self.configs_environment or self.secrets_environment:
            if "env" not in kwargs:
                kwargs["env"] = {}
            kwargs["env"].update(self.kind_environment_env("cfg", self.configs_environment))
            kwargs["env"].update(self.kind_environment_env("sec", self.secrets_environment))
        if self.environment:
            if "env" not in kwargs:
                kwargs["env"] = {}
            for mapping in self.environment:
                try:
                    var, val = mapping.split("=", 1)
                except Exception as exc:
                    self.log.info("ignored environment mapping %s: %s", mapping, exc)
                    continue
                var = var.upper()
                kwargs["env"][var] = val

        try:
            self.action_triggers("", "command", **kwargs)
            self.write_last_run_retcode(0)
        except ex.excError:
            self.write_last_run_retcode(1)
            if self.on_error:
                kwargs["blocking"] = False
                self.action_triggers("", "on_error", **kwargs)
            raise
