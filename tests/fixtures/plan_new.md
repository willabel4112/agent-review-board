# Search index migration

## 1. Scope

Move the local search index to a new format. Keep the source documents unchanged.

## 2. Rollout configuration

```jsonc
// v2: reviewed candidate policy
{
  "reader": "stable",
  "writer": "candidate",
  "validation": "fixed_stratified_sample",
  "rollback": "keep_previous_index"
}
```

Compare the candidate's results with the stable reader before changing the default.

## Appendix: acceptance evidence

A fixed evaluation set includes short queries, ambiguous terms and absent answers.
Record the result for every query, including failures, before choosing a default.
