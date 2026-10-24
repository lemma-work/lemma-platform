# A bare machine for the connected-machine parity suite: Python 3.14, uv and the
# Lemma CLI from this tree, nothing of the workspace image. `lemma machine
# connect` has to build everything else itself, as it would on someone's VM.
# Built from the repository root (test_connected_machine_parity_e2e.py).
FROM python:3.14-slim
RUN pip install --no-cache-dir uv
COPY lemma-python /src/lemma-python
COPY lemma-pod-bundle /src/lemma-pod-bundle
COPY lemma-skills /src/lemma-skills
COPY lemma-cli /src/lemma-cli
RUN uv pip install --system /src/lemma-python /src/lemma-pod-bundle /src/lemma-cli
