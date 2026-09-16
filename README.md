# Bonsai 🪴
Bonsai simulates realistic, complex financial systems for synthetic companies. Like how a bonsai tree recreates the structure and detail of a full-sized tree, Bonsai generates coherent transactional records and digital communications that reflect real business operations.

## Quick start

To get started, add an OPENAI_API_KEY to your .env file. 

To simulate the tables, run: 

```
uv run main.py --output_path results
```

Then, you can run the following code to generate emails:
```
uv run generate_emails.py results
```

Finally, to run the viewer, run: 
```
uv run app.py --output_filepath results
```

## Code for replicate the demo

To replicate the demo, run the following code with the following parameters:
```
uv run main.py \
  --output_path results/northstar \
  --end_date 2025-12-31 \
  --model gpt-5.6-sol \
  --num_customers 10463 \
  --num_departments 8 \
  --num_employees 253 \
  --num_sales_orders 110532 \
  --num_subscriptions 12042 \
  --random_seed 42 \
  --scenario_duration_days 1826 \
  --start_date 2021-01-01 \

uv run generate_emails.py results/northstar \
  --model terra \
  --thread-limit 1000 \
  --concurrency 25 \
  --generate-images \
  --image-model gpt-image-2.5-flare \
  --image-quality low

uv run app.py --output_filepath results/northstar
```

## Repo contents

### main.py 
This contains the pipeline script for generating a world and tables using the workflow methodology.

### generate_emails.py
This contains the script for generating plausible emails from the sqlite database as ground truth.

### app.py
Inside this repo, we also have an application you can use to display and look at a generated world. 
Simply specify a result folder and run `uv run app.py --output_filepath results`.
