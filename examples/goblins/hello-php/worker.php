<?php

function read_object(string $path, string $label): array {
    $value = json_decode(file_get_contents($path), true, flags: JSON_THROW_ON_ERROR);
    if (!is_array($value) || array_is_list($value)) {
        throw new RuntimeException("$label must be a JSON object");
    }
    return $value;
}

$input = read_object(getenv("GOBLIN_INPUT_PATH"), "input");
read_object(getenv("GOBLIN_CONTEXT_PATH"), "context");

$runId = getenv("GOBLIN_RUN_ID") ?: "unknown-run";
$kind = getenv("GOBLIN_KIND") ?: "example.hello-php";
$target = $input["target"] ?? "World";

fwrite(STDOUT, "PHP goblin says hello to $target. The crown appreciates a practical script.\n");

$result = [
    "status" => "success",
    "data" => [
        "message" => "Hello World",
        "language" => "php",
        "runtime" => "PHP 8.3 CLI",
        "kind" => $kind,
        "run_id" => $runId,
        "target" => $target,
        "input" => $input,
        "quote" => "A humble array may still carry royal meaning.",
    ],
    "artifacts" => [],
    "metrics" => new stdClass(),
    "handoff" => [],
    "error" => null,
];

file_put_contents(getenv("GOBLIN_RESULT_PATH"), json_encode($result, JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR));
