Fleet AI Albert Sun Work Trial  
Sep 15-16, 2026  
Seed Data Generation Project  
Project Bonsai: Mimic realistic, complex systems at a miniature scale 🪴

# Product

We sell a 2 GB file that contains the of a SMB  
Goal: Present to a customer considering purchasing the data

# System

\[TO REMOVE\] (Goal: introduce a teammate to the reusable system you just built)  
**Github Link.** [https://github.com/fleet-albertsun-worktrial/bonsai](https://github.com/fleet-albertsun-worktrial/bonsai)  
**Docs Link.**   
**Summary.** Bonsai is a system that \[INSERT HERE\]

```py
from bonsai import generate, WorldParameters
world_parameters = WorldParameters(...)

generate(
output_path=”results/”, 
world_parameters=world_parameters
)
# Outputs a folder in results/ containing output.sqlite, statistics.json, and pipeline.log.
```

\[TODO: CREATE AND HOST HAND-WRITTEN DOCS FOR THIS\]

**Purpose.** Simulate plausible business activity, diverse records, realistic transaction histories.

**Metrics.** 1-2 GB of realistic data. Yuhan expressed that we should be creative in filling the file with this realistic data. My approach to this is to add a set of parameters that enables us to scale the data and add arbitrary complexity. 

**Scope.** With Yuhan, we scoped this project to focus on building worlds resembling SMB (small-medium sized businesses) that enable accountants or corporate finance people at companies to look at their books to understand PnL and audit any inconsistencies. The data must be numerically consistent where equations for certain numbers must add up to each other. The goal is to build data that is generally-consistent, enabling the deployments team to create interesting tasks by synthesizing artifacts and perturbing the data.

**Schema analysis.** To start, we must understand the schema. I created an entity-relationship diagram of schema.sql. 
There are 134 application entities (tables used by the application), such as `customers` or `invoices`. There are 399 foreign keys, which are database constraints that link one table to another. For instance, `invoices.customer_id → customers.id`. 

**System.** Our approach is to create functions that create people, companies, and plausible business motions and then translate them into the schema. 

1. Parameters. We receive the business scenario in text and a set of parameters we want our simulation engine to abide by.  
2. Extract Knowledge Base  
   1. Generate knowledge base and shared entity registry. We convert the narrative into authoritative facts such as people, companies, entities, dates, currencies, quantities, prices, policies, and relationships. These master records will assign stable IDs. This will enable us to run deterministic validators afterwards.   
3. Dependency plan. Generate records in the following order:   
   1. Organization, user, accounting setup  
   2. Catalog, customers, and vendors  
   3. Orders, subscriptions, and contracts  
   4. Fulfillments, receipts, and usage  
   5. Invoices, bills, payments, and charges  
   6. Revenue recognition and journal entries  
   7. Reports and compliance records  
4. Business event ledger. Represent each fact once as an event, and then derive affected tables from it.   
   1. usage\_record → charge → invoice → payment → revenue\_plan → journal\_entry  
5. Compile  
6. Create Verifiers: Check that we pass all of our unit tests. If not, iteratively improve the system.  
   1. Knowledge base:   
      1. The entities, dates, currencies, quantities, prices, policies, and relationships exist in the sqlite file.   
   2. Rules. Ensure mathematical consistency; for instance, that PnL adds up in a cohesive way.   
      1. Debits equal credits  
      2. Line totals equal document totals  
      3. Payments do not exceed outstanding balances  
      4. Dates follow causal order  
      5. Subsidiary, currency, and accounting periods agree  
      6. Quantities ordered, fulfilled, received, and billed reconcile  
      7. Revenue-plan lines sum to the plan total  
7. Run Verifiers

Deliverables: 

- System: takes a database schema as input and produces a SQLite database ranging up to 2GB. It is in a single repo and documents all 3rd party or cloud services  
- Generated database for Stratum using the supplied schema

Create a Entity-Relationship Diagram of the tables to understand what they look like  
Maybe indices is where we should start generating

# 

# Appendix

I started off my problem solving process by taking a quick glance at each table in the schema to best understand the problem: 

What’s the Stratum schema? 

- Tables: accounting\_books, accounting\_periods, accounts, approval\_requests, approval\_steps, approval\_workflows, audit\_trail\_entries, bank\_accounts, bank\_transfers, bill\_lines, bill\_payment\_applications, bill\_payments, bill\_variances, billing\_accounts, billing\_schedule\_milestones, billing\_schedules, bills, budget\_lines, budgets, cash\_refund\_lines, cash\_refunds, cash\_sale\_lines, cash\_sales, change\_order\_lines, change\_orders, charges, classes, commit\_plus\_overages, company\_preferences, compliance\_controls, compliance\_tests, consolidation\_processes, consolidation\_subsidiaries, consolidation\_translations, credit\_memo\_applications, credit\_memos, currencies, currency\_exchange\_notes, customers, dashboard\_roles, dashboard\_widgets, dashboards, deferred\_revenue\_reclassifications, departments, deposits, depreciation\_schedules, elimination\_entries, employees, estimate\_lines, estimates, expense\_report\_lines, expense\_reports, financial\_reports, fixed\_assets, forecast\_lines, forecasts, fulfillment\_lines, fulfillment\_request\_lines, fulfillment\_requests, intercompany\_transactions, invoice\_lines, invoices, item\_fulfillments, item\_receipt\_lines, item\_receipts, item\_subsidiaries, items, journal\_entries, journal\_entry\_lines, kpi\_widget, kpis, locations, payment\_applications, payment\_term\_subsidiaries, payment terms, payments, prepaid\_drawdowns, prepaid\_usages, price\_books, price\_plan\_tiers, price\_plans, pricing\_tiers, purchase\_contract\_lines, purchase\_contracts, purchase\_order\_lines, purchase\_orders, rating\_runs, reclassification\_journal\_entries, reonciliation\_lines, reconciliations, report\_snapshots, request\_for\_quote\_lines, requests\_for\_quote, requisition\_lines, requisitions, return\_authorization\_lines, return\_authorizations, revenue\_arrangements, revenue\_elements, revenue\_forecast\_lines, revenue\_forecasts, revenue\_plan\_lines, revenue\_plans, revenue\_recognition\_journals, revenue\_rules, sales\_order\_billing\_schedules, sales\_order\_lines, sales\_orders, saved\_searches, scenario\_assumptions, scenarios, subscription\_lines, subscription\_plan\_lins, subscription\_plans, subscriptions, subsidiaries, tax\_code\_subsidiaries, tax\_codes, tax\_periods, usage\_records, user\_role\_subsidiaries, user\_roles, user\_roles\_mapping, user\_subsidiaries, users, vendor\_credit\_applications, vendor\_credits, vendor\_prepayments, vendor\_quote\_lines, vendor\_quotes, vendor\_return\_authorization\_lines, vendor\_return\_authorizations, vendors  
- Indices:   
  - \`idx\_accounting\_books\_book\_id\` ON \`accounting\_books\` (\`book\_id\`)  
  - \`idx\_accounting\_books\_subsidiary\` ON \`accounting\_books\` (\`subsidiary\_id\`)  
  - \`idx\_accounting\_periods\_fiscal\_year\` ON \`accounting\_periods\` (\`fiscal\_year\`)  
  - \`idx\_accounting\_periods\_subsidiary\` ON \`accounting\_periods\` (\`subsidiary\_id\`)  
  - \`idx\_accounting\_periods\_status\` ON \`accounting\_periods\` (\`status\`)  
  - …

Questions: 

- Any particular scenario that we care about?   
  - Corporate accounting, close the book every month at the end of the month, train an agent on these tasks  
  - Come up with a couple hypotheses for the workflows / scenarios we are targeting.   
  - Base example workflow: at the end of everymonth, you want an agent to close a book to make sure every number matches with each other  
  - In real world, accountants / corporate finance person at a company will look at a revenue, costs / margins, expenses from employees, vender costs. They will make sure that the number that matches with the emails. PnL. We will focus on building the data. Downstream, we will have experts to look at the data.   
- Are there tables that we care most about?   
  - Varied by domain.   
  - “Can you sanity check the data?” \<- we want our data to sanity check the data  
  - Sometimes we would drop the data to experts to just look at them  
  - Sometimes we will come up with our own thesis  
- Should I design and run any tasks?  
  - We will hand this SQLite and somewhere downstream someone will use an API to perturb and create the task. Frontier model will call the SQLite directly.   
- What types of tasks should we support? What task types? Should these be decided beforehand?  
- What sort of parameters should I support in my system?   
  - The customer will tell us a Spec (huge bank)  
  - Company, people  
  - Simulate a small to medium SMB style, 1-2GB  
- Byte volume  
  - To fill the byte volume, be creative  
- Tasks  
  - What if the later people tell us that the world is too system  
- Maybe:   
  - A researcher might come up with an interesting reward function / different shape of task. Might include user simulation.   
- Choose one of these directions:   
  - Adding one more application, such as Email, notion, ramp. How would you ensure consistency over all these apps  
  - Files. Receipts, excel, source of truth was in excel last month, the task might be like, go through this month troubleshooting accounting error based on last month’s excel. Think about files.   
- Can I have an API key? For cursor and openai  
  - Reimburse it with Astra  
  - Budget: 1000-2000  
  - If the data is good enough, you can sell it for a lot of money  
  - Limit R\&D Cost

# Pastebin

Deliverables: 

- System: takes a database schema as input and produces a SQLite database ranging up to 2GB. It is in a single repo and documents all 3rd party or cloud services  
- Generated database for Stratum using the supplied schema
