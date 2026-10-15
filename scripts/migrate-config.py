#!/usr/bin/env python3
"""Merge legacy TOML files into one private config. Local filesystem only."""
import argparse
import json
import os
from pathlib import Path
import tomllib


def render(data, prefix=()):
    lines = []
    if prefix:
        lines.append('[' + '.'.join(json.dumps(k) for k in prefix) + ']')
    for key, value in data.items():
        if isinstance(value, dict):
            continue
        if not isinstance(value, (str, int, float, bool, list)):
            raise ValueError('Unsupported configuration value')
        lines.append(json.dumps(key) + ' = ' + json.dumps(value, ensure_ascii=False, allow_nan=False))
    for key, value in data.items():
        if isinstance(value, dict):
            lines.extend(['', render(value, (*prefix, key))])
    return '\n'.join(lines) + '\n'


def migrate(base, jump, destination):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Destination already exists; not overwritten')
    with Path(base).open('rb') as stream:
        data = tomllib.load(stream)
    with Path(jump).open('rb') as stream:
        extra = tomllib.load(stream)
    # Match Config's existing precedence exactly; no unrelated overlay fields.
    for section in ('jump', 'board'):
        data[section] = {**data.get(section, {}), **extra.get(section, {})}
    text = render(data)
    if tomllib.loads(text) != data:
        raise ValueError('Configuration serialization changed values')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    # Keep legacy files intact for frozen callers; this never contacts hardware.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--jump', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        migrate(args.base, args.jump, args.output)
    except (OSError, ValueError, TypeError):
        parser.exit(2, 'Migration refused: invalid input or destination already exists; no credentials printed.\n')
    print('Private unified config created; legacy files preserved. No remote operations.')


if __name__ == '__main__':
    main()
