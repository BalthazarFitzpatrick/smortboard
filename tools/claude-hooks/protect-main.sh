#!/bin/bash
# claude code PreToolUse hook for Bash: main belongs to a person, development belongs to the agents.
#
# allowed: branches, commits and pushes on anything but main/master, merging one branch into another,
# and merging a pull request whose base is `development`.
# refused: committing on main/master, pushing to them by any refspec, and merging any pull request
# whose base is not `development` - main/master above all, or a base github can't report.
# this catches agent mistakes; it is not a security boundary, since it only sees Bash tool calls.
# pair it with a github ruleset on main (see docs/protect-main.md).
set -u

DEV_BRANCH="${PROTECT_MAIN_DEV_BRANCH:-development}"

input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.tool_name // empty')" = "Bash" ] || exit 0
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')

# heredoc bodies are data, so a doc that quotes `git push origin main` doesn't trip the guard
cmd=$(printf '%s\n' "$cmd" | awk '
	$0 ~ /<<-?[[:space:]]*['"'"'"]?[A-Za-z_][A-Za-z0-9_]*['"'"'"]?/ && !inbody {
		body = $0
		sub(/.*<<-?[[:space:]]*/, "", body)
		gsub(/['"'"'"]/, "", body)
		sub(/[^A-Za-z0-9_].*$/, "", body)
		if (body != "") { marker = body; inbody = 1 }
		print; next
	}
	inbody { if ($0 == marker || $0 == "\t" marker) inbody = 0; next }
	{ print }
')

# only commands that write a branch are checked; options may sit between `git` and the subcommand
printf '%s' "$cmd" \
	| grep -qE '\bgit[[:space:]]+([^[:space:]]+[[:space:]]+){0,4}(commit|push|merge|rebase)([[:space:]]|$)' \
	|| printf '%s' "$cmd" | grep -qE '\bgh[[:space:]]+pr[[:space:]]+merge\b' \
	|| exit 0

# the repo being written is named by `git -C <path>` or a leading `cd <path> &&`, else the cwd
quoted='("[^"]+"|'"'"'[^'"'"']+'"'"'|[^[:space:]]+)'
dir=$(printf '%s' "$cmd" | sed -nE "s/.*git[[:space:]]+-C[[:space:]]+$quoted.*/\\1/p" | head -1)
[ -n "$dir" ] || dir=$(printf '%s' "$cmd" | sed -nE "s/^[[:space:]]*cd[[:space:]]+$quoted[[:space:]]*&&.*/\\1/p" | head -1)
dir=$(printf '%s' "$dir" | sed -E 's/^["'"'"']//; s/["'"'"']$//')
case "$dir" in "~") dir="$HOME" ;; "~/"*) dir="$HOME/${dir#\~/}" ;; esac
[ -n "$dir" ] || dir=.

strip_quotes() { printf '%s' "$1" | sed -E 's/^["'"'"']//; s/["'"'"']$//'; }

# a pull request merge writes its base, so the base is asked from github and must be development
if printf '%s' "$cmd" | grep -qE '\bgh[[:space:]]+pr[[:space:]]+merge\b'; then
	merge_seg=$(printf '%s' "$cmd" | sed 's/&&/\n/g; s/||/\n/g; s/;/\n/g' \
		| grep -E 'gh[[:space:]]+pr[[:space:]]+merge' | head -1 \
		| sed -E 's/.*gh[[:space:]]+pr[[:space:]]+merge//')
	selector=""
	repo_flag=""
	skip=""
	set -f
	for word in $merge_seg; do
		if [ -n "$skip" ]; then
			[ "$skip" = repo ] && repo_flag=$word
			skip=""
			continue
		fi
		case "$word" in
			-R|--repo) skip=repo ;;
			--repo=*) repo_flag=${word#--repo=} ;;
			# flags that take a value, so the value is never read as the pr selector
			-b|--body|-F|--body-file|-t|--subject|-A|--author-email|--match-head-commit) skip=value ;;
			-*) ;;
			*) [ -n "$selector" ] || selector=$word ;;
		esac
	done
	set +f
	selector=$(strip_quotes "$selector")
	repo_flag=$(strip_quotes "$repo_flag")
	base=$(cd "$dir" 2>/dev/null && gh pr view ${selector:+"$selector"} ${repo_flag:+-R "$repo_flag"} \
		--json baseRefName -q .baseRefName 2>/dev/null)
	case "$base" in
		"$DEV_BRANCH") exit 0 ;;
		"")
			echo "BLOCKED: could not read this pull request's base branch, so it is not merged." >&2 ;;
		main|master)
			echo "BLOCKED: this pull request merges into $base. Merging into $base is for a person, not an agent." >&2 ;;
		*)
			echo "BLOCKED: agents merge pull requests into $DEV_BRANCH only; this one targets $base." >&2 ;;
	esac
	exit 2
fi

branch=$(git -C "$dir" branch --show-current 2>/dev/null)
if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
	echo "BLOCKED: refusing to write to $branch. Work on a branch and merge it into $DEV_BRANCH." >&2
	exit 2
fi

# a push names its own target, so `git push origin HEAD:main` is caught from any branch
push_seg=$(printf '%s' "$cmd" | sed 's/&&/\n/g; s/||/\n/g; s/;/\n/g' \
	| grep -E '\bgit[[:space:]]+([^[:space:]]+[[:space:]]+){0,4}push([[:space:]]|$)' | head -1)
[ -n "$push_seg" ] || exit 0

set -f
for word in $push_seg; do
	case "$word" in -*) continue ;; esac
	dest=${word##*:}
	dest=${dest#+}
	case "$dest" in
		main|master|refs/heads/main|refs/heads/master)
			echo "BLOCKED: refusing to push to $dest. Open a pull request instead." >&2
			exit 2 ;;
	esac
done

exit 0
