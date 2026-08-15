# Sandbox execution image: python:3.11-slim plus the data analysis
# libraries the Data Analyst agent (and any future code-execution agent)
# needs pre-installed. Installing at build time here — not at container
# run time — matters because the sandbox container runs with
# network_disabled=True; there is no network access at execution time to
# pip install anything, by design.
FROM python:3.11-slim

RUN pip install --no-cache-dir \
    pandas \
    numpy \
    matplotlib \
    seaborn

# No CMD/ENTRYPOINT needed — docker_executor.py always specifies the
# command explicitly (`python /sandbox/script.py`) per run.