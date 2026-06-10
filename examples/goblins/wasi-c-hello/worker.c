#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_all(const char *path) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        fprintf(stderr, "could not open %s\n", path);
        exit(1);
    }
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    rewind(file);
    char *buffer = calloc((size_t)size + 1, sizeof(char));
    if (buffer == NULL) {
        fprintf(stderr, "could not allocate input buffer\n");
        exit(1);
    }
    fread(buffer, 1, (size_t)size, file);
    fclose(file);
    return buffer;
}

static void require_object(const char *raw, const char *label) {
    size_t length = strlen(raw);
    if (length < 2 || raw[0] != '{' || raw[length - 1] != '}') {
        fprintf(stderr, "%s must be a JSON object\n", label);
        exit(1);
    }
}

static void target_from_input(const char *raw, char *target, size_t target_size) {
    const char *field = strstr(raw, "\"target\"");
    if (field == NULL) {
        snprintf(target, target_size, "World");
        return;
    }
    const char *colon = strchr(field, ':');
    const char *first_quote = colon == NULL ? NULL : strchr(colon, '"');
    const char *second_quote = first_quote == NULL ? NULL : strchr(first_quote + 1, '"');
    if (first_quote == NULL || second_quote == NULL || second_quote <= first_quote + 1) {
        snprintf(target, target_size, "World");
        return;
    }
    size_t length = (size_t)(second_quote - first_quote - 1);
    if (length >= target_size) {
        length = target_size - 1;
    }
    memcpy(target, first_quote + 1, length);
    target[length] = '\0';
}

static const char *env_or(const char *name, const char *fallback) {
    const char *value = getenv(name);
    return value == NULL || strlen(value) == 0 ? fallback : value;
}

int main(void) {
    const char *input_path = getenv("GOBLIN_INPUT_PATH");
    const char *context_path = getenv("GOBLIN_CONTEXT_PATH");
    const char *result_path = getenv("GOBLIN_RESULT_PATH");
    if (input_path == NULL || context_path == NULL || result_path == NULL) {
        fprintf(stderr, "contract paths are required\n");
        return 1;
    }

    char *input = read_all(input_path);
    char *context = read_all(context_path);
    require_object(input, "input");
    require_object(context, "context");
    free(context);

    char target[128];
    target_from_input(input, target, sizeof(target));
    printf("C WASI goblin says hello to %s. The crown likes predictable syscalls.\n", target);

    FILE *result = fopen(result_path, "wb");
    if (result == NULL) {
        fprintf(stderr, "could not write result\n");
        free(input);
        return 1;
    }
    fprintf(result,
            "{\n"
            "  \"status\": \"success\",\n"
            "  \"data\": {\n"
            "    \"message\": \"Hello World from C WASI\",\n"
            "    \"language\": \"c-wasi\",\n"
            "    \"runtime\": \"C wasm32-wasi on Wasmtime\",\n"
            "    \"kind\": \"%s\",\n"
            "    \"run_id\": \"%s\",\n"
            "    \"target\": \"%s\",\n"
            "    \"quote\": \"A small C module knocks before entering the throne room.\"\n"
            "  },\n"
            "  \"artifacts\": [],\n"
            "  \"metrics\": {},\n"
            "  \"handoff\": [],\n"
            "  \"error\": null\n"
            "}\n",
            env_or("GOBLIN_KIND", "example.wasi-c-hello"),
            env_or("GOBLIN_RUN_ID", "unknown-run"),
            target);
    fclose(result);
    free(input);
    return 0;
}
