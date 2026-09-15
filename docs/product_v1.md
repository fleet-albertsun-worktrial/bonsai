Fleet AI Albert Sun Work Trial
Sep 15-16, 2026
Seed Data Generation Project
Project Bonsai: Mimic realistic, complex systems at a miniature scale 🪴
Product
We sell a 2 GB file that contains the of a SMB
Goal: Present to a customer considering purchasing the data
System
Goal: introduce a teammate to the reusable system you just built
Summary. Bonsai is a system that 


from bonsai import generate, WorldParameters
world_parameters = WorldParameters(
	num_departments=5,
num_employees: int = 100,
num_customers: int = 500,
num_subscriptions: int = 250,
num_sales_orders: int = 1_000,
scenario_duration_days: int = 365,
cross_domain_link_rate: float = 0.6,
exception_rate: float = 0.05
)

generate(
output_path=”results/”, 
world_parameters=world_parameters
)
# Outputs a folder in results/ containing output.sqlite, statistics.json, and pipeline.log.



Purpose. Simulate plausible business activity, diverse records, realistic transaction histories.

Metrics. 1-2 GB of realistic data. Yuhan expressed that we should be creative in filling the file with this realistic data. My approach to this is to add a set of parameters that enables us to scale the data and add arbitrary complexity. 

Scope. With Yuhan, we scoped this project to focus on building worlds resembling SMB (small-medium sized businesses) that enable accountants or corporate finance people at companies to look at their books to understand PnL and audit any inconsistencies. The data must be numerically consistent where equations for certain numbers must add up to each other. The goal is to build data that is generally-consistent, enabling the deployments team to create interesting tasks by synthesizing artifacts and perturbing the data.

Schema analysis. To start, we must understand the schema. I created an entity-relationship diagram of schema.sql. 


There are 134 application entities (tables used by the application), such as customers or invoices. There are 399 foreign keys, which are database constraints that link one table to another. For instance, invoices.customer_id → customers.id. 



System. Our approach is to create functions that create people, companies, and plausible business motions and then translate them into the schema. 

Narrative. We receive the business scenario in human terms.
Generate knowledge base. We convert the narrative into authoritative facts such as people, companies, entities, dates, currencies, quantities, prices, policies, and relationships. 
Shared entity registry. Create master records once and assign stable IDs. Every category references these IDs instead of independently creating customers, items, subsidiaries, or accounts. This will enable us to run deterministic validators afterwards. 
Dependency plan. Generate records in the following order: 
Organization, user, accounting setup
Catalog, customers, and vendors
Orders, subscriptions, and contracts
Fulfillments, receipts, and usage
Invoices, bills, payments, and charges
Revenue recognition and journal entries
Reports and compliance records
Business event ledger./ Represent each fact once as an event, and then derive affected tables from it. 
usage_record → charge → invoice → payment → revenue_plan → journal_entry
Validation: Check that we pass all of our unit tests. If not, iteratively improve the system.
Knowledge base: 
The entities, dates, currencies, quantities, prices, policies, and relationships exist in the sqlite file. 
Rules. Ensure mathematical consistency; for instance, that PnL adds up in a cohesive way. 
Debits equal credits
Line totals equal document totals
Payments do not exceed outstanding balances
Dates follow causal order
Subsidiary, currency, and accounting periods agree
Quantities ordered, fulfilled, received, and billed reconcile
Revenue-plan lines sum to the plan total


Deliverables: 
System: takes a database schema as input and produces a SQLite database ranging up to 2GB. It is in a single repo and documents all 3rd party or cloud services
Generated database for Stratum using the supplied schema


Create a Entity-Relationship Diagram of the tables to understand what they look like
Maybe indices is where we should start generating

Appendix

I started off my problem solving process by taking a quick glance at each table in the schema to best understand the problem: 

What’s the Stratum schema? 
Tables: accounting_books, accounting_periods, accounts, approval_requests, approval_steps, approval_workflows, audit_trail_entries, bank_accounts, bank_transfers, bill_lines, bill_payment_applications, bill_payments, bill_variances, billing_accounts, billing_schedule_milestones, billing_schedules, bills, budget_lines, budgets, cash_refund_lines, cash_refunds, cash_sale_lines, cash_sales, change_order_lines, change_orders, charges, classes, commit_plus_overages, company_preferences, compliance_controls, compliance_tests, consolidation_processes, consolidation_subsidiaries, consolidation_translations, credit_memo_applications, credit_memos, currencies, currency_exchange_notes, customers, dashboard_roles, dashboard_widgets, dashboards, deferred_revenue_reclassifications, departments, deposits, depreciation_schedules, elimination_entries, employees, estimate_lines, estimates, expense_report_lines, expense_reports, financial_reports, fixed_assets, forecast_lines, forecasts, fulfillment_lines, fulfillment_request_lines, fulfillment_requests, intercompany_transactions, invoice_lines, invoices, item_fulfillments, item_receipt_lines, item_receipts, item_subsidiaries, items, journal_entries, journal_entry_lines, kpi_widget, kpis, locations, payment_applications, payment_term_subsidiaries, payment terms, payments, prepaid_drawdowns, prepaid_usages, price_books, price_plan_tiers, price_plans, pricing_tiers, purchase_contract_lines, purchase_contracts, purchase_order_lines, purchase_orders, rating_runs, reclassification_journal_entries, reonciliation_lines, reconciliations, report_snapshots, request_for_quote_lines, requests_for_quote, requisition_lines, requisitions, return_authorization_lines, return_authorizations, revenue_arrangements, revenue_elements, revenue_forecast_lines, revenue_forecasts, revenue_plan_lines, revenue_plans, revenue_recognition_journals, revenue_rules, sales_order_billing_schedules, sales_order_lines, sales_orders, saved_searches, scenario_assumptions, scenarios, subscription_lines, subscription_plan_lins, subscription_plans, subscriptions, subsidiaries, tax_code_subsidiaries, tax_codes, tax_periods, usage_records, user_role_subsidiaries, user_roles, user_roles_mapping, user_subsidiaries, users, vendor_credit_applications, vendor_credits, vendor_prepayments, vendor_quote_lines, vendor_quotes, vendor_return_authorization_lines, vendor_return_authorizations, vendors
Indices: 
`idx_accounting_books_book_id` ON `accounting_books` (`book_id`)
`idx_accounting_books_subsidiary` ON `accounting_books` (`subsidiary_id`)
`idx_accounting_periods_fiscal_year` ON `accounting_periods` (`fiscal_year`)
`idx_accounting_periods_subsidiary` ON `accounting_periods` (`subsidiary_id`)
`idx_accounting_periods_status` ON `accounting_periods` (`status`)
…

Questions: 
Any particular scenario that we care about? 
Corporate accounting, close the book every month at the end of the month, train an agent on these tasks
Come up with a couple hypotheses for the workflows / scenarios we are targeting. 
Base example workflow: at the end of everymonth, you want an agent to close a book to make sure every number matches with each other
In real world, accountants / corporate finance person at a company will look at a revenue, costs / margins, expenses from employees, vender costs. They will make sure that the number that matches with the emails. PnL. We will focus on building the data. Downstream, we will have experts to look at the data. 
Are there tables that we care most about? 
Varied by domain. 
“Can you sanity check the data?” <- we want our data to sanity check the data
Sometimes we would drop the data to experts to just look at them
Sometimes we will come up with our own thesis
Should I design and run any tasks?
We will hand this SQLite and somewhere downstream someone will use an API to perturb and create the task. Frontier model will call the SQLite directly. 
What types of tasks should we support? What task types? Should these be decided beforehand?
What sort of parameters should I support in my system? 
The customer will tell us a Spec (huge bank)
Company, people
Simulate a small to medium SMB style, 1-2GB
Byte volume
To fill the byte volume, be creative
Tasks
What if the later people tell us that the world is too system
Maybe: 
A researcher might come up with an interesting reward function / different shape of task. Might include user simulation. 
Choose one of these directions: 
Adding one more application, such as Email, notion, ramp. How would you ensure consistency over all these apps
Files. Receipts, excel, source of truth was in excel last month, the task might be like, go through this month troubleshooting accounting error based on last month’s excel. Think about files. 
Can I have an API key? For cursor and openai
Reimburse it with Astra
Budget: 1000-2000
If the data is good enough, you can sell it for a lot of money
Limit R&D Cost
Pastebin
Deliverables: 
System: takes a database schema as input and produces a SQLite database ranging up to 2GB. It is in a single repo and documents all 3rd party or cloud services
Generated database for Stratum using the supplied schema
