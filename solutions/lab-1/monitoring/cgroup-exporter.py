#!/usr/bin/env python3
"""Read one lab cgroup v2; expose its counters without joining or changing it."""

import argparse
import math
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


CGROUP = Path('/sys/fs/cgroup/lab1-mydocker')
LABELS = '{cgroup="lab1-mydocker"}'


def fields(path):
    return {key: int(value) for key, value in
            (line.split() for line in path.read_text().splitlines())}


def limit(value):
    return math.inf if value == 'max' else int(value)


def collect(directory=CGROUP):
    cpu = fields(directory / 'cpu.stat')
    events = fields(directory / 'memory.events')
    state = fields(directory / 'cgroup.events')
    quota, period = (directory / 'cpu.max').read_text().split()
    if int(period) <= 0:
        raise ValueError('CPU period must be positive')
    metrics = [
        ('memory_current_bytes', 'gauge', 'Memory charged to the cgroup in bytes.',
         int((directory / 'memory.current').read_text())),
        ('memory_limit_bytes', 'gauge', 'Memory ceiling in bytes; +Inf if unlimited.',
         limit((directory / 'memory.max').read_text().strip())),
        ('cpu_usage_seconds_total', 'counter', 'Accumulated CPU time in seconds.',
         cpu['usage_usec'] / 1_000_000),
        ('cpu_periods_total', 'counter', 'Accumulated CPU bandwidth periods.',
         cpu['nr_periods']),
        ('cpu_throttled_periods_total', 'counter', 'Accumulated throttled periods.',
         cpu['nr_throttled']),
        ('cpu_throttled_seconds_total', 'counter', 'Accumulated throttled seconds.',
         cpu['throttled_usec'] / 1_000_000),
        ('cpu_limit_cores', 'gauge', 'CPU quota divided by period; +Inf if unlimited.',
         limit(quota) / int(period)),
        ('memory_oom_kills_total', 'counter', 'Accumulated cgroup OOM kills.',
         events['oom_kill']),
        ('pids_current', 'gauge', 'Tasks currently charged to the cgroup.',
         int((directory / 'pids.current').read_text())),
        ('populated', 'gauge', 'One if the cgroup contains live processes.',
         state['populated']),
    ]
    lines = []
    for suffix, kind, help_text, value in metrics:
        name = 'lab1_cgroup_' + suffix
        number = '+Inf' if value == math.inf else format(value, '.12g')
        lines.extend([f'# HELP {name} {help_text}', f'# TYPE {name} {kind}',
                      f'{name}{LABELS} {number}'])
    return ('\n'.join(lines) + '\n').encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/metrics':
            self.send_error(404)
            return
        try:
            payload = collect()
        except (OSError, ValueError, KeyError) as error:
            self.log_error('Cannot read lab cgroup: %s', error)
            self.send_error(503, 'Cgroup metrics unavailable')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--listen', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=19100)
    args = parser.parse_args()
    with HTTPServer((args.listen, args.port), Handler) as server:
        print(f'Reading {CGROUP}; http://{args.listen}:{args.port}/metrics', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
