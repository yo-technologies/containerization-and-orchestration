"""Load the lab's allow-by-default/ERRNO profile and exec one command.

This teaching loader supports only the profile fields used in this exercise.
libseccomp resolves syscall names for the current architecture.
"""

import ctypes as C
import json
import os
import sys


def main():
    if len(sys.argv) < 3:
        raise ValueError("usage: run.py PROFILE COMMAND [ARG ...]")
    with open(sys.argv[1], encoding="utf-8") as source:
        profile = json.load(source)
    if set(profile) != {"defaultAction", "syscalls"}:
        raise ValueError("unsupported profile fields")
    if profile["defaultAction"] != "SCMP_ACT_ALLOW":
        raise ValueError("this loader requires SCMP_ACT_ALLOW by default")

    lib = C.CDLL("libseccomp.so.2")
    lib.seccomp_init.argtypes = [C.c_uint32]
    lib.seccomp_init.restype = C.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [C.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = C.c_int
    lib.seccomp_rule_add_array.argtypes = [
        C.c_void_p, C.c_uint32, C.c_int, C.c_uint, C.c_void_p
    ]
    lib.seccomp_rule_add_array.restype = C.c_int
    lib.seccomp_load.argtypes = [C.c_void_p]
    lib.seccomp_load.restype = C.c_int
    lib.seccomp_release.argtypes = [C.c_void_p]
    lib.seccomp_release.restype = None

    def check(result, operation):
        if result < 0:
            raise OSError(-result, operation + ": " + os.strerror(-result))

    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise MemoryError("seccomp_init failed")
    try:
        for rule in profile["syscalls"]:
            if set(rule) != {"names", "action", "errnoRet"}:
                raise ValueError("unsupported syscall rule fields")
            if rule["action"] != "SCMP_ACT_ERRNO":
                raise ValueError("this loader supports only ERRNO rules")
            error = rule["errnoRet"]
            if type(error) is not int or not 1 <= error <= 4095:
                raise ValueError("errnoRet must be between 1 and 4095")
            names = rule["names"]
            if not isinstance(names, list) or not names:
                raise ValueError("names must be a nonempty list")
            for name in names:
                if not isinstance(name, str) or "\0" in name:
                    raise ValueError("invalid syscall name")
                number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
                if number < 0:
                    raise ValueError("unknown syscall: " + name)
                action = 0x00050000 | error  # SCMP_ACT_ERRNO(error)
                check(lib.seccomp_rule_add_array(context, action, number, 0, None),
                      "seccomp_rule_add_array")

        libc = C.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [C.c_int] + [C.c_ulong] * 4
        libc.prctl.restype = C.c_int
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            error = C.get_errno()
            raise OSError(error, os.strerror(error))
        check(lib.seccomp_load(context), "seccomp_load")
    finally:
        lib.seccomp_release(context)

    os.execvp(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, MemoryError) as error:
        print("seccomp loader:", error, file=sys.stderr)
        sys.exit(1)
