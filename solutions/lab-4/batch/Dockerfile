FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/batch .

FROM scratch
COPY --from=build /out/batch /batch
USER 65532:65532
ENTRYPOINT ["/batch"]
