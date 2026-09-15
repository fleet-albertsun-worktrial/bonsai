Seed Data Generation

Overview
Fleet's environments are used to create lots of interesting tasks, and those tasks often depend on data that is globally consistent, complex, and realistic. We want to create a reusable system that can automatically generate data in this shape.

Concretely, we want to populate environments with high-fidelity synthetic data. For example, we have an accounting environment named stratum that needs to be populated with 1-2GB of realistic data. The supplied database schema defines the structure your generated seed must preserve.

Your goal is to build a reusable system that takes a database schema as input and generates a SQLite database with realistic, globally consistent seed data that preserves that schema.

Data and Env Specifications
Here's a schema dump from a low-fidelity version of Stratum, and you can use it as an input reference to the system: theseus-stratum-deployed-bundle/. It includes the SQLite database schema (schema.sql) and supporting API, tool, and workflow specifications (envspec/).

Your generated seed should follow the database schema and represent plausible business activity, with diverse records, realistic transaction histories, and consistent relationships across the database. The supporting specifications provide context for how the environment uses the data.

These files may be outdated or contain inconsistencies. Please feel free to ask if anything is unclear or contradictory.

Deliverables

A system that takes a database schema as input and produces a SQLite database ranging up to 2GB.
A generated database for Stratum using the supplied schema.
The full runnable system in a single repo, documenting any third party or cloud services used


Follow Ups
Once you feel good about the core deliverables, if you would like a challenge, feel free to generalize the system further:

Support the generation of seed data across multiple environments, where the data is all consistent within a single 'world' as if a single company were using all of these applications.
Support the generation of filesystem data that represents the filesystem of a single employee and the company.


Demo
At the end of the work trial, we’d like to schedule a demo session. We'd like for you to walk us through your deliverables as if you were presenting to a customer considering purchasing the data, and explain how the system works as if you were introducing a teammate to the reusable system you just built. No polished slides or formal presentation are needed. We’re more interested in your approach, the tradeoffs you considered, and why you made the decisions you did.