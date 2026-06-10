import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class HelloGoblin {
    private static final Pattern MESSAGE_PATTERN = Pattern.compile("\"target\"\\s*:\\s*\"([^\"]*)\"");

    private static String readJsonObject(String envName) throws IOException {
        String path = System.getenv(envName);
        if (path == null || path.isBlank()) {
            throw new IllegalArgumentException(envName + " is required");
        }
        String raw = Files.readString(Path.of(path), StandardCharsets.UTF_8).trim();
        if (!raw.startsWith("{") || !raw.endsWith("}")) {
            throw new IllegalArgumentException(envName + " must point to a JSON object");
        }
        return raw;
    }

    private static String envOr(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }

    private static String jsonEscape(String value) {
        return value
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    private static String targetFrom(String inputJson) {
        Matcher matcher = MESSAGE_PATTERN.matcher(inputJson);
        return matcher.find() ? matcher.group(1) : "World";
    }

    public static void main(String[] args) throws IOException {
        String inputJson = readJsonObject("GOBLIN_INPUT_PATH");
        readJsonObject("GOBLIN_CONTEXT_PATH");

        String runId = envOr("GOBLIN_RUN_ID", "unknown-run");
        String kind = envOr("GOBLIN_KIND", "example.hello-java");
        String target = targetFrom(inputJson);

        System.out.printf("Java goblin says hello to %s. The crown appreciates bytecode.%n", target);

        String result = """
                {
                  "status": "success",
                  "data": {
                    "message": "Hello World",
                    "language": "java",
                    "runtime": "Java 21",
                    "kind": "%s",
                    "run_id": "%s",
                    "target": "%s",
                    "input_raw": "%s",
                    "quote": "A class file can still carry a royal seal."
                  },
                  "artifacts": [],
                  "metrics": {},
                  "handoff": [],
                  "error": null
                }
                """.formatted(jsonEscape(kind), jsonEscape(runId), jsonEscape(target), jsonEscape(inputJson));

        Files.writeString(Path.of(System.getenv("GOBLIN_RESULT_PATH")), result, StandardCharsets.UTF_8);
    }
}
