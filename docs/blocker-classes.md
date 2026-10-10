# Blocker classes — what a blocked card's reason means, and what happens to the card

A build agent that cannot build its card writes a note, and the workflow posts
it on the card as a `🛑 Agent blocked:` comment and parks the card in Backlog.
Before DRE-6438 the sweep then held the card until a person replied, whatever
the note said. Most notes say one of a few mechanical things — there is nothing
to change, the card went to the wrong repository, the work is on a branch with
no pull request — and none of those needs a person to read prose to resolve.

Now every reason has a class. `config/blocker-classes.json` declares the
classes, and `scripts/blocker_class.py` is the one reader that names a
reason's class. The poster (DRE-6444) writes the class on the marker, and the
sweep (DRE-6448) hands the card to the class's action module through the
resolver (DRE-6508). This page is the version of that file a person reads. If
the two disagree, the file is right.

`python3 scripts/blocker_class.py check` holds the file to the contract: every
class says what it means, every class but the default names at least one
phrase, no phrase sits under two classes, the default is a class, and every
action is a `[a-z_]+` module name.

## The classes

| Class | Means | Resolved by | What it does to the card |
| -- | -- | -- | -- |
| `nothing-to-change` | Every acceptance criterion is already met on the default branch, so there is no change to open a pull request for. | `blocker_nothing_to_change` (DRE-6458) | Cancels a card whose every criterion the agent attested, one `- [x]` line each, and sends the rest to the planner. |
| `wrong-repo` | The card's files live in a different repository from the one its repo label dispatched the run into. | `blocker_wrong_repo` (DRE-6446) | Resolves by the repo label: spent when the label has changed since the note, swapped when the note names the one repository that holds the files. |
| `branch-without-pr` | The work is built and pushed on the card's branch, but the run opened no pull request for it. | `blocker_branch_pr` (DRE-6447) | Opens, or finds, the pull request for the work on the branch. A note the agent stamped gets a ready pull request. A legacy note read by its wording gets a draft, and whether the held work ships is asked in Green Light. |
| `question` | The reason needs a person's answer before the card can move. | `blocker_ask` (DRE-6459) | Asks once in Green Light with the three lines: Finding, Question and Recommendation. |

`question` is the default. Every class names an action module, `question`
included, because a question is resolved by asking it.

### Two facts about the real notes

The four real markers on the board when this vocabulary was written are copied
verbatim into `tests/fixtures/blocker-reasons.json`, and the suite proves each
one classifies as the table says.

- **DRE-5195's note carries no checked list line.** It was written before the
  stamp existed, so it says there is nothing to change in prose and attests no
  criterion line by line. Its wording names `nothing-to-change`, and the action
  module still needs the attestation it does not have.
- **DRE-6056's run held its pull request back on purpose.** It was waiting on
  parity numbers from a live planning run before it would let the work ship. A
  note of the `branch-without-pr` class may be exactly that: work that is done
  and deliberately not offered. So a legacy note of that class, read by its
  wording rather than a stamp, is opened as a **draft**, and the decision
  whether the held work ships is asked in Green Light (DRE-6447). Only a note
  the agent stamped `branch-without-pr` says outright that the pull request is
  simply missing, and only that one gets a ready pull request.

## The stamp — the first line of the agent's note

The build agent writes the class on the FIRST line of `/tmp/agent-blocker.txt`
(DRE-6443):

```
blocker-class: <class>
```

A stamp naming a word that is not a class is read as no stamp, and the note is
classified by its wording. What follows the stamp depends on the class:

- **`wrong-repo`** — the second line is `repo: <slug>`, the repository that
  holds the files. The poster removes the stamp line and keeps the `repo:` line
  as the first line of the quoted reason, because the wrong-repo module reads it
  there.
- **`nothing-to-change`** — the lines after the stamp are one
  `- [x] <criterion> — <what on the default branch satisfies it>` per
  acceptance criterion of the card.

The poster calls two commands, so it never restates this grammar in shell:

```
python3 scripts/blocker_class.py classify /tmp/agent-blocker.txt   # the bare class
python3 scripts/blocker_class.py reason /tmp/agent-blocker.txt     # the note, stamp line removed
```

`classify` of a missing, unreadable or empty file prints `question`.

## How a reason is classified

1. A valid stamp on the first line wins.
2. Otherwise the phrases: each class's `phrases` are lower-case substrings,
   matched case-insensitively against the whole text.
3. A text whose phrases name more than one class is `question` — ambiguity is a
   person's call.
4. A text naming no class is `question`.

The reader never raises and always returns one of the four names.

## The marker — what the poster writes and the sweep reads

```
🛑 Agent blocked: class=<class> · <reason> — parked in Backlog until … Run: <url>
```

The prefix `🛑 Agent blocked:` is unchanged, so every reader of the old marker
still sees it. Then `class=<class>`, then `·`, then the reason, which may run to
several lines, then the poster's trailing clause with the run URL. The reason is
the text between the `·` and the LAST ` — parked in Backlog`, so a multi-line
reason survives whole. A legacy marker carries no `class=`; its reason runs from
the prefix to the same clause, and its class is read off its wording.

## Which blocker is open

A card's blocker is read newest comment first, the same walk the sweep has
always made: a comment carrying the resolver's receipt tag resolves it, a
comment that does not open with one of the pipeline's own machine prefixes is a
person's reply and resolves it, and the first `🛑 Agent blocked` marker met is
the open blocker. A machine comment without the tag — the sweep's own
`🧹 Auto-promoted …`, say — does not resolve it. The tag and the prefixes are
the caller's to hand in; the reader holds neither.

To read a card or the board:

```
python3 scripts/blocker_class.py classify-card DRE-<n>    # DRE-<n> class=<class>, or no blocker
REPO=<owner/repo> REPO_SLUG=<slug> python3 scripts/blocker_class.py board
```

`classify-card` reads the card's whole thread, because the marker may be older
than its fifty newest comments. `board` decides "open" exactly as the sweep
does, with the sweep's own predicate, and prints one
`DRE-<n> class=<class> · <the first 80 characters of the reason>` line per open
blocker on the repo's Backlog cards. It refuses to run without both `REPO` and
`REPO_SLUG`, because a missing `REPO_SLUG` would read another repo's board and
print nothing. Both commands only read.

## What the sweep prints without acting

The sweep sometimes names a blocker and does nothing to it this pass. Every one
of these lines is transient — the next pass reads the card again — and every
one names the blocker's class:

- **The class's action module is not on the checkout yet.** The vocabulary
  names the module; the card that builds it has not merged. The marker stays
  open until it does.
- **The action module raised `NotNow`.** A fact the module needs could not be
  read this pass, such as a pull request state GitHub would not answer or a
  Linear write that was refused. The resolver prints the class and the reason
  and leaves the marker open. A person's call is never `NotNow`: the module
  returns nothing, and the sweep asks the person.
- **The card could not be read live.** The board read named the card, and the
  live re-read before acting failed.
- **The card left Backlog, or was resolved, since the board was read.** Someone
  moved it or replied in between, so there is nothing left to act on.
- **The pass is not the full sweep.** A narrower pass, such as the merge path's
  read of one card's dependents, names the class and leaves the acting to the
  full sweep.
