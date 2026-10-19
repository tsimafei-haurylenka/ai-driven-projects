#!/bin/bash
# BUG: this is supposed to print the SUM of all numbers in data.txt
total=0
for n in $(cat data.txt); do
  total=$((total + n))
done
echo $total
