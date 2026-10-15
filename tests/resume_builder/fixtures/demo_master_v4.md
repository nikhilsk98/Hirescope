# Master Resume — Demo Candidate
_Source of truth for the DEMO tests. Not a real person._

## Rules for the builder
- Use only metrics with status `verified` or `estimated`. Never use `unverified`.
- Section order: Header → Education → Technical Skills → Experience → Projects, for every application and every level.

## Default one-page resume (DEMO snapshot)
_Builder: this is the approved general-purpose version. Start from it and swap bullets per job description._

**Freelance, Remote — Software Developer** (Feb 2022 – Jan 2025)
- Built an ordering platform for a DEMO bakery supplier using React, Spring Boot, and PostgreSQL, cutting order-entry time by 30%.
- Implemented a React and Redux reporting dashboard for the supplier, tracking sales by product and period.
- Developed a booking app for a DEMO yoga studio using React, Node.js, and MongoDB, with role-based access and email confirmations.
- Automated testing and AWS EC2 deployments with GitHub Actions, cutting deployment time from 20 to 6 minutes (70%).

**DemoCorp, Austin, TX — Software Engineer | Client: Demo Bank** (Feb 2021 – Dec 2021)
- Contributed to Demo Bank's platform modernization, developing REST endpoints across 3 Java/Spring Boot microservices.
- Migrated 4 messaging workflows to Kafka, building Java producers and consumers with idempotency checks for safe retries.
- Tuned consumer concurrency, cutting p95 latency from 500 to 400 ms and raising throughput from 100 to 150 messages/sec.

**Tiny Labs, Austin, TX — Software Engineering Intern** (Jul 2020 – Dec 2020)
- Developed 5 Java/Spring Boot REST endpoints for DEMO inventory workflows with input validation and error handling.
- Added 20 JUnit unit tests for backend validation and containerized the app with Docker Compose.

**Alpha – Route Planner** (GitHub) — C++17, Python | CS5800 Algorithms, Demo University | Team of 3
- Designed and implemented Alpha, a DEMO A* variant with bounded local probes, empirically preserving optimal cost.
- Validated optimality against BFS on 1,000 randomized grids and benchmarked against A* on DEMO maps.

**Beta – Mood Recommender** (GitHub) — Python, FastAPI, Redis | Demo University | In Progress
- Designing a DEMO two-stage recommender: candidate retrieval over pgvector and a LightGBM ranker.
- Architecting real-time personalization in which swipe events update a Redis session embedding for re-ranking.

---

## Contact
- **Name:** Demo Candidate
- **Location:** Boston, MA
- **Location (alternate, for remote/out-of-state roles):** Boston, MA (Open to Relocation). Use this line for remote or out-of-state jobs.
- **Phone:** +1 (555) 010-0000
- **Email:** demo@example.com
- **LinkedIn:** linkedin.com/in/democ
- **GitHub:** github.com/demo-candidate (repos public)

## Target roles
All primary. Builder: identify which track a job description fits.
1. Software Engineer (SWE)
2. Data Engineer

## Summary
_Builder: never include a summary section on any resume._

---

## Education

### Demo University — Boston, MA
- **Degree:** Master of Science in Computer Science
- **Graduation:** Expected May 2027 (use for all applications)
- **GPA:** 3.8/4.0

#### Coursework
| Course | Status | Use on resume? |
|---|---|---|
| Algorithms | Completed | Yes |
| Cloud Computing | Completed | Yes |
| Distributed Systems | In progress, Fall 2026 | Yes |
| Quantum Basket Weaving | Planned | No, not until enrolled |

_Builder: default resume shows 2 courses (Distributed Systems, Cloud Computing). Keep it to 2 unless instructed otherwise._

### Sample College — Austin, TX
- **Degree:** Bachelor of Engineering in Computer Engineering
- **Graduation:** Jul 2020
- **GPA:** Do not include.

---

## Experience
### Software Developer (Freelance)
- **Company line:** "Freelance". Never write "Self-Employed."
- **Title (default):** Software Developer
- **Title (alternates):** Software Engineer (Freelance); Full-Stack Software Engineer (Freelance) for full-stack roles. Never add keyword tags to the title.
- **Location:** Remote (USA)
- **Dates:** Feb 2022 – Jan 2025
- **Context:** Delivered 3 DEMO applications for 2 small-business clients.
- _Builder, compliance: never imply HIPAA or PCI work._
- **Tech used:** React, Redux, Java, Spring Boot, Node.js, PostgreSQL, MongoDB, Oracle _(skill only; no specific app recorded)_, AWS (EC2, S3), GitHub Actions

#### Metrics
| Metric | Before → After | Project | How I know | Status |
|---|---|---|---|---|
| Order-entry time | −30% | A. Ordering | Confirmed | verified |
| Dashboard p95 API time | 800 ms → 300 ms | B. Reporting | Load test | verified |
| Deployment time | 20 min → 6 min (−70%) | Across projects | Measured | verified |
| Booking conversions | +45% | C. Booking | Guess | unverified |

#### Bullet bank

**A. Ordering — DEMO bakery supplier**
_In my own words:_ "I built a DEMO ordering platform for a bakery supplier; order entry got 30% faster."
- Built an ordering platform for a DEMO bakery supplier using React, Spring Boot, and PostgreSQL, cutting order-entry time by 30%.
- Built React order-entry forms with inline validation for the supplier's staff.

**B. Reporting — same DEMO supplier**
- Reduced p95 API time of the supplier's reporting app from 800 ms to 300 ms by adding PostgreSQL indexes.
- Built React form views for the supplier's reporting filters with validation messages.
- Compact variant of the dashboard bullet: Implemented a React and Redux reporting dashboard for the supplier, tracking sales by product and period.

**C. Booking — DEMO yoga studio**
- Developed a booking app for a DEMO yoga studio using React, Node.js, and MongoDB, with role-based access and email confirmations.
- Raised the studio's booking conversions by 45% with a one-click booking flow.
- Built React booking forms for the studio's front desk with clear conflict messages. (pending)

**Across projects**
- Automated testing and AWS EC2 deployments with GitHub Actions, cutting deployment time from 20 to 6 minutes (70%).

_Builder: for a one-page resume, pick 4–5 bullets from this role, at most 2 per project. The two form bullets (A2 and B2) overlap; use at most one per resume._

#### Legacy bullets (CV v1, reference only — do not use)
- 99% uptime; 10x faster everything

---

### DemoCorp — Software Engineer
- **Title:** Software Engineer
- **Location:** Austin, TX
- **Dates:** Feb 2021 – Dec 2021
- **Client:** Demo Bank (confirmed OK to name)
- **Context:** Team effort to modernize Demo Bank's platform; the candidate owned their messaging components.
- _Builder: never describe team-owned items as the candidate's own._
- **Tech used:** Java, Spring Boot, Node.js _(verified; no specific component recorded yet, so it stays out of DemoCorp bullets until one is described)_, Kafka, MySQL, Jenkins

#### Metrics
| Metric | Before → After | How I know | Status |
|---|---|---|---|
| Microservices | 3 | Counted | verified |
| Workflows migrated | 4 | Records | verified |
| p95 latency | 500 ms → 400 ms | Benchmarks | verified |
| Throughput | 100 → 150 msg/s | Benchmarks | verified |
| Jenkins pipeline duration | 15 min → 10 min | Build history | verified |

#### Bullet bank

**A. Modernization (team effort)**
- Contributed to Demo Bank's platform modernization, developing REST endpoints across 3 Java/Spring Boot microservices.

**B. Messaging (primary responsibility)**
- Migrated 4 messaging workflows to Kafka, building Java producers and consumers with idempotency checks for safe retries.
- Tuned consumer concurrency, cutting p95 latency from 500 to 400 ms and raising throughput from 100 to 150 messages/sec.

**C. CI/CD**
- Optimized Jenkins pipelines for assigned services, cutting median pipeline duration from 15 to 10 minutes.
- Wrote JUnit tests for backend services and MySQL stored procedures.

_Builder: for a one-page resume, pick 3–4 bullets from this role._

---

### Tiny Labs — Software Engineering Intern
- **Title:** Software Engineering Intern
- **Location:** Austin, TX
- **Dates:** Jul 2020 – Dec 2020
- _Builder: this is an internship. Keep claims narrow and intern-sized. Never use "owned," "led," or "architected."_

#### Metrics
| Metric | Before → After | How I know | Status |
|---|---|---|---|
| REST API endpoints developed | 5 | Stated count | verified |
| Unit tests added | 20 | Stated count | verified |

#### Bullet bank — core (default)
- Developed 5 Java/Spring Boot REST endpoints for DEMO inventory workflows with input validation and error handling.
- Added 20 JUnit unit tests for backend validation and containerized the app with Docker Compose.
- Built Vue.js screens for DEMO inventory records connected to backend APIs.

#### Bullet bank — extended (only when the job requires it; prep before use)
- Extended existing PHP/Laravel REST APIs with data validation.

_Builder: for a one-page resume, pick 2 bullets from this role by default._

---

_Excluded by Demo's decision: Blockchain Intern, Old Chain Co (Jun–Jul 2019). Builder: never add this role._

## Projects
_Builder: never inflate a team or in-progress project into a solo or finished one._

### Alpha: Route Planner with Local Probes
- **Context:** CS5800 Algorithms course project, Demo University. Team of 3. Completed.
- **Repo:** github.com/demo-candidate/alpha (public)
- **Tech:** C++17, Python

#### Bullet bank
- Designed and implemented Alpha, a DEMO A* variant with bounded local probes, empirically preserving optimal cost.
- Validated optimality against BFS on 1,000 randomized grids and benchmarked against A* on DEMO maps.

### Beta: Mood Recommender
- **Context:** In-progress project, Demo University. Status: in design.
- **Repo:** github.com/demo-candidate/beta
- **Planned tech:** Python, FastAPI, PostgreSQL + pgvector, Redis, LightGBM

#### Component status
| Component | Status |
|---|---|
| Retrieval | planned |
| Ranker | planned |

_Builder, strict rules: use present-progressive design verbs only ("Designing," "Architecting"); do not use "Building" until a component is marked built._

#### Bullet bank
- Designing a DEMO two-stage recommender: candidate retrieval over pgvector and a LightGBM ranker.
- Architecting real-time personalization in which swipe events update a Redis session embedding for re-ranking.

### Excluded
- Old Shop App (MERN, from CV v1): excluded because it overlaps with freelance work. Builder: never add it.

## Skills inventory
_Builder: tailor the Skills section to each job description._

#### Resume default (aligned rows)
- **Languages:** Java, Python, JavaScript, SQL, C++
- **Backend & Testing:** Spring Boot, Node.js, REST APIs, JUnit
- **Frontend:** React, Redux, Vue.js
- **Databases & Messaging:** PostgreSQL, MySQL, Oracle, MongoDB, Kafka
- **Cloud & DevOps:** AWS (EC2, S3), Docker, GitHub Actions, Jenkins
- **Certifications:** AWS Certified Developer – Associate (DVA-C02)

#### Inventory
| Skill | Category | Evidence | Tier |
|---|---|---|---|
| Java | Language | DemoCorp, Tiny Labs, Freelance | core |
| Python | Language | Alpha | core |
| JavaScript | Language | Freelance | core |
| SQL | Language | All | core |
| C++ (C++17) | Language | Alpha | core |
| PHP | Language | Tiny Labs (extended bullet) | extended |
| Spring Boot | Backend | All | core |
| Node.js | Backend | Freelance C | core, verified |
| REST APIs | Backend | All | core |
| JUnit | Testing | DemoCorp, Tiny Labs | core |
| React, Redux | Frontend | Freelance | core |
| Vue.js | Frontend | Tiny Labs | core |
| PostgreSQL | Database | Freelance | core |
| MySQL | Database | DemoCorp | core |
| Oracle | Database | Freelance. Builder: list as a skill only; never attach Oracle to a specific project bullet. | core |
| MongoDB | Database | Freelance C | core |
| Kafka | Messaging | DemoCorp | core |
| AWS (EC2, S3) | Cloud | Freelance | core |
| Docker | Containers | Tiny Labs | core |
| Docker Compose | Containers | Tiny Labs | extended |
| GitHub Actions | CI/CD | Freelance | core |
| Jenkins | CI/CD | DemoCorp | core |
| Stored procedures | Database | DemoCorp | extended |

#### Self-reported (no evidence in this file; JD-driven, max 4 per resume)
| Category | Skills |
|---|---|
| Languages | C, Go |
| Cloud & DevOps | Linux, Ansible, Helm |
| Databases | Redis |

#### Concepts (add as a "Concepts" row for backend/distributed roles)
- Asynchronous messaging, idempotency, load testing

#### Planned (never list until built)
- LightGBM, pgvector (Beta). Redis is also self-reported above.

#### Excluded (no evidence)
_Builder: this list always wins over the tool checklist._
- Generic "Azure", RabbitMQ

## Certifications
- AWS Certified Developer – Associate (DVA-C02)
- Microsoft Certified: Azure Fundamentals (AZ-900)

_Builder: show certifications as the last row of the Technical Skills section. Always include the AWS certification; include Azure Fundamentals only for cloud roles or when the job description mentions Azure._
