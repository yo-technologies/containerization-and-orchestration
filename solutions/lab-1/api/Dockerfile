FROM golang:1.26.8-alpine3.23 AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /api .

FROM scratch
COPY --from=build /api /api
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/api"]
CMD ["-listen", "0.0.0.0:8080"]
