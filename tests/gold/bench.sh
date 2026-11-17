#!/usr/bin/env bash
# Measures what a goal costs, from start to result: tests/gold/bench.sh [GOAL_ID...]
#
# Each goal runs as a person would (`auto "GOAL" --yes`) in a fresh copy of
# fixtures/bench-app, then reports its outcome, wall time and what each stage
# (planning, tasks, checks, repairs) spent. Results are appended to
# $GOLD_ROOT/bench.jsonl, one line per goal, to compare changes over time.
# Like gold tests, it calls a real engine and costs tokens.
set -uo pipefail

GOLD_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
AUTO="${AUTO:-$GOLD_DIR/../../bin/auto}"
: "${GOLD_ENGINE:=pi}"
: "${GOLD_PROVIDER=openai}"
: "${GOLD_MODEL=gpt-6-luna}"
: "${GOLD_TIMEOUT:=1800}"
: "${BENCH_ROUTE:=triage}"
case "$BENCH_ROUTE" in
	triage) ROUTE_ARGS=() ;;
	direct|plan) ROUTE_ARGS=(--route "$BENCH_ROUTE") ;;
	*) echo "bench: BENCH_ROUTE must be triage, direct or plan" >&2; exit 2 ;;
esac
GOLD_ROOT="${GOLD_ROOT:-${TMPDIR:-/tmp}/auto-gold}"
LABEL="${BENCH_LABEL:-$(git -C "$GOLD_DIR" log -1 --format=%h 2>/dev/null || echo unknown)}"

# Goals by increasing size: id, then the goal as a person would type it.
declare -A GOALS=(
	[trivial]="Add a .gitignore for this Python project"
	[small]="Add a --shout option to app.py that prints the greeting in upper case, with a test"
	[multi]="Add a README explaining how to run the app and its tests, and a Makefile with run and test targets that the README uses"
)
ORDER=(trivial small multi)
[ "$#" -eq 0 ] || ORDER=("$@")

command -v jq >/dev/null || {
	echo "bench: jq is required" >&2
	exit 2
}
mkdir -p "$GOLD_ROOT"
export NO_COLOR=1 TERM=dumb

printf '%-8s %-10s %7s  %s\n' GOAL OUTCOME SECONDS SPENT
for id in "${ORDER[@]}"; do
	goal="${GOALS[$id]:-}"
	[ -n "$goal" ] || {
		echo "bench: unknown goal $id (one of: ${!GOALS[*]})" >&2
		exit 2
	}
	sandbox="$(mktemp -d "$GOLD_ROOT/bench-$id-XXXX")"
	cp -R "$GOLD_DIR/fixtures/bench-app/." "$sandbox/"
	mkdir -p "$sandbox/.auto"
	{
		echo "[runtime]"
		echo "engine = \"$GOLD_ENGINE\""
		# Pi execution receives Auto's explicit prompt, not ambient skills or context files.
		[ "$GOLD_ENGINE" != pi ] || echo "isolated = true"
		[ -z "$GOLD_PROVIDER" ] || echo "provider = \"$GOLD_PROVIDER\""
		[ -z "$GOLD_MODEL" ] || echo "model = \"$GOLD_MODEL\""
	} >"$sandbox/.auto/config.toml"
	git -C "$sandbox" init -q
	git -C "$sandbox" add -A
	git -C "$sandbox" -c user.name=bench -c user.email=bench@localhost commit -qm fixture
	started="$(date +%s.%N)"
	(cd "$sandbox" && timeout "$GOLD_TIMEOUT" "$AUTO" "$goal" "${ROUTE_ARGS[@]}" --yes --no-input --format json </dev/null) >"$sandbox.out" 2>"$sandbox.err"
	status=$?
	# Ambient engine tools (eg. the runner's own OpenCode skills) are declined, as a person would, so results
	# do not depend on who runs the benchmark; the plan then goes on. Other questions end the goal.
	for ((round = 0; round < 5 && status == 3; round++)); do
		declined=0
		while IFS= read -r gap; do
			[ -n "$gap" ] || continue
			(cd "$sandbox" && "$AUTO" x gap decide "$gap" '"reject"' --rationale "Not needed for the benchmark" --format json </dev/null) >>"$sandbox.err" 2>&1 && declined=1
		done < <(cd "$sandbox" && "$AUTO" x gap list --format json 2>/dev/null | jq -r '.[] | select(.status == "Open" and ((.runtime_policy.effect // "") | test("^engine\\."))) | .id')
		[ "$declined" = 1 ] || break
		(cd "$sandbox" && timeout "$GOLD_TIMEOUT" "$AUTO" go --no-input --format json </dev/null) >>"$sandbox.out" 2>>"$sandbox.err"
		status=$?
	done
	seconds="$(echo "$(date +%s.%N) - $started" | bc)"
	summary="$(jq -c 'select(.kind == "PlanSummary") | .data' "$sandbox.out" 2>/dev/null | tail -1)"
	[ -n "$summary" ] || summary='{}'
	outcome="$(jq -r '.stop // "error"' <<<"$summary")"
	[ "$outcome" != ended ] || outcome="$(jq -r '.status' <<<"$summary")"
	spent="$(jq -r '[(.spending // [])[] | "\(.stage) \((.tokens // 0) / 1000 | floor)k/\(.turns // "?")t" + (if .cost_estimate then " $\(.cost_estimate * 10000 | round / 10000)" else "" end)] | join(", ")' <<<"$summary")"
	printf '%-8s %-10s %7.1f  %s\n' "$id" "$outcome" "$seconds" "$spent"
	jq -cn --arg label "$LABEL" --arg route "$BENCH_ROUTE" --arg id "$id" --arg goal "$goal" --arg engine "$GOLD_ENGINE" --arg model "$GOLD_PROVIDER/$GOLD_MODEL" \
		--argjson status "$status" --argjson seconds "$seconds" --arg sandbox "$sandbox" --argjson summary "$summary" \
		'{ts: (now | todate), label: $label, route: $route, goal: $id, text: $goal, engine: $engine, model: $model, status: $status, seconds: $seconds,
		outcome: ($summary.stop // "error"), plan: $summary.status, tasks: $summary.total, done: $summary.done,
		changed: (($summary.changed // []) | map(.path)), reason: $summary.reason, spending: ($summary.spending // []), sandbox: $sandbox}' \
		>>"$GOLD_ROOT/bench.jsonl"
done
echo "Results appended to $GOLD_ROOT/bench.jsonl (label $LABEL)"
