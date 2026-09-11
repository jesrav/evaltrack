"""The eval data model and the rules that act on it. It imports nothing from the
rest of evaltrack, and nothing outside the standard library but pydantic and
python-ulid.

evaltrack has two data models for a recorded eval. `EvalRound` is what the eval
produced, a flat list of attempts. `RunRecord` is what a repository stores, a
tree of tests, then cases, then attempts. A translator builds the first from
whatever the runner returned, and `projection` builds the second from the first:

    runner result --(translator)--> EvalRound --(projection)--> RunRecord
                                    flat                         tree

`RunRecord` is derived from the rounds, not a copy of them. `projection` builds
it, and nothing goes back the other way. On the way it:

- groups the attempts of a case under one key, and keeps one copy of the case's
  inputs, expected output and metadata
- derives an outcome for each attempt and for each case, and counts a case's
  passes

The models differ in what they accept. A round refuses a field it does not
declare, since what describes the round itself is not stored. Every stored model
accepts fields it does not declare, because a run written by a newer evaltrack
still has to load in an older one. The result and error records are stored as
well, so either accepts an undeclared field even inside a round that refuses one.
"""
