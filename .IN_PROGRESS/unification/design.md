# Unification: declaring two rows the same thing

Status: design note, nothing built.
Written while moving Tide Table's calendar into one SQLite file in git (see `packages/tide-table/DIARY.md`, 2026-11-19).

## The problem

Small Sea data gets a random UUIDv7 per object, because content alone cannot reliably tell whether two objects are the same thing.
The cost of random IDs is that the same thing can end up with two IDs:
- Alice and Bob each add the same emailed invitation to the team calendar. Each client creates an event, and both carry the invitation's UID.
- Alice and Bob each type in the same meeting by hand. The two events have different UIDs, and only a person can tell they are one meeting.
- In the Manager: two registrations of the same app, a renamed app, or one person who turns out to have two participant IDs.

Unification records that two IDs name one thing, so every device can treat them as one.
The Manager's spec already expects this (`participant_unification`, `teammate_unification`, and a sketched `app_unification`), but nothing is built.
Tide Table would be the first real user.

## The record

A unification is a pair of IDs in a table such as:

```sql
CREATE TABLE event_unification (
    id_low  BLOB NOT NULL,   -- the smaller of the two IDs
    id_high BLOB NOT NULL,   -- the larger
    PRIMARY KEY (id_low, id_high)
);
```

Rules:
- **Rows are only ever added.** Two teammates' tables combine by taking every row from both, which Splice Merge already does without conflict.
- **The survivor is the smallest ID among everything unified together.** Pairs chain: if Alice unifies A with B and Bob unifies B with C, then A, B and C are one thing, and A survives.
  Because the rule is fixed, teammates who unify concurrently agree on the survivor without talking to each other, and no set of pairs can form a cycle of winners.
- **The pair carries nothing else.** If the row also held a timestamp or a reason, Alice and Bob recording the same pair would produce two different rows under one key, which Splice Merge reports as a conflict.
  Who decided and why belongs in the commit that adds the row.

The Manager spec's sketch has an explicit `winner_app_id` column.
That mixes two choices that are better kept apart:
which ID survives (the fixed rule above), and which content survives, such as which friendly name a renamed app keeps.
The second is a choice a person makes when unifying, and it shows up in the surviving row's content, not in the pair.

## Unifying

To unify B into A on one device, in one commit:
1. Combine B's content into A.
2. Delete B's row.
3. Add the pair.

Step 1 has no shared base version: A and B were created separately.
For events, every field where A and B differ needs a choice; "keep A's", "keep B's" or a field-by-field pick from a person are all reasonable.
B's last version stays in git history, so the unification can be inspected later.

## Merging with unification

The merge driver collects the pairs from all three versions (base, ours and theirs).
The table only grows, so the union of the three is always correct.

**A deletion that is part of a unification is not a teammate's delete.**
If B's row is gone and B is unified into A, the driver does not treat B's disappearance as a delete.
Without this rule, a unification would collide with any edit to B on the other side.

**An edit to a unified-away row is moved to the survivor.**
Suppose Alice unified B into A while Bob, not yet synced, changed B's location.
Bob's side changed B from version B₀ to B₁.
The driver applies that change to A as a three-way merge:

```
merge_event(base = B₀, ours = A, theirs = B₁)
```

Fields Bob changed where A still matches B₀ take Bob's value.
A field Bob changed where the unification chose a different value for A is a real conflict.
The same translation works whichever side runs the merge, and it covers chains: Bob merging C into B is an edit to B, which moves on to A.

`merge_event` currently refuses versions whose UIDs differ.
For a unified pair it would accept them, since that is the point of unifying.

**Two teammates unifying the same pair can disagree without a visible edit.**
Say A's location is Room 1 and B's is Room 2, and Alice and Bob each unify the pair.
Alice keeps Room 1, so A's location is unchanged on her side; Bob picks Room 2, so A's location changed on his.
Both add the same pair and delete B.
An ordinary three-way merge of A sees only Bob editing the location, takes Room 2, and nobody learns they disagreed.
The surviving ID is not the issue (both agree it is A); the issue is that keeping a value looks the same as never touching it.

Proposed rule: when both sides added the same pair since the base, the fields where A and B differed are choices both sides made.
The driver compares those fields directly between ours and theirs, and reports a conflict where they differ.
Fields where A and B agreed merge three-way as usual.
The alternative is to accept that the side that changed something wins, which is simpler but loses Alice's choice silently.

**A late delete of a unified-away row is ambiguous.**
Bob deleting B may mean "this meeting is cancelled", which should delete A, or it may be cleanup that no longer matters.
Candidate rules:
- Delete A only if A still matches B's last version; otherwise report a conflict.
- Always report a conflict, and let a person decide.

**References to a unified-away ID.**
Tide Table events have no rows pointing at them yet; the Manager's tables do (a berth points at an app, a role at a teammate).
Two ways to handle them:
- The driver rewrites references to the survivor's ID during the merge.
- Readers look IDs up through the unification table every time.

The first keeps reading simple and puts the work in one place; the second never edits the referring rows.

## Who decides to unify

Unification lets a teammate make your event disappear into theirs.
Teammates can already delete each other's events, so this is not a new power, and history keeps B's last version either way.

- **Same UID, different IDs**, as with a shared invitation: Tide Table could unify automatically during a merge.
  The alternative is to report a conflict and ask a person.
  Automatic unification trusts that a client's UID means what it should; a client with poorly chosen UIDs would merge unrelated events.
- **Different UIDs**: only a person can decide, as the Manager spec already requires for apps with the same name.

Until automatic unification exists, the merge driver reports two rows with one UID as a conflict, so nothing is silently combined or duplicated.

**"Which row is this event" is a disagreement that must survive merging.**
Two rows with one UID are not two separate problems, one per row; they are one question about which row stands for the event.
A review found what happens when the driver forgets that.
Alice and Bob each kept their own copy of an invitation after the duplicate was reported.
When their histories crossed, git combined the two candidate bases into one holding both copies, and against that base each side seemed to have deleted the other's copy.
The merge succeeded and deleted both.
The driver now marks both rows as having no agreed base, the same treatment as two versions of one row, so the next merge reports the disagreement until both sides hold the same copy.

Unification is the way to settle that question instead of reporting it.
Whatever form it takes, it has to work with combined bases: a pair recorded on only one side, or two sides that each kept a different row, must still reach a person or the fixed survivor rule, never a silent delete of both.

## What CalDAV clients see

When B is unified into A, a client sees B's event deleted and A's event changed, through the usual sync-token change list.
A client that later PUTs to B's old name creates a new event, unless the server maps that name to A.
Whether it should is an open question; clients keep stale names for a while after a sync.

## Undoing a unification

Rows are only added, so a unification cannot be removed by deleting its pair: the next merge would bring the pair back from a teammate.
Undoing would need a second record, such as a "separate these" row that outranks the pair, plus a way to recreate B from history.
Deferred until someone needs it.

## Where it lives

The same mechanism appears in Tide Table (events) and in the Manager (apps, participants, teammates).
The parts that do not depend on the data, namely collecting pairs, finding survivors, telling unification deletes from real deletes, and rewriting references, could live in Splice Merge.
The part that depends on the data, which is combining two versions such as `merge_event` for events, stays with the app.

Building it in Tide Table first and moving the general parts into Splice Merge when the Manager needs them avoids designing the library interface before there are two users.

## Open questions

- When two teammates unify the same pair with different choices, compare the chosen fields directly (proposed), or let the side that changed something win?
- Should the same UID unify automatically, or always ask?
- Which rule for a late delete of a unified-away row?
- Should the CalDAV server map a unified-away name to its survivor?
- Rewrite references at merge time, or resolve them when reading?
- Is the smallest ID a good survivor? It is the oldest for UUIDv7, which is a reasonable default, but any fixed rule works.
