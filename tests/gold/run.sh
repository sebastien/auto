#!/usr/bin/env bash
# Runs gold tests one after another: tests/gold/run.sh [TEST...]
# TEST is a file or an id prefix (eg. T001-01); all T*.sh by default.
# Gold tests call real engines and cost tokens: see tests/gold/README.md.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 2

declare -a tests=() failed=()
if [ "$#" -eq 0 ]; then
	tests=(T*.sh)
else
	for name in "$@"; do
		name="$(basename "$name")"
		match=("${name%.sh}"*.sh)
		[ -f "${match[0]}" ] || {
			echo "No gold test matches: $name" >&2
			exit 2
		}
		tests+=("${match[@]}")
	done
fi

for test in "${tests[@]}"; do
	bash "$test" || failed+=("$test")
done

echo
echo "Gold tests: $((${#tests[@]} - ${#failed[@]}))/${#tests[@]} passed"
for test in "${failed[@]}"; do
	echo "  FAILED $test"
done
[ "${#failed[@]}" -eq 0 ]
