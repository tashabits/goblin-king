FROM ruby:3.3-alpine

WORKDIR /worker
COPY worker.rb /worker/worker.rb

ENTRYPOINT ["ruby", "/worker/worker.rb"]
