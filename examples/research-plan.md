# Choosing a local search tool

> Synthetic example. The questions and evaluation plan were written for this demo.

## The decision

Choose a search tool for a small personal document collection. The decision is
about finding useful passages, keeping the data local, and making failures easy
to inspect.

## Evaluation plan

Compare the tools on a fixed set of questions. Record answer quality and response
time for every question, including failures. A polished demo is not enough to
establish that the tool works across the collection.

## What counts as success?

The evaluation includes exact-name lookups, paraphrased questions, ambiguous
queries, and questions whose answer is absent. Report each group separately.
Keep the source snippets so the human can inspect why a result was judged useful.

## Open questions

How should we select questions without favoring a tool we already like? What is
the right behavior when the collection contains no answer?

## Acceptance

The human reviews the results and chooses whether to adopt a tool. Completing the
test run does not automatically approve the recommendation.
