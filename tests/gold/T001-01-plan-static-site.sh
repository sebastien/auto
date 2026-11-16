#!/usr/bin/env bash
# Gold: plan and build a static website end to end, covering script runs,
# plan creation with elaboration, gaps and decisions, catalogue reuse, task
# execution, refinement (GOLD_REFINE=1) and usage accounting.
# shellcheck disable=SC2016 # jq programs use $variables
set -euo pipefail
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib-auto.sh"
# Assertions record failures and carry on, so one run reports every outcome;
# steps that later ones depend on stop the test explicitly with test-abort.
set +e
trap - ERR

FIXTURE="$GOLD_DIR/fixtures/static-site"
# An unusual colour, so finding it in the stylesheet proves the decision reached it
GOLD_ACCENT="${GOLD_ACCENT:-#3a7d44}"
GOLD_BUDGET="${GOLD_BUDGET:-{cost: 3, time: 3600\}}"
COLOUR='colou?r|accent|brand|palette|hex'
CHECK_SITE='catalogue/task/check-site'

# Answers colour gaps with the accent colour, others with the default.
function gold-answer {
	if jq -e --arg pattern "$COLOUR" '((.question // "") + " " + ((.options // []) | tostring)) | test($pattern; "i")' <<<"$1" >/dev/null; then
		echo "$GOLD_ACCENT"
	else
		gold-answer-default "$1"
	fi
}

test-start "T001-01 Plan a static site"

# -----------------------------------------------------------------------------
test-step "Preflight: tools, sandbox, configuration and catalogue"
auto-assert "jq is available" command -v jq
auto-assert "$GOLD_ENGINE is available" command -v "$GOLD_ENGINE"
# Gaps and decisions are Memo records: without it, plans fail after a paid elaboration
auto-assert "Memo is installed in Auto's environment" "$AUTO_ROOT/.venv/bin/python" -c "import memo" ||
	test-abort "install Memo first: uv sync, with sebastien/memo checked out in ../memo (see README)"
auto-sandbox
cp -R "$FIXTURE/tools" "$SANDBOX/"
mkdir -p "$SANDBOX/.auto/catalogue"
cp -R "$FIXTURE/catalogue/." "$SANDBOX/.auto/catalogue/"
git -C "$SANDBOX" add -A
git -C "$SANDBOX" -c user.name=gold -c user.email=gold@example.invalid commit -q -m "Gold fixture"
auto-step config auto x config
auto-expect-status 0
auto-assert "configuration selects $GOLD_ENGINE" grep -q "runtime.engine = \"$GOLD_ENGINE\"" "$LAST_OUT"
auto-step catalogue auto x catalogue list
auto-expect-status 0
auto-assert "the project catalogue provides check-site" \
	jq -e --arg ref "auto:$CHECK_SITE" 'any(.[]; .identity == $ref and .layer == "project")' "$LAST_OUT"

# -----------------------------------------------------------------------------
test-step "Script: a standalone script runs on the engine"
auto-step script auto run "$FIXTURE/hello.md" --format json --no-input
auto-expect-status 0 || test-abort "the engine cannot run a script, see $LAST.*"
auto-assert "the script output greets" grep -qi "HELLO gold test" "$LAST_OUT"

# -----------------------------------------------------------------------------
test-step "Create: the plan is elaborated from the goal"
auto-step create auto x plan create "$(<"$FIXTURE/goal.md")" \
	--effects '[repo.read, repo.write, shell.run]' --budget "$GOLD_BUDGET" --agents '[]' \
	--format json --no-input
auto-expect-status 0 3 || test-abort "plan creation failed"
CREATED="$LAST"
PLAN="$(basename "$(auto-json .plan)")"
auto-assert "plan id is NNN-slug" grep -Eq '^[0-9a-f]{3,}-[a-z0-9-]+$' <<<"$PLAN"
auto-step list auto plans --format json
auto-assert "plan list includes $PLAN" jq -e --arg plan "$PLAN" 'any(.[]; .id == $plan)' "$LAST_OUT"

# -----------------------------------------------------------------------------
test-step "Decision: a decision made outside a gap is recorded"
auto-step decision auto decide "Which language and locale should the site content use?" \
	"$(auto-yaml 'British English, with <html lang="en-GB">')" --rationale "The bakery serves a UK neighbourhood" \
	--concerns '[content]' --source 'gold:T001-01' --plan "$PLAN" --format json --no-input
auto-expect-status 0
DECISION="$(auto-json .id)"

# -----------------------------------------------------------------------------
test-step "Execution: the plan runs to its end, gaps decided as they come"
if [ "$(cat "$CREATED.status")" = 3 ]; then
	auto-answer-gaps || test-abort "could not decide the gaps raised by elaboration"
fi
auto-continue || test-abort "the plan did not complete"
auto-plan-status
auto-assert "plan succeeded" jq -e '.status == "Succeeded"' "$STATUS"
auto-assert "every task succeeded" jq -e '[.tasks[]] | all(. == "Succeeded")' "$STATUS"
# A coarse breakdown (the default) may be as small as a build and a check
auto-assert "elaboration produced at least two tasks" \
	jq -e '[.taskDetails | keys[] | select(. != "root")] | length >= 2' "$STATUS"
for run in $(jq -r '.taskDetails[].run.id // empty' "$STATUS"); do
	auto-step run auto x run get "$run" --plan "$PLAN" --format json
	auto-expect-status 0
done

# -----------------------------------------------------------------------------
test-step "Gaps: gaps were raised, decided and journalled"
auto-plan-events
auto-step gaps auto x gap list --plan "$PLAN" --format json
GAPS="$LAST_OUT"
auto-assert "at least one gap was raised" test "$(auto-events-count GapRaised)" -ge 1
auto-assert "a gap asked for the accent colour" \
	jq -e --arg pattern "$COLOUR" 'any(.[]; (.question // "") | test($pattern; "i"))' "$GAPS"
auto-assert "no gap remains open" jq -e 'all(.[]; .status != "Open")' "$GAPS"
for gap in "${GOLD_GAPS[@]}"; do
	auto-assert "gap $gap is resolved with a decision" \
		jq -e --arg id "$gap" 'any(.[]; .id == $id and .status == "Resolved" and .decision != null)' "$GAPS"
	auto-assert "gap $gap resolution is in the events" \
		jq -se --arg id "$gap" 'any(.[]; .kind == "GapResolved" and .data.gap == $id)' "$EVENTS"
done
auto-assert "the recorded decision is in the events" \
	jq -se --arg id "$DECISION" 'any(.[]; .kind == "DecisionRecorded" and .data.decision == $id)' "$EVENTS"

# -----------------------------------------------------------------------------
test-step "Catalogue: the project's check-site template was reused"
# Its `post` runs the site check, so a succeeded run also means the check passed
auto-assert "a task instantiates check-site" \
	jq -e --arg ref "$CHECK_SITE" 'any(.taskDetails[]; (.from // "") | contains($ref))' "$STATUS"
auto-assert "the check-site tasks succeeded" jq -e --arg ref "$CHECK_SITE" \
	'. as $s | [$s.taskDetails | to_entries[] | select((.value.from // "") | contains($ref)) | .key]
	| length > 0 and all(.[]; $s.tasks[.] == "Succeeded")' "$STATUS"

# -----------------------------------------------------------------------------
test-step "Product: the website passes the check and follows the decisions"
# The fixture's copy, as the agent may have edited the sandbox's
auto-step check-site sh "$FIXTURE/tools/check-site.sh" site
auto-expect-status 0
auto-assert "style.css uses the accent colour $GOLD_ACCENT" grep -qiF -- "$GOLD_ACCENT" "$SANDBOX/site/style.css"
auto-warn "pages follow the recorded locale decision (lang=\"en-GB\")" \
	grep -qi 'lang="en-GB"' "$SANDBOX/site/index.html"

# -----------------------------------------------------------------------------
if [ "$GOLD_REFINE" = 1 ]; then
	test-step "Refine: a new page is added to the completed plan"
	REVISIONS="$(auto-events-count PlanRevised)"
	auto-step refine auto change "Add an about page, site/about.html, telling the story of the bakery, linked from the navigation of every page, then check the site again." \
		--plan "$PLAN" --format json --no-input
	auto-expect-status 0 3 || test-abort "refinement failed"
	if [ "$LAST_STATUS" = 3 ]; then
		auto-answer-gaps || test-abort "could not decide the gaps raised by refinement"
	fi
	auto-continue || test-abort "the refined plan did not complete"
	auto-plan-events
	auto-assert "refinement added a revision" test "$(auto-events-count PlanRevised)" -gt "$REVISIONS"
	auto-plan-status
	auto-assert "refined plan succeeded" jq -e '.status == "Succeeded"' "$STATUS"
	auto-step check-site-about sh "$FIXTURE/tools/check-site.sh" site index services contact about
	auto-expect-status 0
	auto-assert "style.css still uses the accent colour" grep -qiF -- "$GOLD_ACCENT" "$SANDBOX/site/style.css"
fi

# -----------------------------------------------------------------------------
test-step "Usage: engine runs are accounted to the plan"
auto-step usage auto x usage --by plan --format json
auto-expect-status 0
auto-assert "usage has runs for $PLAN" \
	jq -e --arg plan "$PLAN" 'any(.[]; ((.plan // "") | tostring | contains($plan)) and .runs > 0)' "$LAST_OUT"
USAGE="$(jq -c --arg plan "$PLAN" '[.[] | select((.plan // "") | tostring | contains($plan))] | {runs: (map(.runs) | add), tokens: (map(.tokens // 0) | add), cost: (map(.cost // 0) | add)}' "$LAST_OUT")"
test_log "${DIM}Usage: $USAGE${RESET}"
auto-warn "cost stays under GOLD_COST_WARN=$GOLD_COST_WARN" \
	jq -e --argjson limit "$GOLD_COST_WARN" '.cost <= $limit' <<<"$USAGE"

test-end
