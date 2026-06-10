FROM golang:1.22-alpine AS build

WORKDIR /src
COPY main.go /src/main.go
RUN go build -o /out/behavior-go-transform /src/main.go

FROM alpine:3.20

COPY --from=build /out/behavior-go-transform /worker/behavior-go-transform
ENTRYPOINT ["/worker/behavior-go-transform"]
