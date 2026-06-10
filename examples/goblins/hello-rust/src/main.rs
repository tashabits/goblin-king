use serde_json::{json, Value};
use std::{env, fs, path::PathBuf};

fn read_object(name: &str) -> Value {
    let path = env::var(name).expect("contract path environment variable is required");
    let raw = fs::read_to_string(path).expect("contract JSON file must be readable");
    let value: Value = serde_json::from_str(&raw).expect("contract file must be valid JSON");
    if !value.is_object() {
        panic!("{name} must point to a JSON object");
    }
    value
}

fn env_or(name: &str, fallback: &str) -> String {
    env::var(name).unwrap_or_else(|_| fallback.to_string())
}

fn main() {
    let input = read_object("GOBLIN_INPUT_PATH");
    let _context = read_object("GOBLIN_CONTEXT_PATH");
    let run_id = env_or("GOBLIN_RUN_ID", "unknown-run");
    let kind = env_or("GOBLIN_KIND", "example.hello-rust");
    let target = input
        .get("target")
        .and_then(Value::as_str)
        .unwrap_or("World");

    println!("Rust goblin says hello to {target}. The borrow checker guards the crown.");

    let result = json!({
        "status": "success",
        "data": {
            "message": "Hello World",
            "language": "rust",
            "runtime": "Rust 2021",
            "kind": kind,
            "run_id": run_id,
            "target": target,
            "input": input,
            "quote": "The throne room has no data races today."
        },
        "artifacts": [],
        "metrics": {},
        "handoff": [],
        "error": null
    });

    let result_path = PathBuf::from(env::var("GOBLIN_RESULT_PATH").expect("result path is required"));
    fs::write(
        result_path,
        serde_json::to_string_pretty(&result).expect("result must serialize"),
    )
    .expect("result file must be writable");
}
