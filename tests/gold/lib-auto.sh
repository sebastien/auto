#!/usr/bin/env bash
# --
# Helpers for gold tests: end-to-end Auto scenarios against a real engine.
#
# Gold tests only use the `auto` CLI (arguments, exit statuses and
# `--format json` output) and the files a plan produces. Every command's
# stdout, stderr and status are kept under `$TEST_PATH/log/`; the sandbox is
# kept when a test fails or GOLD_KEEP=1.
# shellcheck disable=SC2016 # jq programs use $variables

set -euo pipefail

GOLD_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
AUTO_ROOT="$(cd "$GOLD_DIR/../.." && pwd)"
AUTO="${AUTO:-$AUTO_ROOT/bin/auto}"

# Variable: GOLD_* settings, see tests/gold/README.md
: "${GOLD_ENGINE:=opencode}"
# Set empty to leave them unset in the sandbox configuration
: "${GOLD_PROVIDER=openai}"
: "${GOLD_MODEL=gpt-6-luna}"
: "${GOLD_TIMEOUT:=1800}"
: "${GOLD_MAX_ROUNDS:=8}"
: "${GOLD_KEEP:=0}"
: "${GOLD_REFINE:=1}"
: "${GOLD_COST_WARN:=1}"
# Runtime policy approvals to decline (regex on the effect): ambient engine tools
: "${GOLD_DECLINE:=^engine\\.}"

# Sandboxes must live outside the Auto checkout: Auto and the engine's agent
# would otherwise discover this repository's `.auto/` and `.memo/`.
GOLD_ROOT="${GOLD_ROOT:-${TMPDIR:-/tmp}/auto-gold}"
mkdir -p "$GOLD_ROOT"
cd "$GOLD_ROOT"
# shellcheck source=lib-testing.sh
source "$GOLD_DIR/lib-testing.sh"

export NO_COLOR=1 TERM=dumb

# Variable: SANDBOX
# The project directory of the current test, inside TEST_PATH.
SANDBOX=""
# Variable: PLAN
# The plan id under test.
PLAN=""
# Variables: LAST, LAST_OUT, LAST_STATUS
# Log base path, stdout file and exit status of the latest `auto-step`.
LAST=""
LAST_OUT=""
LAST_STATUS=""
AUTO_STEP=0

# Function: test_cleanup
# Overrides lib-testing: keeps the sandbox when the test failed or GOLD_KEEP=1.
function test_cleanup {
	if [ -n "${TEST_PATH:-}" ] && [ -e "$TEST_PATH" ]; then
		if [ "$GOLD_KEEP" = 1 ] || grep -q '^FAIL' "$TEST_PATH/.test-results" 2>/dev/null; then
			test_log "${ORANGE}Kept sandbox: $TEST_PATH (logs in $TEST_PATH/log)${RESET}"
		else
			rm -rf "$TEST_PATH"
		fi
	fi
	unset 'TEST_CLEAN[@]'
}

# Function: auto-sandbox
# Creates the project in `$TEST_PATH/project`, as a git repository configured
# for the selected engine. Logs live next to it, out of the agent's reach.
function auto-sandbox {
	SANDBOX="$TEST_PATH/project"
	mkdir -p "$SANDBOX/.auto" "$TEST_PATH/log"
	git -C "$SANDBOX" init -q
	{
		echo "[runtime]"
		echo "engine = \"$GOLD_ENGINE\""
		[ -z "$GOLD_PROVIDER" ] || echo "provider = \"$GOLD_PROVIDER\""
		[ -z "$GOLD_MODEL" ] || echo "model = \"$GOLD_MODEL\""
	} >"$SANDBOX/.auto/config.toml"
	test_log "${DIM}Sandbox: $SANDBOX ($GOLD_ENGINE ${GOLD_PROVIDER:+$GOLD_PROVIDER/}$GOLD_MODEL)${RESET}"
}

# Function: auto-step NAME COMMAND…
# Runs COMMAND in the sandbox with a timeout, `auto` standing for this
# checkout's CLI, and records it under `log/NN-NAME.{cmd,out,err,status}`.
function auto-step {
	local name="$1" status=0
	shift
	[ "$1" != auto ] || set -- "$AUTO" "${@:2}"
	AUTO_STEP=$((AUTO_STEP + 1))
	LAST="$TEST_PATH/log/$(printf %02d "$AUTO_STEP")-$name"
	printf '%q ' "$@" >"$LAST.cmd"
	(cd "$SANDBOX" && timeout "$GOLD_TIMEOUT" "$@" </dev/null) >"$LAST.out" 2>"$LAST.err" || status=$?
	echo "$status" >"$LAST.status"
	LAST_OUT="$LAST.out"
	LAST_STATUS="$status"
	test_log_message "$(basename "$LAST") → $status"
}

# Function: auto-tail [FILE]
# The end of a log file, flattened for a failure message. Defaults to the
# latest step's stderr, or its stdout (JSON failures) when stderr is empty.
function auto-tail {
	local file="${1:-$LAST.err}"
	[ -n "${1:-}" ] || [ -s "$file" ] || file="$LAST.out"
	tail -c 600 "$file" 2>/dev/null | tr '\n' ' '
}

# Function: auto-expect-status CODE…
# Passes when the latest step exited with one of CODEs.
function auto-expect-status {
	if [[ " $* " == *" $LAST_STATUS "* ]]; then
		test-ok "$(basename "$LAST") exited $LAST_STATUS"
		return 0
	fi
	test-fail "$(basename "$LAST") exited $LAST_STATUS, expected $*: $(auto-tail)"
	return 1
}

# Function: auto-assert DESCRIPTION COMMAND…
# Passes when COMMAND succeeds.
function auto-assert {
	local description="$1"
	shift
	if "$@" >/dev/null 2>&1; then
		test-ok "$description"
	else
		test-fail "$description"
		return 1
	fi
}

# Function: auto-warn DESCRIPTION COMMAND…
# Reports, without failing the test, when COMMAND fails. For model behaviour
# worth watching but too variable to require.
function auto-warn {
	local description="$1"
	shift
	if "$@" >/dev/null 2>&1; then
		test-ok "$description"
	else
		test_step_reveal
		test_log "${YELLOW}WARN  $description${RESET}"
	fi
}

# Function: auto-json FILTER [FILE]
# Queries a JSON (or JSON lines) output, by default the latest step's.
function auto-json {
	jq -r "$1" "${2:-$LAST_OUT}"
}

# Function: auto-yaml TEXT
# Quotes TEXT as a YAML string for value arguments (eg. `gap decide` answers).
function auto-yaml {
	jq -n --arg value "$1" '$value'
}

# Function: auto-plan-status
# Saves `plan status` JSON to `$STATUS`.
function auto-plan-status {
	auto-step status auto status --plan "$PLAN" --format json
	STATUS="$LAST_OUT"
	[ "$LAST_STATUS" = 0 ] || test-fail "plan status exited $LAST_STATUS: $(auto-tail)"
}

# Function: auto-plan-events
# Saves all plan events (JSON lines) to `$EVENTS`.
function auto-plan-events {
	auto-step events auto log --plan "$PLAN" --since 0 --format json
	EVENTS="$LAST_OUT"
	[ "$LAST_STATUS" = 0 ] || test-fail "plan events exited $LAST_STATUS: $(auto-tail)"
}

# Function: auto-events-count KIND
function auto-events-count {
	jq -s --arg kind "$1" '[.[] | select(.kind == $kind)] | length' "$EVENTS"
}

# Variable: GOLD_GAPS[]
# Ids of the gaps answered by `auto-answer-gaps`.
declare -a GOLD_GAPS=()
# Variable: GOLD_DECLINED[]
# Ids of the runtime policy approvals declined by `auto-answer-gaps`.
declare -a GOLD_DECLINED=()

# Function: gold-answer GAP_JSON
# Prints the answer to an open gap. Scenarios override it, falling back to
# `gold-answer-default`.
function gold-answer {
	gold-answer-default "$1"
}

# Function: gold-answer-default GAP_JSON
# Picks the first option, or defers to the performer.
function gold-answer-default {
	local first
	first="$(jq -r '(.options // [])[0] // empty | if type == "string" then . else (.label // .value // tostring) end' <<<"$1")"
	echo "${first:-Use your best judgement.}"
}

# Function: auto-answer-gaps
# Decides every open gap with `gold-answer`. Fails on approval gaps, which
# effect grants given at plan creation should have prevented.
function auto-answer-gaps {
	local gaps count index gap id kind question
	auto-step gaps auto x gap list --plan "$PLAN" --format json
	[ "$LAST_STATUS" = 0 ] || {
		test-fail "gap list exited $LAST_STATUS: $(auto-tail)"
		return 1
	}
	gaps="$(jq -c '[.[] | select(.status == "Open")]' "$LAST_OUT")"
	count="$(jq length <<<"$gaps")"
	if [ "$count" = 0 ]; then
		test-fail "plan is pending without open gaps: $(auto-tail "$LAST.out")"
		return 1
	fi
	for ((index = 0; index < count; index++)); do
		gap="$(jq -c ".[$index]" <<<"$gaps")"
		id="$(jq -r .id <<<"$gap")"
		kind="$(jq -r .kind <<<"$gap")"
		question="$(jq -r .question <<<"$gap")"
		if [ "$kind" = approval ]; then
			# Ambient engine tools (eg. the user's OpenCode skills) are declined like a
			# human would: the run resumes and the model continues without them.
			if jq -e --arg pattern "$GOLD_DECLINE" '(.runtime_policy.effect // "") | test($pattern)' <<<"$gap" >/dev/null; then
				test_log_message "declining $id: $question"
				auto-step decline auto x gap decide "$id" "$(auto-yaml reject)" --rationale "Not needed for this test" --plan "$PLAN" --format json
				auto-expect-status 0 || return 1
				GOLD_DECLINED+=("$id")
				continue
			fi
			test-fail "unexpected approval gap $id: $question"
			return 1
		fi
		test_log_message "gap $id ($kind): $question"
		auto-step decide auto x gap decide "$id" "$(auto-yaml "$(gold-answer "$gap")")" --rationale "Scripted gold-test answer" --plan "$PLAN" --format json
		auto-expect-status 0 || return 1
		GOLD_GAPS+=("$id")
	done
}

# Function: auto-continue
# Continues the plan until it ends, deciding gaps whenever it stops on them.
function auto-continue {
	local round
	for ((round = 1; round <= GOLD_MAX_ROUNDS; round++)); do
		auto-step continue auto x plan continue --plan "$PLAN" --format json --no-input
		case "$LAST_STATUS" in
		0)
			test-ok "plan ended after $round continuation(s)"
			return 0
			;;
		3) auto-answer-gaps || return 1 ;;
		*)
			test-fail "plan continue exited $LAST_STATUS: $(auto-tail)"
			return 1
			;;
		esac
	done
	test-fail "plan did not end within $GOLD_MAX_ROUNDS continuations"
	return 1
}

# Function: auto-task-outputs
# Prints the stored output of every run of the plan, separated by run headers.
function auto-task-outputs {
	local run
	for run in $(jq -r '.taskDetails[].run.id // empty' "$STATUS"); do
		echo "=== $run"
		(cd "$SANDBOX" && "$AUTO" run output "$run" --plan "$PLAN" --format json 2>/dev/null) || true
	done
}

# EOF
