# Add Electrical Parameters

A pyRevit tool for adding electrical shared parameters and connectors
to Revit families in batch.

## Requirements
- Revit 2022+
- pyRevit 4.8+
- A configured shared parameter file containing the parameters listed
  in `AUTO_ADD_PARAMS` and `SCHEDULE_PARAMETERS`.

## Usage
1. Open a project or family that references the target families.
2. Run the script from the pyRevit toolbar.
3. Select a family, choose connector type and options, click Add Shared Parameters.

## Versions
See [CHANGELOG.md](CHANGELOG.md) and the [Releases](../../releases) page.