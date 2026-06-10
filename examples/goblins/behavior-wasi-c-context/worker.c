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
    fread(buffer, 1, (size_t)size, file);
    fclose(file);
    return buffer;
}

static const char *env_or(const char *name, const char *fallback) {
    const char *value = getenv(name);
    return value == NULL || strlen(value) == 0 ? fallback : value;
}

int main(void) {
    char *input = read_all(getenv("GOBLIN_INPUT_PATH"));
    char *context = read_all(getenv("GOBLIN_CONTEXT_PATH"));
    printf("WASI C context goblin read %zu input bytes and %zu context bytes.\n", strlen(input), strlen(context));
    FILE *result = fopen(getenv("GOBLIN_RESULT_PATH"), "wb");
    fprintf(result,
            "{\n"
            "  \"status\": \"success\",\n"
            "  \"data\": {\n"
            "    \"message\": \"WASI context read complete\",\n"
            "    \"language\": \"c-wasi\",\n"
            "    \"run_id\": \"%s\",\n"
            "    \"input_bytes\": %zu,\n"
            "    \"context_bytes\": %zu\n"
            "  },\n"
            "  \"artifacts\": [],\n"
            "  \"metrics\": {\"input_bytes\": %zu, \"context_bytes\": %zu},\n"
            "  \"handoff\": [],\n"
            "  \"error\": null\n"
            "}\n",
            env_or("GOBLIN_RUN_ID", "unknown-run"),
            strlen(input),
            strlen(context),
            strlen(input),
            strlen(context));
    fclose(result);
    free(input);
    free(context);
    return 0;
}
