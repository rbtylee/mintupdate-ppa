#!/usr/bin/python3

import os
import sys

import psutil

from common.constants import NAMED_PIPE
from main.MintUpdate import MintUpdate


if __name__ == "__main__":
    args = sys.argv[1:]
    arg_exit = "exit" in args
    already_running = False
    try:
        _pid = os.getpid()
        _uid = os.getuid()
        for proc in psutil.process_iter():
            if proc.pid == _pid or proc.uids().real != _uid:
                # ignore processes from other users and this process
                continue
            if proc.name() == "mintUpdate":
                if "restart" in args or arg_exit:
                    proc.kill()
                else:
                    already_running = True
    except:
        pass

    if not arg_exit:
        if not already_running:
            del args
            if os.path.exists(NAMED_PIPE):
                try:
                    os.unlink(NAMED_PIPE)
                except:
                    pass
            import setproctitle
            setproctitle.setproctitle("mintUpdate")
            MintUpdate()
        elif args:
            with open(NAMED_PIPE, "w") as f:
                for arg in args:
                    f.write(arg)
