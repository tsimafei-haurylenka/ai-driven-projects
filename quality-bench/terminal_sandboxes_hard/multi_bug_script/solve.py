#!/usr/bin/env python3
# BUG: this should print the sum of all ODD numbers in data.txt
# There are 3 bugs.
total = 0
with open("data.txt") as f:
    lines = f.readlines()
for i in range(len(lines)):
    n = int(lines[i].strip())
    if n % 2 != 0:
        total += n
print(total)
