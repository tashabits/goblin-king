FROM eclipse-temurin:21-jdk AS build

WORKDIR /src
COPY HelloGoblin.java /src/HelloGoblin.java
RUN javac /src/HelloGoblin.java

FROM eclipse-temurin:21-jre

WORKDIR /worker
COPY --from=build /src/HelloGoblin.class /worker/HelloGoblin.class
ENTRYPOINT ["java", "-cp", "/worker", "HelloGoblin"]
