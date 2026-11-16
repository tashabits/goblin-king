ARG CONTROL_PLANE_IMAGE=goblin-king:pipeline-dev
FROM ${CONTROL_PLANE_IMAGE}
USER root
RUN python -c "import urllib.request,hashlib,tarfile,io,pathlib; data=urllib.request.urlopen('https://get.helm.sh/helm-v4.1.3-linux-amd64.tar.gz').read(); assert hashlib.sha256(data).hexdigest() == '02ce9722d541238f81459938b84cf47df2fdf1187493b4bfb2346754d82a4700'; t=tarfile.open(fileobj=io.BytesIO(data)); p=pathlib.Path('/usr/local/bin/helm'); p.write_bytes(t.extractfile('linux-amd64/helm').read()); p.chmod(0o755)"
COPY charts/pipeline-http-service /opt/pipeline-http-service
ENTRYPOINT ["python", "-m", "goblin_king.pipeline_deploy_worker"]
