using System.Text.Json;
using System.Text.Json.Nodes;

static JsonObject ReadObject(string envName)
{
    var path = Environment.GetEnvironmentVariable(envName)
        ?? throw new InvalidOperationException($"{envName} is required");
    var raw = File.ReadAllText(path);
    var node = JsonNode.Parse(raw) as JsonObject
        ?? throw new InvalidOperationException($"{envName} must point to a JSON object");
    return node;
}

var input = ReadObject("GOBLIN_INPUT_PATH");
_ = ReadObject("GOBLIN_CONTEXT_PATH");

var runId = Environment.GetEnvironmentVariable("GOBLIN_RUN_ID") ?? "unknown-run";
var kind = Environment.GetEnvironmentVariable("GOBLIN_KIND") ?? "example.hello-dotnet";
var target = input["target"]?.GetValue<string>() ?? "World";

Console.WriteLine($".NET goblin says hello to {target}. The crown respects managed runtime manners.");

var result = new
{
    status = "success",
    data = new
    {
        message = "Hello World",
        language = "dotnet",
        runtime = ".NET 8",
        kind,
        run_id = runId,
        target,
        input,
        quote = "A managed goblin tidies the throne room before leaving.",
    },
    artifacts = Array.Empty<object>(),
    metrics = new { },
    handoff = Array.Empty<object>(),
    error = (object?)null,
};

var resultPath = Environment.GetEnvironmentVariable("GOBLIN_RESULT_PATH")
    ?? throw new InvalidOperationException("GOBLIN_RESULT_PATH is required");
File.WriteAllText(resultPath, JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
