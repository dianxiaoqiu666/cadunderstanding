"""Windows user-bound DPAPI storage for the single locally configured API key."""
import ctypes
from ctypes import wintypes
import os

from services.understanding.geometry import InputError


def _crypt(data: bytes, *, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise InputError('MODEL_KEY_STORAGE_UNAVAILABLE', '当前系统不支持本机密钥保存；可使用 API Key 环境变量。')

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    # UI_FORBIDDEN, with current-user protection (not LOCAL_MACHINE).
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise InputError('MODEL_KEY_STORAGE_ERROR', '无法读取或保存本机 API Key，请在模型设置中重新填写。')
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(output.pbData, 0, output.cbData)
        kernel.LocalFree(output.pbData)
        ctypes.memset(buffer, 0, len(data))


def protect(data: bytes) -> bytes:
    return _crypt(data)


def unprotect(data: bytes) -> bytes:
    return _crypt(data, decrypt=True)
