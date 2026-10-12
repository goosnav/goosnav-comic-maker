#!/bin/sh
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
./run.sh "$@"
result=$?
if [ "$result" -ne 0 ]; then
  printf '\nThe app could not start. Read the message above, then press Return.\n'
  read answer
fi
exit "$result"
