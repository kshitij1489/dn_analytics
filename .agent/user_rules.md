# User Preferences & Rules

- **No Unauthorized Code Changes**: Always ask for explicit approval before making any code changes or file modifications. Providing explanations or answering questions does not count as approval for implementation.
- **Plan First**: Present an implementation plan and wait for the user to say "Approve" or "Proceed" before executing any edits.

- **Date Field Conventions**:
  - `created_on`: PRIMARY for analytics. Use this for business date and chart generation.
  - `created_at`: SYSTEM audit only. Never use for business logic.
  - `occurred_at`: INVALID. Do not use.
- **Business Day**: Defined as 5:00 AM to 5:00 AM.
