# Comprehensive Problem Statement & Engineering Blueprint
## Platform: Super Agent Liquidity & Risk Intelligence Platform
## Event: bKash Presents SUST CSE Carnival 2026 Hackathon (Codex Community)

---

## SECTION 1: Executive Summary
[cite_start]In the Bangladeshi Mobile Financial Services (MFS) ecosystem, retail financial outlets predominantly operate as **"Super Agents."** These agents serve customers across multiple competing platforms—such as **bKash, Nagad, and Rocket**—from a single physical retail location[cite: 4, 10].

### The Operational Paradox:
* [cite_start]**The Shared Physical Resource:** The agent manages daily operations using one single, combined pool of physical cash kept inside their physical shop drawer (`shared_physical_cash`)[cite: 11].
* **The Fragmented Digital Resource:** Each MFS provider operates as an isolated digital tenant. [cite_start]The agent holds separate, disconnected electronic money (e-money) balances for each provider inside their respective app wallets[cite: 11].
* [cite_start]**The Core Blindspot:** No unified visibility layer exists to track how the rapid drawdown of one provider's e-money impacts the aggregate shared physical cash[cite: 12]. [cite_start]An agent might look financially liquid when summing up all electronic balances, yet face immediate service breakdown because their physical drawer is completely empty, or because a specific provider's e-money balance hits zero[cite: 12].
* [cite_start]**Platform Goal:** Build a working decision-support prototype that grants unified multi-provider liquidity visibility, predicts cash runaways, flags anomalous transaction clusters, and routes privacy-preserving resolution tickets to field operators without executing real transactions or issuing definitive fraud conclusions[cite: 13, 14].

---

## SECTION 2: Background and Context
To understand the platform's execution pressure, visualize a real-world edge-case scenario:

* [cite_start]**The Scenario:** An agent outlet in a hyper-dense market on the afternoon before Eid[cite: 16]. [cite_start]The shop processes high-velocity transaction streams for bKash, Nagad, and Rocket concurrently[cite: 17].
* [cite_start]**The Liquidity Crisis:** Cash-out requests experience sudden, massive exponential spikes[cite: 18]. [cite_start]The agent can monitor individual balances but cannot accurately calculate if the shared drawer cash will deplete within the next 30 or 60 minutes based on real-time transaction velocities[cite: 18].
* [cite_start]**The Risk Anomalies:** Concurrently, a localized surge occurs where multiple cash-outs of near-identical amounts are processed within minutes from a small, tight cluster of anonymized account hashes[cite: 19, 20]. [cite_start]This could be legitimate festival demand, a systemic data feed delay, or structural transaction splitting designed to bypass regulatory velocity alerts[cite: 21].
* [cite_start]**The Coordination Gap:** When an alert triggers, multi-role communication collapses[cite: 22]. [cite_start]The agent, field territory officer, area manager, and central operations teams lack a unified, traceable state-machine timeline to acknowledge, review, and track resolution tasks[cite: 22].

---

## SECTION 3: Problem Statement Core Pillars
[cite_start]The prototype must programmatically address three intertwined structural vectors[cite: 24]:
1. [cite_start]**Upcoming Liquidity Shortage:** Real-time forecasting of physical and electronic depletion horizons using active stream velocities[cite: 24, 30].
2. [cite_start]**Unusual Transaction & Balance Behavior:** Algorithmic identification of behavioral anomalies backed by clear, transparent structural evidence logs (`evidence_json`) rather than opaque black-box scoring[cite: 24, 25, 31].
3. [cite_start]**Operational Coordination Workflow:** Multi-tenant, role-restricted incident routing that assigns distinct ownership timelines while rigorously isolating proprietary competitor data[cite: 24, 32].

---

## SECTION 4: Challenge Objectives

### Primary Engineering Objectives:
* [cite_start]Provide a unified operational view combining the shared physical cash drawer with separate provider-specific digital balances[cite: 29].
* [cite_start]Compute provider-level and aggregate drawer depletion rates before terminal service failure[cite: 30].
* [cite_start]Surface behavioral anomalies with deep, context-aware evidence structures while preserving statistical uncertainty parameters[cite: 31].
* [cite_start]Support end-to-end human-in-the-loop coordination with formal tracking of case owners, escalation states, and auditable actions[cite: 32].
* [cite_start]Isolate competitor metrics to prevent critical data leaks between providers[cite: 39, 56].

### Secondary Objectives:
* [cite_start]Enable prioritized, hierarchical views grouped by area, division, provider, or specific timestamp windows[cite: 36].
* [cite_start]Output natural language actionable insights in native Bengali and Banglish for field operators[cite: 37].
* [cite_start]Implement resilient fallback behaviors when specific provider streaming components face latency, dropping or corrupting data packets[cite: 38].

### Optional Advanced Objectives:
* [cite_start]Explore multi-hop relationship patterns or graph-like proximity structures using anonymized synthetic identifiers[cite: 44].
* [cite_start]Support proactive simulated "what-if" stress tests (e.g., predictive impact of a localized festival or agent suspension)[cite: 45].
* [cite_start]Map localized risk hotspots and aggregate regional operational metrics[cite: 48].

---

## SECTION 5: Intended Users & Stakeholders
[cite_start]The platform maps across a strict role hierarchy with separated data permissions[cite: 53]:

1. [cite_start]**Multi-Provider Agent:** Needs live tracking of the physical drawer against individual digital wallets, intuitive warnings, and simplified local steps to request physical cash routing[cite: 59].
2. **Territory / Field Officer:** Monitors assigned agent sub-clusters within their specific area boundary. [cite_start]Receives routed operational tickets, conducts physical reviews, and records field notes[cite: 53, 59].
3. [cite_start]**Risk / Compliance Analyst:** Investigates complex structural anomalies using deep statistical evidence blocks (`master_evidence_json`) without making absolute fraud assumptions[cite: 55, 59].
4. [cite_start]**Central Provider Operations Team:** Governs global platform health, evaluates regional liquidity shortages, coordinates cash distribution channels, and handles high-priority escalations[cite: 53, 55].

---

## SECTION 6: Scope Boundaries

### IN-SCOPE (Strict Production Requirements):
* [cite_start]A simulated streaming environment ingesting real-time concurrent multi-provider transactions[cite: 63].
* [cite_start]Predictive calculations evaluating the depletion rate of shared physical cash vs isolated digital wallets[cite: 63, 64].
* [cite_start]Generating explainable, deterministic evidence payloads for flagged behavior profiles[cite: 64, 65].
* [cite_start]A strict state-machine engine driving the lifecycle of isolated tickets across provider boundaries[cite: 69].

### OUT-OF-SCOPE (Explicitly Prohibited Actions):
* [cite_start]**NO Real Financial Execution:** No actual moving of money, processing real bank settlements, or modifying external production balances[cite: 6, 72].
* [cite_start]**NO Opaque Machine Blocking:** The platform must never automatically block an agent, freeze customer wallets, or emit absolute fraud accusations[cite: 14, 74].
* [cite_start]**NO Sensitive Credential Ingestion:** Absolute prohibition against capturing or processing passwords, PINs, or raw OTP payloads[cite: 75].

---

## SECTION 7: Functional Requirements Matrix

| Priority | Requirement Code | Description | Ingestion / Output Specification |
| :--- | :--- | :--- | :--- |
| **Mandatory** | `REQ-FUN-001` | Shared Cash & Wallet Parsing | [cite_start]Track live physical drawer cash against independent provider e-money states[cite: 77]. |
| **Mandatory** | `REQ-FUN-002` | Predictive Depletion Tracking | [cite_start]Forecast exactly which resource will fail first and compute the time envelope ($T_{runaway}$)[cite: 77]. |
| **Mandatory** | `REQ-FUN-003` | Anomaly Classification | [cite_start]Flag specific behavioral patterns (e.g., splitting) and expose the analytical variables[cite: 77]. |
| **Mandatory** | `REQ-FUN-004` | Non-Accusatory Protocol | [cite_start]Use risk-conscious taxonomy ("unusual behavior", "requires review")[cite: 77]. |
| **Mandatory** | `REQ-FUN-005` | State-Machine Allocation | [cite_start]Drive explicit tracking of ticket states (`OPEN`, `ACKNOWLEDGED`, `UNDER_REVIEW`, `RESOLVED`)[cite: 77]. |
| **Mandatory** | `REQ-FUN-006` | Degradation Fallback | [cite_start]Reduce engine confidence scores when data layers encounter ingestion delays[cite: 77]. |
| **Recommended** | `REQ-FUN-007` | Context Filtering | [cite_start]Slice active dashboards across Division, Provider, Agent status, and Time[cite: 77]. |
| **Recommended** | `REQ-FUN-008` | Native Bengali Localization | [cite_start]Generate explainable AI summaries in native Bengali (`message_bn`) via LLM integration[cite: 77]. |

---

## SECTION 8: Non-Functional Expectations
* [cite_start]**Explainability:** High-impact system alerts must explicitly append underlying raw parameters, timestamps, and mathematical breaches[cite: 80].
* [cite_start]**Security & Privacy:** Enforce strict Multi-Tenant Data Separation[cite: 80]. [cite_start]A field officer from bKash must be physically and logically blocked from viewing transaction logs, velocity rates, or e-money metrics belonging to Nagad or Rocket[cite: 56, 147].
* [cite_start]**Auditability:** Every historical transition, state change, field note escalation, or status update must write an un-modifiable row to the audit infrastructure[cite: 80].

---

## SECTION 9: Data Layer & Core Modeling Constraints
* [cite_start]Data ingestion feeds operate on realistic synthetic profiles utilizing anonymized identifiers[cite: 84, 85].
* [cite_start]Raw user profiles utilize deterministic synthetic cryptographic hashes (`customer_id_hash`) to map sequence behaviors without exposing customer information[cite: 80, 85].
* [cite_start]**Risk Interpretation Mandate:** An anomaly flag is merely an indicator of variance, never hard proof of malicious intent[cite: 88]. [cite_start]Systems must document predicted false-positive risks stemming from high-volume macro events like salary distribution cycles or localized shopping rushes[cite: 88, 92].

---

## SECTION 10: Required Submission Deliverables
1. [cite_start]**Working Prototype:** Live interactive dashboard displaying data boundaries, liquidity runaways, alert triggers, and ticket state transitions[cite: 96].
2. [cite_start]**Source Repository:** Complete clean source code with a thorough documentation README, architecture blueprints, environment parameters, and seed data scripts[cite: 96].
3. [cite_start]**Data Simulation Document:** Technical breakdown detailing data generation models, behavioral assumptions, and analytical boundaries[cite: 96].
4. [cite_start]**Validation Evidence Logs:** Traceable outputs demonstrating a minimum of three core analytical or system metrics[cite: 96].
5. [cite_start]**Responsible Design Framework:** Clear documentation mapping data privacy handling, human override bounds, and active mitigation steps for false-positives[cite: 96].

---

## SECTION 11: Expected Demonstration Scenarios

### Scenario A: The Hidden Provider Shortage
* **Context:** Summed up across all wallets, the agent looks structurally solvent. [cite_start]However, one specific provider's e-money is experiencing extreme cash-out velocity and is about to hit zero[cite: 107].
* [cite_start]**Expected Output:** Fire an immediate alert calculating the exact deficit window, the tracking confidence metric, and localized steps to re-balance[cite: 108].
* **Mandatory Bangla Localization Payload:**
  > [cite_start]*"বর্তমান লেনদেনের ধারা অনুযায়ী বিকেল ৫টা ২০ মিনিটের মধ্যে আপনার নগদ টাকা শেষ হয়ে যেতে পারে। সবচেয়ে বেশি চাপ আসছে বিকাশ ক্যাশ-আউট থেকে। নিরাপদভাবে সেবা চালু রাখতে কমপক্ষে ২০,০০০ টাকা অতিরিক্ত নগদ ব্যবস্থা করার পরামর্শ দেওয়া হচ্ছে।"* [cite: 110, 111]

### Scenario B: Liquidity Velocity with Pattern Anomalies
* [cite_start]**Context:** Shared physical cash is dropping exponentially while a tight sequence of cash-outs of identical value targets a single provider channel from highly concentrated account hashes[cite: 117].
* **Expected Output:** Capture the physical drawdown rate alongside a high-confidence anomaly profile. [cite_start]Recommend human field routing before execution changes are enforced[cite: 118].
* **Mandatory Bangla Localization Payload:**
  > [cite_start]*"গত ১২ মিনিটে স্বাভাবিকের তুলনায় অনেক বেশি ক্যাশ-আউট হয়েছে। কয়েকটি লেনদেনের পরিমাণ প্রায় একই এবং অল্প কয়েকটি অ্যাকাউন্ট থেকে বারবার অনুরোধ এসেছে। এটি ঈদ-পূর্ব স্বাভাবিক চাহিদাও হতে পারে, তবে বড় অঙ্কের নগদ পুনরায় সরবরাহের আগে লেনদেনগুলো পর্যালোচনা করা প্রয়োজন।"* [cite: 120, 121]

### Scenario C: Stream Inconsistency & Delayed Ingestion
* [cite_start]**Context:** Data streams from specific provider networks experience communication failures, arriving late or containing conflicting balance tallies[cite: 123, 124].
* [cite_start]**Expected Output:** System flags a data-quality failure, lowers the overall analytical certainty metric, and bars misleading automatic planning commands[cite: 124].

### Scenario D: End-to-End Coordinated Lifecycle
* **Context:** A high-priority crisis triggers. [cite_start]The platform validates routing rules, splits tickets across providers, tracks acknowledgement timelines, enables escalation paths, and logs resolution updates to the audit trail[cite: 125, 126].

---

## SECTION 12: Success Criteria & Core Metric Framework
[cite_start]Solutions will be evaluated against real system evidence[cite: 133]. [cite_start]The platform must programmatically compute and expose at least **three** of the following architectural metrics[cite: 5, 133, 134]:

1. **Liquidity Runaway Window ($T_{runaway}$):**
   $$\Delta T_{depletion} = \frac{\text{Current Resource Balance}}{\text{Aggregate Outflow Velocity} - \text{Aggregate Inflow Velocity}}$$
   [cite_start]Tracks the remaining operational minutes before physical cash or isolated digital e-money hits total depletion[cite: 134].
2. [cite_start]**Algorithmic Risk Confidence Score ($C_{risk}$):** A decimal percentage mapping the statistical deviation of an active transaction cluster from historical baseline models[cite: 134].
3. [cite_start]**Mean Time to Acknowledge & Resolve (MTTA / MTTR):** Temporal performance metrics calculated directly from the state transition records inside the database audit logs[cite: 134].

---

## SECTION 13: Evaluation Criteria Matrix
[cite_start]Judges will score implementations across the following specific weights[cite: 138, 139]:
* **Technical Implementation & Integration Quality (25%):** System architecture depth, component cohesion, state-machine tracking, code purity, and structural reliability[cite: 139].
* [cite_start]**Innovation & Decision-Support Value (20%):** Originality of the combined liquidity visualizations, anomaly mapping pipelines, and operational value[cite: 139].
* [cite_start]**Data & Analytical Quality (20%):** Precision of simulation streams, handling of operational uncertainty, evidence parsing, and false-positive evaluation[cite: 139].
* **Problem Understanding & Ecosystem Relevance (15%):** Integrity of multi-provider role boundaries, operational structures, and system assumptions[cite: 139].
* [cite_start]**User Experience & Explainability (10%):** Interface utility, role restriction clarity, actionable next steps, and localization accuracy[cite: 139].
* [cite_start]**Security, Privacy, & Responsible Design (5%):** absolute protection of provider boundaries, human review hooks, and non-accusatory safety patterns[cite: 139].
* **Presentation & Coherent Narrative (5%):** Story-driven scenario validation, clear evidence compilation, and objective documentation of platform limits[cite: 139].

---

## SECTION 14: Restrictive Guardrails
* Never allow cross-tenant data leaks. [cite_start]Raw e-money balances and transaction rates are confidential business secrets[cite: 56, 147].
* [cite_start]The AI engine must never generate absolute legal or fraudulent declarations; all output arrays denote probabilistic warnings requiring human validation[cite: 14, 145].
* Do not expose competitor operational velocities inside filtered child tickets; the evidence splitting layer must drop non-belonging vectors entirely[cite: 147].

---

## SECTION 15: Advanced Innovation Vectors
* Scalable multi-tenant microservices architecture designed to run high-volume concurrent streaming loops.
* Proactive geo-spatial assignment engines routing tasks to field officers based on regional division indexes.
* Predictive "what-if" planning modules tracking how nearby agent suspensions re-route local transaction patterns.

---

## SECTION 16: System Validation Checklist
* [cite_start][ ] Minimum of two MFS providers distinctly represented[cite: 163].
* [cite_start][ ] Live physical drawer balance parsed alongside separate e-money wallets[cite: 164].
* [cite_start][ ] Forward-looking forecasting window active ($T_{runaway}$)[cite: 165].
* [cite_start][ ] Structural evidence payload (`evidence_json`) bound to generated tickets[cite: 166].
* [cite_start][ ] Strict state-machine transitions active with immutable audit tracking[cite: 168].
* [cite_start][ ] Minimum of 3 operational efficiency or analytical metrics measured and rendered[cite: 170].

---

## SECTION 17: Closing Mandate
[cite_start]The definitive winning prototype is not the one with the flashiest interface, but the one that models a complex multi-provider ecosystem with absolute data safety[cite: 175, 176]. [cite_start]It must demonstrate a high degree of architectural maturity, combining complex stream analytics, clear evidence logs, and real-world coordination flows while strictly respecting strict corporate privacy boundaries[cite: 176].

---
---

## APPENDIX A: Production Database ERD (Mermaid Schema)

```mermaid
erDiagram
    PROVIDERS {
        string id PK
        string provider_name
    }

    DIVISIONS {
        string div_id PK
        string div_name
    }

    RISK_ANALYSTS {
        string id PK
        string name
        string provider_id FK
        string div_id FK
        string area_name
    }

    TERRITORY_OFFICES {
        string id PK
        string name
        string provider_id FK
        string div_id FK
        string area_name
        string risk_analyst_id FK
    }

    AGENTS {
        string agent_id PK
        string shop_name
        string area
        string district
        decimal shared_physical_cash
        string status
    }

    AGENT_PROVIDER_ASSIGNMENTS {
        int id PK
        string agent_id FK
        string provider_id FK
        string to_officer_id FK
    }

    PROVIDER_WALLETS {
        string wallet_id PK
        string agent_id FK
        string provider_id FK
        decimal e_money_balance
        datetime last_sync_time
    }

    TRANSACTIONS_STREAM {
        string tx_id PK
        string agent_id FK
        string provider_id FK
        string customer_id_hash
        string tx_type
        decimal amount
        datetime timestamp
    }

    ALERTS {
        string alert_id PK
        string agent_id FK
        string provider_id FK
        string alert_type
        string severity
        string message_bn
        decimal confidence_score
        string master_evidence_json
        datetime created_at
    }

    TICKETS {
        string ticket_id PK
        string alert_id FK
        string provider_id FK
        string assigned_officer_id FK
        string current_owner_role
        string status
        string evidence_json
        datetime created_at
        datetime updated_at
    }

    AUDIT_LOGS {
        string log_id PK
        string ticket_id FK
        string action_taken
        string performed_by_role
        string notes_text
        datetime timestamp
    }

    PROVIDERS ||--|{ RISK_ANALYSTS : employs
    PROVIDERS ||--|{ TERRITORY_OFFICES : operates
    PROVIDERS ||--|{ AGENT_PROVIDER_ASSIGNMENTS : registers
    PROVIDERS ||--|{ PROVIDER_WALLETS : links
    PROVIDERS ||--|{ TRANSACTIONS_STREAM : logs
    PROVIDERS ||--|{ ALERTS : context
    PROVIDERS ||--|{ TICKETS : owns

    DIVISIONS ||--|{ RISK_ANALYSTS : defines_boundary
    DIVISIONS ||--|{ TERRITORY_OFFICES : defines_boundary

    RISK_ANALYSTS ||--|{ TERRITORY_OFFICES : monitors
    TERRITORY_OFFICES ||--|{ AGENT_PROVIDER_ASSIGNMENTS : manages
    TERRITORY_OFFICES ||--|{ TICKETS : handles

    AGENTS ||--|{ AGENT_PROVIDER_ASSIGNMENTS : assigns
    AGENTS ||--|{ PROVIDER_WALLETS : owns
    AGENTS ||--|{ TRANSACTIONS_STREAM : processes
    AGENTS ||--|{ ALERTS : triggers

    ALERTS ||--|{ TICKETS : splits_into
    TICKETS ||--|{ AUDIT_LOGS : records