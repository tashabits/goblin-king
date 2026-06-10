require "json"

def read_object(path, label)
  value = JSON.parse(File.read(path))
  raise "#{label} must be a JSON object" unless value.is_a?(Hash)

  value
end

input = read_object(ENV.fetch("GOBLIN_INPUT_PATH"), "input")
read_object(ENV.fetch("GOBLIN_CONTEXT_PATH"), "context")

run_id = ENV.fetch("GOBLIN_RUN_ID", "unknown-run")
kind = ENV.fetch("GOBLIN_KIND", "example.hello-ruby")
target = input.fetch("target", "World")

puts "Ruby goblin says hello to #{target}. The crown likes expressive syntax."

result = {
  status: "success",
  data: {
    message: "Hello World",
    language: "ruby",
    runtime: "Ruby 3.3",
    kind: kind,
    run_id: run_id,
    target: target,
    input: input,
    quote: "A cheerful block can carry a royal decree."
  },
  artifacts: [],
  metrics: {},
  handoff: [],
  error: nil
}

File.write(ENV.fetch("GOBLIN_RESULT_PATH"), JSON.pretty_generate(result))
