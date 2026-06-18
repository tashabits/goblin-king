FROM quay.io/jupyter/base-notebook:python-3.12

USER root
COPY . /tmp/goblin-king
RUN fix-permissions /tmp/goblin-king

USER ${NB_UID}
RUN python -m pip install --no-cache-dir -e /tmp/goblin-king \
    && jupyter server extension enable goblin_king.jupyter_directory --sys-prefix \
    && cd /tmp/goblin-king/jupyterlab-goblin-directory \
    && npm install --omit=optional \
    && npm run build \
    && jupyter labextension develop . --overwrite --sys-prefix
