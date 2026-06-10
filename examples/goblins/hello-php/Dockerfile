FROM php:8.3-cli-alpine

WORKDIR /worker
COPY worker.php /worker/worker.php

ENTRYPOINT ["php", "/worker/worker.php"]
