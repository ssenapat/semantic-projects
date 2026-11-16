# Contributing

Thanks for your interest. Bug reports, questions, new examples and pull requests are all welcome.

## Reporting issues

Open a GitHub issue with:

- which project (`pagila-semantic` or `flight-ontime-nl-analytics`),
- the question you asked and what you expected,
- what happened instead (error text, or a screenshot of the answer),
- whether you used `--stub-llm` or a real model.

Never paste database passwords or API keys. Secrets are read from the environment only.

## Pull requests

1. Fork the repo and create a branch.
2. Keep changes focused. One topic per pull request.
3. Run the project's tests (and the `e2e/` suite where it applies) before opening the PR.
4. If you change a metric definition, update its reconciliation query in `validation/` and its change-log entry.
5. Describe what changed and why.

## Good first contributions

- Improve the README or a per-project guide where you got stuck.
- Add example questions (normal, ambiguous, unsupported) to an e2e question set.
- Add a metric or dimension to an existing semantic layer, with a reconciliation check.
- Try the quick start on a fresh machine and report any friction.
- Share a semantic layer you built for another dataset.

## Principles to keep

- The LLM never writes SQL and never touches the database.
- Prefer a clarification over a guess.
- Each metric's meaning lives in one place.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
