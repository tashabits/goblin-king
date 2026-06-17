HELM_NAMESPACE = default
HELM_RELEASE = goblin-king
HELM_CHART = charts/goblin-king
HELM_TIMEOUT = 5m

JUPYTERHUB_RELEASE = jupyterhub
JUPYTERHUB_CHART = jupyterhub/jupyterhub
JUPYTERHUB_VALUES = examples/jupyterhub-goblin-king/zero-to-jupyterhub.values.yaml
JUPYTERHUB_SERVICE_TOKEN_SECRET = goblin-king-jupyterhub-auth
JUPYTERHUB_SERVICE_TOKEN_KEY = service-token
JUPYTERHUB_SERVICE_TOKEN = local-goblin-king-hub-token

HELM_ARGS = -f examples/jupyterhub-goblin-king/goblin-king.values.yaml
HELM_JUPYTERHUB_ARGS =
