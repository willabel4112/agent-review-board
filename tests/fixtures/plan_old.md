# Search index migration

## 1. Scope

Move the local search index to a new format. Keep the source documents unchanged.

## 2. Rollout configuration

```jsonc
// v1: initial candidate policy
{
  "reader": "stable",
  "writer": "candidate",
  "validation": "sample",
  "rollback": "keep_previous_index"
}
```

Compare the candidate's results with the stable reader before changing the default.

## 3. Pending decisions

Choose a validation sample later.
