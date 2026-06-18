FROM quay.io/jupyter/base-notebook:python-3.12

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY . /tmp/goblin-king
RUN fix-permissions /tmp/goblin-king

USER ${NB_UID}
RUN python -m pip install --no-cache-dir -e /tmp/goblin-king \
    && python -c "import goblin_king.jupyter_directory" \
    && cd /tmp/goblin-king/jupyterlab-goblin-directory \
    && npm install --omit=optional \
    && npm run build \
    && npm run build:labextension \
    && mkdir -p /opt/conda/share/jupyter/labextensions/goblin-king-jupyterlab \
    && cp -R /tmp/goblin-king/src/goblin_king/labextension/. \
      /opt/conda/share/jupyter/labextensions/goblin-king-jupyterlab/

USER root
RUN mkdir -p /opt/conda/etc/jupyter/jupyter_server_config.d \
    && printf '%s\n' '{"ServerApp":{"jpserver_extensions":{"goblin_king.jupyter_directory":true}}}' \
      > /opt/conda/etc/jupyter/jupyter_server_config.d/goblin_king_jupyter_directory.json \
    && fix-permissions /opt/conda/etc/jupyter

USER ${NB_UID}
