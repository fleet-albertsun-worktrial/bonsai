from dataclasses import dataclass
from datetime import date

@dataclass
class WorldParameters:
    """Configure the scope and complexity of a synthetic business world.

    Attributes:
        world_description: Narrative describing the business and its financial
            activity.
        start_date: First calendar date included in the simulation.
        end_date: Last calendar date included in the simulation.
        num_departments: Number of organizational departments to generate.
        num_employees: Number of employees distributed across the departments.
        num_customers: Number of customers participating in commercial activity.
        num_subscriptions: Number of recurring subscriptions to generate.
        num_sales_orders: Number of sales orders created during the scenario.
        scenario_duration_days: Number of simulated calendar days.
        cross_domain_link_rate: Fraction of eligible records linked to workflows
            in other domains.
        exception_rate: Fraction of eligible workflows assigned an exceptional
            outcome.
    """

    world_description: str = (
        "Northstar Office Systems is a 100-person company that sells office "
        "equipment and subscription-based maintenance services to 500 customers. "
        "Each month, its finance team reconciles sales, subscriptions, vendor "
        "bills, employee expenses, payments, and occasional accounting discrepancies."
    )
    start_date: date = date(2025, 1, 1)
    end_date: date = date(2025, 12, 31)
    num_departments: int = 5
    num_employees: int = 100
    num_customers: int = 500
    num_subscriptions: int = 250
    num_sales_orders: int = 1_000
    scenario_duration_days: int = 365
    cross_domain_link_rate: float = 0.6
    exception_rate: float = 0.05

def _extract_knowledge_base()
    # convert the narrative into people, companies, entities, dates, currencies, quantities
    # prices, policies, relationships with stable IDs. 
    # this will enable us to run deterministic validators afterwards

def _generate_records()
    # Generate the following records into the intermediate_states.json
    # Organization, user, accounting setup
    # Catalog, customers, and vendors
    # Orders, subscriptions, and contracts
    # Fulfillments, receipts, and usage
    # Invoices, bills, payments, and charges
    # Revenue recognition and journal entries
    # Reports and compliance records

    # use GPT-5.6-Sol with long context to output this successively. you have my keys in .env for OpenAI.
    # for things that can be simulated using faker (people's names, etc) and other python packages, use that first so that we can scale up quickly
    # create folder with different prompt_templates

def _create_verifiers()
    # create programmatic verifiers we can use to check that the states of the sqlite file align along the two. 

def _compile()
    # compile all the intermediate states into a sqlite file. 


def generate(
    output_path: str = "results/",
    world_parameters=world_parameters
):

    _extract_knowledge_base(
        
    )

    _generate_records(

    )

    _compile(

    )

    _create_verifiers(

    )

    _run_verifiers(

    )

    # output this, the file should containing: 
    # output.sqlite
    # statistics.json: how long each process took
    # pipeline.log: append-only log of any errors that we ran into
    # intermediate_states/: a json file for each so that our process is idempotent
