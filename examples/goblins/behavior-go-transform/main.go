package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

func readMap(path string) map[string]any {
	handle, err := os.Open(path)
	if err != nil {
		panic(err)
	}
	defer handle.Close()
	var value map[string]any
	if err := json.NewDecoder(handle).Decode(&value); err != nil {
		panic(err)
	}
	return value
}

func main() {
	input := readMap(os.Getenv("GOBLIN_INPUT_PATH"))
	context := readMap(os.Getenv("GOBLIN_CONTEXT_PATH"))
	rawItems, _ := input["items"].([]any)
	transformed := make([]string, 0, len(rawItems))
	for _, item := range rawItems {
		transformed = append(transformed, strings.ToUpper(fmt.Sprint(item)))
	}
	fmt.Printf("Go transform goblin processed %d items. The crown likes tidy casing.\n", len(transformed))
	result := map[string]any{
		"status": "success",
		"data": map[string]any{
			"message":     "Transform complete",
			"language":    "go",
			"run_id":      context["run_id"],
			"transformed": transformed,
		},
		"artifacts": []any{},
		"metrics": map[string]any{
			"input_count":  len(rawItems),
			"output_count": len(transformed),
		},
		"handoff": []any{},
		"error":   nil,
	}
	payload, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		panic(err)
	}
	if err := os.WriteFile(os.Getenv("GOBLIN_RESULT_PATH"), payload, 0o644); err != nil {
		panic(err)
	}
}
