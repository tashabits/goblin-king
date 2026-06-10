package main

import (
	"encoding/json"
	"fmt"
	"os"
)

func readObject(path string, label string) (map[string]any, error) {
	handle, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer handle.Close()

	var value map[string]any
	if err := json.NewDecoder(handle).Decode(&value); err != nil {
		return nil, err
	}
	if value == nil {
		return nil, fmt.Errorf("%s must be a JSON object", label)
	}
	return value, nil
}

func env(name string, fallback string) string {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	return value
}

func main() {
	input, err := readObject(os.Getenv("GOBLIN_INPUT_PATH"), "input")
	if err != nil {
		panic(err)
	}
	if _, err := readObject(os.Getenv("GOBLIN_CONTEXT_PATH"), "context"); err != nil {
		panic(err)
	}

	runID := env("GOBLIN_RUN_ID", "unknown-run")
	kind := env("GOBLIN_KIND", "example.hello-go")
	target, ok := input["target"].(string)
	if !ok || target == "" {
		target = "World"
	}

	fmt.Printf("Go goblin says hello to %s. The crown likes small binaries.\n", target)

	result := map[string]any{
		"status": "success",
		"data": map[string]any{
			"message":  "Hello World",
			"language": "go",
			"runtime":  "Go 1.22",
			"kind":     kind,
			"run_id":   runID,
			"target":   target,
			"input":    input,
			"quote":    "A swift goblin compiles before the trumpet sounds.",
		},
		"artifacts": []any{},
		"metrics":   map[string]any{},
		"handoff":   []any{},
		"error":     nil,
	}

	payload, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		panic(err)
	}
	if err := os.WriteFile(os.Getenv("GOBLIN_RESULT_PATH"), payload, 0o644); err != nil {
		panic(err)
	}
}
