# ADR-004: The Lambda artifact ships through S3, which costs money

- **Date:** 2026-08-24
- **Status:** accepted
- **Amends:** [ADR-002](002-tech-stack-hybrid.md), whose cost constraint was
  stated absolutely — "chi phí hạ tầng backend trung tâm: mục tiêu ≈ 0đ/tháng…
  ràng buộc cứng nhất của dự án". This is the first real exception to it, and
  it is recorded here rather than buried in a Terraform comment.

## Context

Phase 7 built the deployment package for the first time and measured it. The
numbers decide this decision on their own:

| Build | Unzipped | Zipped |
|---|---|---|
| Everything from `requirements.txt` | 234 MB | 77 MB |
| Trimmed (boto3/botocore excluded, tests removed, `.so` stripped) | **200 MB** | **62 MB** |

Both AWS limits matter and they pull in different directions:

- **250 MB unzipped** — a hard ceiling. 200 MB clears it with 20% headroom.
- **50 MB zipped** — the ceiling for a *direct* upload, which is what
  Terraform's `filename` argument does. 62 MB does not clear it.

The bulk is unavoidable: `scipy` 95 MB, `numpy` 57 MB, `sklearn` 35 MB. They
are what IsolationForest runs on. The trimming above is already aggressive; the
one further cut available — deleting `*.dist-info` — saves 3 MB and breaks
`importlib.metadata` for any dependency that reads its own version at runtime.
Three megabytes is not worth a failure mode that appears only in production,
and it would not get under 50 MB anyway.

Verified, not assumed: the trimmed package still imports
(`python3 -c "import services.backend.main"` succeeds against the built tree).

## Options considered

1. **Direct upload** — impossible at 62 MB. Not a choice.
2. **Shrink under 50 MB** — would mean dropping scikit-learn, i.e. reimplementing
   IsolationForest. Absurd scope for a packaging problem.
3. **Lambda container image via ECR** — raises the ceiling to 10 GB and ADR-002
   explicitly chose zip over containers. ECR's private-registry free tier is
   500 MB for *12 months*, so it is not free forever either; it swaps one
   small cost for another while contradicting a settled decision.
4. **S3 (chosen)** — the standard path for packages over 50 MB. Terraform
   uploads the object and both functions reference it by key.

## Decision

Ship the artifact through a dedicated S3 bucket. `aws_s3_object` uploads it as
part of the same apply that creates the functions, so there is no bootstrap
ordering problem. A 30-day non-current version expiry keeps storage bounded.

The bucket is **separate from the Terraform state bucket** on purpose: the
deploy role needs write access to artifacts, and it must not thereby gain write
access to state.

## Consequences

**The cost, stated plainly.** S3's free tier is 12-month only, so after that:
roughly 70 MB stored (~$0.0016/month) plus a handful of PUT and GET requests
per deploy. Call it **under one US cent per month at this scale**.

That is not zero. ADR-002's constraint said zero, absolutely and forever, and
this breaks it — by an amount that rounds to nothing, for a requirement that
has no free alternative. The publisher should know the constraint now has an
exception rather than discovering a bill line they were told could not exist.

**What this does not change.** Compute, database, scheduling, auth and ingress
all remain inside Always-Free. This is a build-artifact cost, not a per-request
one: it does not scale with traffic, tenants or attack volume.

**If the package ever drops under 50 MB** — a lighter ML dependency, a future
scikit-learn that vendors less — direct upload becomes possible again and this
ADR can be reversed. `scripts/build-lambda-package.sh` prints a note when the
zipped size crosses back under 50 MB, so nobody has to remember to check.
