FROM alpine:3.20

RUN apk add --no-cache jq

WORKDIR /worker
COPY worker.sh /worker/worker.sh
RUN chmod +x /worker/worker.sh

ENTRYPOINT ["/worker/worker.sh"]
