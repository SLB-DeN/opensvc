import resTask
import resContainerPodman

import rcExceptions as ex

class Task(resContainerPodman.Container, resTask.Task):
    def __init__(self, *args, **kwargs):
        kwargs["detach"] = False
        kwargs["type"] = "task.podman"
        resContainerPodman.Container.__init__(self, *args, **kwargs)
        resTask.Task.__init__(self, *args, **kwargs)

    _info = resContainerPodman.Container._info

    def _run_call(self):
        try:
            resContainerPodman.Container.start(self)
            self.write_last_run_retcode(0)
        except ex.excError:
            self.write_last_run_retcode(1)
            raise
        if self.rm:
            self.container_rm()

    def _status(self, *args, **kwargs):
        return resTask.Task._status(self, *args, **kwargs)

