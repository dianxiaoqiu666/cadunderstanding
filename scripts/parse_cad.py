"""Parse a local CAD into the same validated JSON returned by the HTTP API."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError
from services.understanding.cad_io import MAX_CAD_BYTES
from services.understanding.geometry import InputError
from services.understanding.parser import parse_bytes


def main():
    parser = argparse.ArgumentParser(description='DXF / DWG → JSON (UTF-8)')
    parser.add_argument('input', type=Path)
    parser.add_argument('-o', '--output', type=Path, help='默认输出到标准输出；文件路径须位于本项目内')
    parser.add_argument('--options', type=Path, help='ParseOptions JSON 文件')
    output_kind = parser.add_mutually_exclusive_group()
    output_kind.add_argument('--array', action='store_true', help='仅输出扁平构件数组')
    output_kind.add_argument('--usable-area', action='store_true', help='仅输出可用区域及约束 JSON')
    parser.add_argument('--require-3d-ready', action='store_true', help='3D 条件不完整时仍输出 JSON，返回状态码 3')
    parser.add_argument('--require-placement-ready', action='store_true', help='可用区域仍需核实/为空时输出 JSON，返回状态码 3')
    parser.add_argument('--force', action='store_true', help='明确允许覆盖已有输出（原 CAD 始终受保护）')
    args = parser.parse_args()
    try:
        source = args.input.resolve()
        target = args.output.resolve() if args.output else None
        if target is not None:
            if not target.is_relative_to(ROOT) or target == source or target.suffix.lower() != '.json':
                raise InputError('OUTPUT_PATH_INVALID', '输出必须是本项目内独立的 .json 文件，不能覆盖原 CAD。')
            if target.exists() and not args.force:
                raise InputError('OUTPUT_EXISTS', '输出已存在；选择新文件名或使用 --force。')
        options = json.loads(args.options.read_text(encoding='utf-8-sig')) if args.options else {}
        with source.open('rb') as stream:
            data = stream.read(MAX_CAD_BYTES + 1)
        package = parse_bytes(data, source.name, options)
        result = [e.model_dump(mode='json') for e in package.components()] if args.array else package.model_dump(mode='json')
        if args.usable_area:
            result = package.usable_area.model_dump(mode='json')
        text = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
        if target is None:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stdout.write(text)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write in one step only after successful validation/serialization.
            with target.open('w' if args.force else 'x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
            print(json.dumps({'output': str(target), 'status': package.status,
                'counts': package.summary['counts'], 'reconstruction': package.reconstruction['status'],
                'usable_area': package.usable_area.status}, ensure_ascii=True))
        return 3 if (args.require_3d_ready and package.reconstruction['status'] != 'READY') or (
            args.require_placement_ready and not package.usable_area.ready_for_placement) else 0
    except (InputError, OSError, ValidationError, ValueError) as exc:
        error = {'error': {'code': exc.code if isinstance(exc, InputError) else 'INPUT_INVALID',
                           'message': str(exc)}}
        print(json.dumps(error, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
