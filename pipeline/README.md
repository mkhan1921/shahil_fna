# Advisor Call Intelligence

Python pipeline that turns one recorded adviser–client call into a reviewed, probabilistic update
to the client's Shahil FNA document (`FnaDocument` v2) and a Monte Carlo forecast.

Work in progress: see the implementation spec for the stage map. Run the tests with

```bash
cd pipeline
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest
```
