FROM golang:1.22-alpine AS build

WORKDIR /src
COPY main.go /src/main.go
RUN go build -o /out/hello-goblin /src/main.go

FROM alpine:3.20

COPY --from=build /out/hello-goblin /worker/hello-goblin
ENTRYPOINT ["/worker/hello-goblin"]
