# nocturne-memory

`nocturne-memory` is NOCTURNE's Memory Palace service and migration package. It
is released in lockstep as a dependency of the public `nocturne-harness`
distribution.

The service owns the extraction consent boundary at `/v1/extractions` and
`/v1/approval-queue`. Pending units use the internal `candidate` lifecycle
state and are excluded from ordinary memory listing, search, and injection.
Queue decisions are append-only; approval enacts the proposed verdict through
revisioned head changes and typed lineage edges, while denial revision-
tombstones the candidate as rejected.

Injection treats memory room as a versioned share of the active model context
window. The share is a ceiling, not a quota: unused room remains available to
conversation, while pinned memories always inject and report any overflow in
the prepare allocation. Share and threshold join the ordinary replay learner
after 100 authentic, hygiene-filtered owner dispositions; every learned
generation remains proposed until the owner activates it.

New users should start at the [NOCTURNE quickstart](https://github.com/Nate0-1999/nocturne)
and install `nocturne-harness`; they do not need to install this package separately.
