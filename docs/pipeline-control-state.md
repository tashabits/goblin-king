# Durable delivery state

I added versioned repository registrations, destination reservations, desired release state,
build attempts, dispatch intents, release operations, provider cursors, authenticated receipts,
and a transition journal. These are additive tables in the existing SQLite database.

Admission commits the verified request, desired generation, build identity, dispatch intent,
and journal entry in one transaction. Scheduling uses an immutable submission receipt with a
stable job ID. Repeating a submission after acknowledgement loss returns the existing job;
reusing that ID with different input fails. Existing jobs without a submission receipt are
not adopted, and historical admission records are not dispatched during an upgrade.

Release actions use expected versions and idempotency keys. A release retains exclusive
ownership until completion or explicit recovery; elapsed time alone does not transfer it.
Explicit rebuilds allocate new UUIDs and preserve the previous attempt's evidence. A newer
desired revision supersedes older unstarted work.

I verified this change with 113 contract, admission, state, store, and scheduler-lease tests.
That result covers database behavior. The complete delivery acceptance report also requires
real execution, publication, deployment, recording, recovery, and browser evidence.
