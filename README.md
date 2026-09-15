# Bonsai 🪴
Bonsai is a system to simulate realistic, complex financial systems for a company.

## Quick start
To start, you can run `uv run main.py`.

Or you can use our Python interface below:

```
from bonsai import generate, WorldParameters
world_parameters = WorldParameters(...)

generate(
    output_path=”results/”, 
    world_parameters=world_parameters
)
# Outputs a folder in results/ containing output.sqlite, statistics.json, and pipeline.log.
```

## Repo contents

### main.py 
This contains the pipeline script for generating a world. 

### app.py
Inside this repo, we also have an application you can use to display and look at a generated world. 
Simply specify a result folder and run `uv run app.py --output_filepath results`.