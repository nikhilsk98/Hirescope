# Bullet guidelines

Structure: **strong action verb → what was built or done → technologies → verified result or scope.**

- 1–2 lines, aim for ≤ 25 words. Never more than 2 lines (the PDF check fails it).
- Vary opening verbs within a role (Built, Designed, Implemented, Optimized, Migrated, Automated, Reduced,
  Deployed, Developed, Engineered, Integrated).
- Keep every metric exactly as written: 15+ may become 15, never 20; 40% stays 40%.
- No metric? Describe concrete scope (users, services, deployments, data volume) only if the evidence states it.
- Past tense for past roles; no first person; consistent punctuation (no trailing periods, or all of them).
- Name the technology in the bullet, spelled as the vendor does (JavaScript, Node.js, MongoDB, PostgreSQL, AWS).
- Use the JD's exact term when the evidence is the same thing ("RESTful APIs" for REST APIs you built); never
  for a different technology.

Example (evidence `exp-web-developer-b4`: "Improved application performance by optimizing MongoDB/MySQL schemas,
indexes, and queries, reducing API response times by 50%."):
- ✓ "Optimized MongoDB/MySQL schemas, indexes and queries, reducing API response times by 50%"
- ✗ "Optimized PostgreSQL queries at scale, cutting latency by 60%" (wrong database, invented scale and number)
