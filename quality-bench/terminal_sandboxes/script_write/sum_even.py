#!/usr/bin/env python3
import sys

total = 0
with open('pairs.csv') as f:
    next(f)  # skip header
    for line in f:
        line = line.strip()
        if not line:
            continue
        a, b = map(int, line.split(','))
        s = a + b
        if s % 2 == 0:
            total += s
print(total)
