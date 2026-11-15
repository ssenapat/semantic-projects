# LinkedIn Post

🔗 https://github.com/ssenapat/semantic-projects

**Dashboards are essential for sharing data with a broad audience. But when a leader needs a quick answer to a specific question, how do we get there faster?**

Dashboards remain the right tool for wide and external audiences. For an individual leader with a new question, though, they can take months to build and only cover the questions someone anticipated. When a new question comes up, the cycle starts again: raise a request, wait for the analyst, wait for the next release.

This is why this project matters. It adds a complementary route that gives decision-makers direct, trustworthy answers in minutes, while dashboards keep doing what they do best.

I've built two working examples of it. Users ask questions in plain English and get answers straight from the data.

🎬 **Pagila**: a DVD-rental database (films, customers, payments)
✈️ **US flight on-time performance**: 

Examples of questions you can ask:
- "Cancelled flights by departure airport for January 2026"
- "What was total revenue by month in 2025?"

**What this solves**
- **Long wait for answers:** no dashboard build needed for each new question.
- **Fixed views:** dashboards only show what they were designed to show. Here the user can ask follow-up questions and explore.
- **Trust in AI answers:** an LLM guessing at SQL can invent metrics or get numbers wrong. This design doesn't allow that.

**How it works**
1. **Semantic layer:** YAML definitions of metrics, dimensions, joins and policies, with business meaning, time basis and owner. Metrics are reviewed and approved before they can be used.
2. **Retrieval service:** picks the relevant metrics, dimensions and glossary terms for the question.
3. **LLM intent parser:** the LLM only turns the question into a small, structured request. If the question is ambiguous, it asks, for example "Do you mean origin or destination airport?"
4. **Semantic query service:** deterministic code compiles the validated request into guarded, read-only SQL and runs it on PostgreSQL.
5. **Streamlit UI:** shows the answer, the metric definition, and the request that produced it.

The LLM never writes SQL and never touches the database. It can only choose from definitions the business has approved.

**Benefits**
- **Speed:** from question to answer in seconds, not months.
- **Self-service:** business users get answers without waiting on a data team.
- **Governed and consistent:** everyone gets the same definition of "revenue" or "cancelled flight".
- **Traceable:** every answer shows the definition and the request behind it.
- **Safe:** read-only access, and the system asks or refuses rather than guessing.
- **Reusable:** the same pattern works on any database. Only the semantic layer changes.

The repo includes a README on building your own semantic project from these two examples, plus run scripts to start everything locally.

How is your organisation moving beyond static dashboards? I'd love to hear.

#DataEngineering #Analytics #SemanticLayer #LLM #GenAI #SelfServiceBI #PostgreSQL #OpenSource

---
