ARG CONTROL_PLANE_IMAGE=goblin-king:pipeline-dev
FROM ${CONTROL_PLANE_IMAGE}
ENTRYPOINT ["python", "-m", "goblin_king.pipeline_build_worker"]
