FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build

WORKDIR /src
COPY HelloDotnet.csproj /src/HelloDotnet.csproj
RUN dotnet restore /src/HelloDotnet.csproj
COPY Program.cs /src/Program.cs
RUN dotnet publish /src/HelloDotnet.csproj -c Release -o /out --no-restore

FROM mcr.microsoft.com/dotnet/runtime:8.0

WORKDIR /worker
COPY --from=build /out /worker
ENTRYPOINT ["dotnet", "/worker/HelloDotnet.dll"]
