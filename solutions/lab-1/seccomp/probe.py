"""Report a real getppid syscall result, including errno, without caching."""

import ctypes as C
import errno
import os


library = C.CDLL("libseccomp.so.2")
library.seccomp_syscall_resolve_name.argtypes = [C.c_char_p]
library.seccomp_syscall_resolve_name.restype = C.c_int
number = library.seccomp_syscall_resolve_name(b"getppid")
if number < 0:
    raise RuntimeError("getppid is unknown on this architecture")

libc = C.CDLL(None, use_errno=True)
libc.syscall.argtypes = [C.c_long]
libc.syscall.restype = C.c_long
C.set_errno(0)
result = libc.syscall(number)
error = C.get_errno()

print(f"uid={os.getuid()} pid={os.getpid()}")
with open("/proc/self/status", encoding="utf-8") as source:
    for line in source:
        if line.startswith(("CapEff:", "NoNewPrivs:", "Seccomp:", "Seccomp_filters:")):
            print(line.rstrip())
print(f"getppid syscall={number} result={result} errno={error} "
      f"({errno.errorcode.get(error, 'OK')})")
