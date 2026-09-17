<!-- graft:start -->
## Graft — repo context graph

This repo is indexed in `graft/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `graft map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `graft ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `graft grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `graft skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `graft callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `graft/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `graft ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `graft build` (deterministic,
no API key, $0).
<!-- graft:end -->

<!-- codex-memory:project:start -->
## Obsidian 项目记忆

本项目的长期背景位于 `D:\ObsidianVault\01Projects\02项目\hrbpilot`。开始工作前先用 `codex-memory context --repo D:\demo\hrbpilot --task "<任务摘要>" --json` 获取相关背景；完成重要且已验证的任务后，按全局规则沉淀结论。只允许工具更新受控区块，绝不写入敏感信息。
<!-- codex-memory:project:end -->

## Worktree 收尾

- **worktree 不遗留**：在 worktree 里做的任务，完成时必须删除该 worktree 及其分支。删除前二选一：合并进 `main`；或不合并时先把未提交改动提交到该分支，打 `archive/` tag 存档，再 `git worktree remove` + `git branch -D`。编排 subagent 的会话负责收尾它派生的 worktree。确需保留的（待用户裁决、有冲突待解）在最终回复里点名路径与原因，不得默默留下。
