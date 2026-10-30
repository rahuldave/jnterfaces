# Plan for files that several profiles share

Status: built. An adversarial review of the first draft changed D1, D2, D5,
D6, D7 and D9, and added D12 to D14. Three reviews of the code changed D4,
D5, D7, D10, D12 and D13, and added D15.

## The problem

A profile can add lines to a file that the project and other profiles also
use. For example, the `python` profile writes `.venv/` in `.gitignore`, and
the `typescript` profile needs `node_modules/` in the same file. Today the
installer writes such a file only when the project has none. So the second
profile adds nothing, and git does not ignore `node_modules`. Also, `just
uninstall typescript` cannot take out the lines of the profile, because no
record says which lines are its lines.

The installer already records most of what it adds, under the name of the
part that added it:

| What is added | Where it goes | Recorded under its owner | Removal |
|---|---|---|---|
| The packages of a tool | `pyproject.toml`, `package.json` | Yes, `owned_packages` in `.agents/stack.json` | Exact |
| Hooks | `.claude/settings.json`, `.codex/hooks.json` | Yes, `merged_hooks` in the install record of the package | Exact |
| Skills, role recipes, starter files | Own files | Yes, the install record of the package | Exact, and an edited file stays |
| Generated recipe files | `.agents/just/*.just` | A hash for each file in `generated` | Made again |
| The base files of a profile | `.gitignore`, `.worktreedisposable` | No | None |
| The config files of a tool | `ruff.toml`, `tsconfig.json` | No | None |

The last two rows are the gap. This plan gives them owners and a record,
in the same way as the other rows.

## Terms

- **Owner.** The part of the stack that adds lines or files. An owner is a
  profile, such as `python`, or a tool of a profile, written as a pair, such
  as `python:lint`. The pairs are the pairs of the `tools` list in
  `.agents/stack.json`. They are not the keys of `owned_packages`, which name
  a dependency group. The installer itself is the owner `jnterfaces`.
- **Line file.** A file that is a list of lines, with `#` comments, where
  each line has a meaning of its own. Examples are `.gitignore`,
  `.worktreedisposable` and `.agents/.gitattributes`.
- **Whole file.** A file that the installer writes in full, such as
  `ruff.toml`. One or more owners can need the same whole file.
- **Block.** The lines of one owner in one line file, between a begin
  comment and an end comment.
- **Your part.** The lines of a line file that are outside every block.
- **Log.** The file `.agents/stack-log.jsonl`. It has one JSON object, an
  entry, on each line.
- **Claim.** What the log says about one line of one owner in one file.

## Decisions

### D1. A log of each change

The installer adds entries to `.agents/stack-log.jsonl` for each run that
changes a line file or a whole file. A run that changes nothing adds no
entry. The log only grows, and the project commits it with the other files.
`.agents/stack.json` stays the record of the current state. The log is the
history of the line files and the whole files.

Reason: the log records what the installer did in this project. A profile
can change after the install, so the profile package alone cannot tell which
lines a project got. A separate file keeps `.agents/stack.json` small.

An entry holds these keys:

| Key | What it holds |
|---|---|
| `id` | The time of the run and four random hex digits, for example `2026-10-30T14:05:12Z-a1b2` |
| `status` | `started` before the run writes a file, then `finished` in a second entry with the same `id` |
| `command` | The command as the user typed it, for example `just install --profile python` |
| `sources` | The commit of each profile package that the run used |
| `lines` | For each file and owner, the lines `added`, `present`, `declined` and `released` |
| `files` | For each whole file, its owners and the hash of what the run wrote, or that the run removed it |

The installer reads the entries in the order of the file. The log is a list
of changes, not a chain, so an entry does not name the entry before it.

**Merges.** Two worktrees can each add entries. Git would mark the two new
lines at the end of the file as a conflict. So the installer writes the
line `stack-log.jsonl merge=union` in `.agents/.gitattributes`, which is a
line file of the owner `jnterfaces`. A union merge keeps the lines of both
sides, and the installer reads them in the merged order.

### D2. One block for each owner, with a precise comment

The lines of each owner go in a block of their own. The begin comment names
the owner, the package and its commit, the file, the command and the entry
of the log. The end comment names the command that removes the block. For
example:

```gitignore
# begin lines of python:lint, the lint tool of the python profile, from jnprofile-python at c299beb.
# Last written to .gitignore by `just install lint`, entry 2026-10-30T14:05:12Z-a1b2 of .agents/stack-log.jsonl.
.ruff_cache/
# end lines of python:lint. `just uninstall lint` removes them.
```

Reason: a person who reads the file can find the entry in the log with a
search for its `id`. The comment says which command removes the lines. Each
run that changes a block writes its own command and `id` in the comment.
Two branches that change the same block give a git conflict in that file,
in the same way as two edits of one line.

### D3. Blocks go at the start of the file

The blocks go before your part. A new file holds the blocks only. In a git
ignore file, a later line wins over an earlier line. So a line in your part,
for example a `!` line, always wins over a line of a block.

Reason: the first draft put the blocks at the end and tried to find a block
line that undoes one of your `!` lines. Only the matcher of git can find
such a line, with its wildcards and folder rules. With the blocks first, no
search is needed.

### D4. The installer works out the whole file at once

On each run, `just install` works out what each line file must hold, for all
owners at once, and then writes the file in one pass. It does so for each
line file that an active owner declares or that the log names. The steps
are these:

1. The installer reads the lines that each active owner declares. The
   active owners are the `jnterfaces` owner, each profile that stays on,
   and each tool in the `tools` list after the run.
2. A line that is in your part counts as `present` for each owner that
   needs it. The installer does not write it again.
3. A line that the log knows and that the file lacks stays out. You deleted
   it, so the installer records it as `declined` for each owner that needs
   it, and does not add it again. The same rule holds for a line of your
   part that an owner counted as `present`.
4. Each other line goes in the block of the first owner that needs it. The
   order of the owners is `jnterfaces`, then each profile in the order of
   `.agents/stack.json`, each followed by its tools.
5. A line in a block that no active owner needs goes out, if the log says
   that the owner of the block added it. If the owner of the block did not
   add a line in it, you added the line. This holds when another owner added
   the same line to its own block. It moves to the start of your part before the owners
   look for their lines, so an owner that needs it counts it as `present`.

The output names each line that a run records as `declined`, and says how
to have it back.

So a line that two owners need stays in the file while either owner is on.
When the first owner goes, the line moves to the block of the next owner.

### D5. Removal finds a line outside its block

A line that an owner added can be outside its block, because a person moved
it or deleted a comment. The installer looks for it in your part with the
loose match of D6. If it finds one copy, it removes that copy when the owner
goes. If it finds two or more copies, or no copy, it removes nothing and
the output names the line. An unsure claim of D12 never removes a line.

While the owner stays on, a line that it added and that is in your part
stays where it is, and keeps its claim `added`. But if the block still holds
the line when a copy appears in your part, you wrote the copy. The copy is
yours, the owner records it as `present`, and the block copy goes.

### D6. The loose match

Two lines are the same line when they are equal after these changes:

- remove the spaces and the carriage return at the start and the end;
- remove one `/` at the end.

A line that is the same in other ways, for example `/dist` and `dist/`, is a
possible match. The installer shows a possible match and does not remove it.
Git reads `/dist` and `dist/` differently, so only the user can decide.

### D7. Whole files have owners and a hash

A whole file, such as `ruff.toml`, goes in the log with its owners and the
hash of what the installer wrote:

- If the file is missing, an owner writes it and is its first owner.
- If the installer wrote the file earlier, a second owner joins the list.
  For example, `tsconfig.json` belongs to `typescript:typecheck` and to
  `typescript:test`.
- If you wrote the file, it is yours, and the installer writes nothing.
- If a `skip_if` rule leaves the file out, the output says so. A rule that
  starts to apply later does not remove a file that is there.
- If you delete the file, the log records it as `declined`, and the
  installer does not write it again. A file that you put back is yours.

When the last owner goes, the installer removes the file if its hash is the
same as the recorded hash. If you edited the file, it stays, and the output
says so.

### D8. Profiles declare their lines in profile.json

A profile gives its lines in a new key, `lines`, in its base and in each
tool. The key maps a file to a list of lines. A line that starts with `#`
is a comment. It goes in the block, and it is not a claim. For example:

```json
"lines": {
  ".gitignore": [".ruff_cache/"],
  ".worktreedisposable": [".ruff_cache/"]
}
```

The key `files` keeps its meaning. It maps a whole file to the file in the
profile package that it comes from. A path is either in `lines` or in `files`, not
in both. A path can be in a folder, for example `docs/.gitignore`.

A bare tool name names the tool in each active profile that has it. So
`just uninstall lint` removes `python:lint` and `typescript:lint`.

### D9. A newer profile

D4 runs on each install. So a newer commit of a profile changes the blocks
at the next `just install`:

- A new line goes in the block of the first owner that needs it.
- A line that the profile no longer has goes out, unless another owner
  needs it.
- A line that you deleted stays out, as D4 step 3 says.

### D10. Dry runs and the check

- **`--dry-run`** prints each line that the run would add, remove or move,
  and each whole file that it would write or remove. It writes nothing.
- **`just install --check`** reads the claims of the log and compares them
  with the line files and the whole files. It
  changes nothing. It prints each difference, for example a missing
  comment or an edited whole file. It also names a line that you added
  inside a block, and an entry that started and did not finish. It exits with 1 only for a
  block with a missing comment, an entry that did not finish, or a log that
  it cannot read. The other differences are notes.

The log wins when the log and a file disagree.

### D11. Projects that exist now

A project that has no log gets a first entry, with the `command`
`baseline`. A line that is in a line file before the first entry is in your
part. So it is `present` for each owner that needs it. A removal never takes
it out. A whole file that is there before the first entry is yours.

For example, in a project that the old `python` profile set up, `.venv/` is
yours. `just uninstall python` leaves it in `.gitignore`. Remove such lines
by hand.

### D12. A run that stops

The installer changes the line files and the whole files after the package
commands of the run succeed. Before it writes a file, it adds the entry with
`status` `started`, with all the claims of the run. After the last file, it
adds an entry with the same `id` and `status` `finished`.

If a run stops between the two entries, the claims of the started entry
are unsure. An unsure claim never declines a line or a file, because the run
may have stopped before it wrote them. The next run works out the files
again with D4, records each unsure claim again, and settles the entry that
stopped. If the next run skips a path that the stopped entry names, for
example because the path is now a link, the entry stays unsure. Until then,
`--check` names the entry that did not finish. The installer
writes each file through a file next to it and a rename, so a run that stops
leaves each file whole.

If a package command fails, the run stops before it changes a line file.
The tool stays in the record, so the next run tries again.

### D13. Text and links

- **Line endings.** The installer keeps the line ending of the file. A file
  with carriage returns keeps them.
- **The end of the file.** A file that does not end with a newline gets one
  before the installer adds a line.
- **Links.** The installer leaves a line file or a whole file alone when the
  file, or a folder on its path, is a symbolic link. The output says so. No
  write or removal goes through a link.
- **Byte-order mark.** git skips a byte-order mark only at the start of a
  file, so the mark stays at the start, before the blocks. `--check` skips
  the mark too.
- **Rows.** Only a newline ends a row. Each row of your part keeps its own
  line ending. New rows take the ending that most rows of the file have.
- **Comments.** A begin or end comment marks a block only when it names a
  known owner. A comment that lost its partner goes only when it has the
  exact form that the installer writes. Other comments of yours stay.

### D14. Owners outside the profiles

A role package, such as agent_gest_git_skills, runs its own installer, so
it cannot be an owner in this plan. A role that needs lines in a shared file
is a later change.

### D15. Paths that no owner may name

A path is a line file or a whole file, not both, across all owners. No owner
may name `.agents/stack-log.jsonl`, `.agents/stack.json` or a file in
`.agents/just/`, because the installer writes them. The installer compares
these paths without letter case, because macOS finds a file by any case. A line of a profile may
not be blank, and a profile may not give two forms of one line, such as
`.venv/` and `.venv`. Each path, in a profile or in the log, must stay
inside the project and out of `.git`, and may not hold a NUL character. A log entry that names another path
stops the run, because a branch or a merge could plant it.

## The build, in order

1. **The log.** Write and read `.agents/stack-log.jsonl`, the `id`, the two
   entries of D12, the first entry of D11, and `.agents/.gitattributes`.
2. **Line files.** Read the `lines` of `profile.json`, work out each file
   with D3 to D6, and write the blocks.
3. **Whole files.** Record, share and remove whole files with D7.
4. **Dry run and check.** Add the output of D10.
5. **The profiles.** Move the line files of jnprofile-python and
   jnprofile-typescript into `lines`, with each cache line on its tool.
   Remove `files/gitignore` and `files/worktreedisposable`.

Each repository gets an adversarial review before its commit.

## Tests

- Two profiles in one project, with a line that both need.
- `just uninstall` of each profile, with `--dry-run` first.
- `--profile typescript` in a project with `python`, so a shared line moves.
- A line that the file had before the install, and a project with no log.
- A line that you edited, deleted, moved out of a block, or added inside a
  block, and a comment that you deleted.
- A newer profile that adds a line and drops a line.
- `tsconfig.json` with two owners, an edited `ruff.toml`, and a `ruff.toml`
  that `skip_if` left out.
- A file with carriage returns, a file with no newline at the end, and a
  line file that is a link.
- A run that stops between the two entries.
- Two worktrees that each add an entry, then a merge with no conflict.

## Docs to write

- **jnterfaces `docs/stack-guide.md`.** A section on the log, the blocks,
  removal and the check, with an example of an entry. Change "How the
  mixins behave" for removal.
- **jnterfaces `README.md`.** The log, `--check`, and how to read a block
  comment.
- **jnprofile-python and jnprofile-typescript.** The `lines` of each tool in
  the `README.md` and the skill.
- **agent_gest_git_skills `README.md`.** A profile adds its lines to
  `.worktreedisposable` in a block.

## Not in this plan

- Files that are not lists of lines, such as `pyproject.toml` and
  `package.json`. The installer changes them with the command of the profile,
  `uv add` or `npm install`, and `owned_packages` records the packages.
- An undo command that replays the log backwards.
- Lines from role packages, as D14 says.
