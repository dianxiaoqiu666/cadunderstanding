"""Bounded local CAD reading; DWG conversion only touches an isolated workspace."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import ezdxf
from ezdxf.lldxf.types import DOUBLE
from services.understanding.geometry import InputError

ROOT = Path(__file__).resolve().parents[2]
MAX_CAD_BYTES = 25 * 1024 * 1024
MAX_CONVERTED_BYTES = 100 * 1024 * 1024


def find_converter() -> Path | None:
    configured = os.environ.get('CAD_ODA_CONVERTER')
    if configured:
        path = Path(configured)
        return path.resolve() if path.is_absolute() and path.is_file() else None
    found = shutil.which('ODAFileConverter')
    if found:
        return Path(found).resolve()
    candidates = []
    for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
        base = Path(os.environ.get(variable, 'C:/Program Files')) / 'ODA'
        if base.is_dir():
            candidates.extend(base.glob('ODAFileConverter*/ODAFileConverter.exe'))
    return sorted(candidates, key=lambda p: p.parent.name, reverse=True)[0] if candidates else None


def convert_file(source: Path, output_dir: Path, output_format: str = 'DXF', timeout: int = 60) -> Path:
    """ODA's documented CLI, audit disabled; no shell and no original overwrite."""
    converter = find_converter()
    if converter is None:
        raise InputError('DWG_CONVERTER_UNAVAILABLE', 'DWG 需要 ODA File Converter；可用 CAD_ODA_CONVERTER 指定已安装的绝对路径。')
    if output_format not in {'DWG', 'DXF'}:
        raise ValueError('Invalid conversion format')
    output_dir.mkdir(parents=True, exist_ok=True)
    if source.parent.resolve() == output_dir.resolve():
        raise ValueError('Conversion input and output folders must differ')
    # Each folder is private to this request and contains only this upload.
    command = [str(converter), str(source.parent), str(output_dir), 'ACAD2018',
               output_format, '0', '0', source.name]
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout,
                                startupinfo=startupinfo, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as exc:
        raise InputError('DWG_CONVERSION_TIMEOUT', 'DWG 转换超时。') from exc
    except OSError as exc:
        raise InputError('DWG_CONVERTER_FAILED', '无法启动本机 DWG 转换程序。') from exc
    candidates = [p for p in output_dir.iterdir() if p.stem.casefold() == source.stem.casefold()
                  and p.suffix.casefold() == '.' + output_format.lower()]
    if result.returncode != 0 or len(candidates) != 1 or not candidates[0].stat().st_size:
        raise InputError('DWG_CONVERSION_FAILED', 'DWG 转换没有生成有效文件，请检查图纸是否损坏、加密或缺少外部参照。')
    if candidates[0].stat().st_size > MAX_CONVERTED_BYTES:
        raise InputError('CAD_EXPANSION_LIMIT', '转换后的 DXF 超过 100 MB 上限。')
    return candidates[0]


def _check_raw_numbers(data: bytes):
    # ASCII numeric tags are encoding-independent. Check BEFORE ezdxf applies defaults.
    if data.startswith(b'AutoCAD Binary DXF'):
        from ezdxf.lldxf.tagger import binary_tags_loader
        tags = binary_tags_loader(data)
        pairs = ((tag.code, tag.value) for tag in tags)
    else:
        lines = data.lstrip(b'\xef\xbb\xbf').splitlines()
        if len(lines) % 2:
            raise InputError('CAD_PARSE_FAILED', 'DXF 组码/值不成对。')
        try:
            pairs = [(int(lines[i].strip()), lines[i + 1].strip()) for i in range(0, len(lines), 2)]
        except ValueError as exc:
            raise InputError('CAD_PARSE_FAILED', 'DXF 包含非法组码。') from exc
    unsafe_hatches = {}
    hatch = None
    def finish_hatch():
        if hatch is not None and hatch['styles'] != [0]:
            unsafe_hatches[hatch['handle']] = hatch['styles']
    try:
        for code, value in pairs:
            if code in DOUBLE and not math.isfinite(float(value)):
                raise InputError('NONFINITE_DXF_VALUE', 'DXF 含 NaN 或 Infinity 数值。')
            if code == 0:
                finish_hatch()
                is_hatch = value == b'HATCH' or value == 'HATCH'
                hatch = {'handle': '', 'styles': []} if is_hatch else None
            elif hatch is not None and code == 5:
                hatch['handle'] = value.decode('ascii') if isinstance(value, bytes) else str(value)
            elif hatch is not None and code == 75:
                try:
                    hatch['styles'].append(int(value))
                except (ValueError, TypeError):
                    hatch['styles'].append('INVALID')
        finish_hatch()
    except (ValueError, TypeError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError('CAD_PARSE_FAILED', 'DXF 包含非法数值。') from exc
    return unsafe_hatches


def read_cad(data: bytes, filename: str):
    filename = Path(filename.replace('\\', '/')).name
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.dxf', '.dwg'}:
        raise InputError('CAD_FORMAT_UNSUPPORTED', '支持 .dxf 和 .dwg 文件。')
    if not data or len(data) > MAX_CAD_BYTES:
        raise InputError('CAD_SIZE_INVALID', '请提供非空且不超过 25 MB 的 CAD。')
    if suffix == '.dwg' and (len(data) < 6 or not data[:6].startswith(b'AC10')):
        raise InputError('DWG_HEADER_INVALID', '文件不是支持的 AutoCAD DWG，扩展名不能代替格式转换。')
    work = ROOT / '.tmp/cad'
    work.mkdir(parents=True, exist_ok=True)
    conversion = None
    with tempfile.TemporaryDirectory(dir=work, prefix='read-') as directory:
        folder = Path(directory)
        input_dir = folder / 'input'
        input_dir.mkdir()
        source = input_dir / ('drawing' + suffix)
        source.write_bytes(data)
        parse_path = source
        if suffix == '.dwg':
            parse_path = convert_file(source, folder / 'converted')
            converted = parse_path.read_bytes()
            conversion = {'engine': 'ODA File Converter', 'audit_requested': False,
                          'converted_dxf_sha256': hashlib.sha256(converted).hexdigest(),
                          'converted_byte_count': len(converted), 'source_handle_namespace': 'CONVERTED_DXF'}
        try:
            unsafe_hatches = _check_raw_numbers(parse_path.read_bytes())
            doc = ezdxf.readfile(parse_path, errors='strict')
        except InputError:
            raise
        except Exception as exc:
            raise InputError('CAD_PARSE_FAILED', f'无法读取 CAD：{type(exc).__name__}。') from exc
    return doc, filename, conversion, unsafe_hatches
