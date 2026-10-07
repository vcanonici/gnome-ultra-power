#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private root PAM worker. No password prompts, secrets or reusable grants."""

import ctypes as C
import ctypes.util
import json
import os
import pwd
import sys
from typing import Any


class Message(C.Structure):
    _fields_ = [("style", C.c_int), ("text", C.c_char_p)]


class Response(C.Structure):
    _fields_ = [("text", C.c_void_p), ("code", C.c_int)]


Callback = C.CFUNCTYPE(
    C.c_int,
    C.c_int,
    C.POINTER(C.POINTER(Message)),
    C.POINTER(C.POINTER(Response)),
    C.c_void_p,
)


class Conversation(C.Structure):
    _fields_ = [("callback", Callback), ("data", C.c_void_p)]


def authenticate(username: str) -> bool:
    pam = C.CDLL(ctypes.util.find_library("pam"))
    libc = C.CDLL(ctypes.util.find_library("c"))
    libc.calloc.argtypes = [C.c_size_t, C.c_size_t]
    libc.calloc.restype = C.c_void_p
    libc.free.argtypes = [C.c_void_p]

    def callback(count: int, messages: Any, replies: Any, data: Any) -> int:
        memory = libc.calloc(count, C.sizeof(Response))
        if not memory:
            return 5
        responses = C.cast(memory, C.POINTER(Response))
        for i in range(count):
            if messages[i].contents.style not in (3, 4):
                libc.free(memory)
                return 19
            # Present canonical Portuguese UI, never forward module text to logs.
            print(
                json.dumps(
                    {"event": "fingerprint", "retry": messages[i].contents.style == 3}
                ),
                flush=True,
            )
        replies[0] = responses
        return 0

    native_callback = Callback(callback)
    conv = Conversation(native_callback, None)
    handle = C.c_void_p()
    pam.pam_start.argtypes = [
        C.c_char_p,
        C.c_char_p,
        C.POINTER(Conversation),
        C.POINTER(C.c_void_p),
    ]
    pam.pam_authenticate.argtypes = [C.c_void_p, C.c_int]
    pam.pam_acct_mgmt.argtypes = [C.c_void_p, C.c_int]
    pam.pam_end.argtypes = [C.c_void_p, C.c_int]
    result = pam.pam_start(
        b"ultra-power", username.encode(), C.byref(conv), C.byref(handle)
    )
    try:
        if result == 0:
            result = pam.pam_authenticate(handle, 0)
        if result == 0:
            result = pam.pam_acct_mgmt(handle, 0)
        return bool(result == 0)
    finally:
        if handle:
            pam.pam_end(handle, result)


if __name__ == "__main__":
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit(2)
    account = pwd.getpwnam(sys.argv[1])
    if account.pw_uid < 1000:
        raise SystemExit(2)
    raise SystemExit(0 if authenticate(account.pw_name) else 1)
